"""
pipeline/transforms/sessions.py
ETL: staging.stg_charging_sessions → core.charging_sessions

Enriquece sessões com:
  - station_id (lookup via station_source_id)
  - features temporais pré-computadas (hour_of_day, day_of_week, etc.)
  - duration_minutes e avg_power_kw calculados
"""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.database import get_session
from config.settings import get_settings
from models.staging import StgChargingSession
from models.core import ChargingSession, ChargingStation
from utils.logger import get_logger

settings = get_settings()
logger = get_logger("transforms.sessions")
CORE = settings.db_schema_core


def _compute_temporal_features(dt: datetime) -> dict:
    """Extrai features temporais de um datetime."""
    return {
        "hour_of_day": dt.hour,
        "day_of_week": dt.weekday(),  # 0=Mon, 6=Sun
        "is_weekend": dt.weekday() >= 5,
        "month_of_year": dt.month,
    }


def _build_station_lookup(session: Session, source_ids: list[str]) -> dict[str, int]:
    """
    Mapeia station_source_id → core.charging_stations.id
    para um lote de IDs.
    """
    if not source_ids:
        return {}
    rows = session.execute(
        select(ChargingStation.external_id, ChargingStation.id)
        .where(ChargingStation.external_id.in_(source_ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def transform_sessions(batch_size: int = 1000) -> dict[str, int]:
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    with get_session() as session:
        pending = (
            session.execute(
                select(StgChargingSession)
                .where(StgChargingSession.is_processed == False)
                .where(StgChargingSession.session_start.is_not(None))
                .order_by(StgChargingSession.session_start)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        if not pending:
            logger.info("Nenhuma sessão pendente no staging.")
            return stats

        logger.info("Transformando %d sessões...", len(pending))

        # Pré-carrega mapeamento de estações
        source_ids = list({
            s.station_source_id for s in pending if s.station_source_id
        })
        station_map = _build_station_lookup(session, source_ids)
        logger.debug(
            "Lookup de estações: %d source_ids → %d encontrados",
            len(source_ids), len(station_map),
        )

        rows_to_insert = []
        processed_ids = []

        for stg in pending:
            try:
                started_at = stg.session_start
                ended_at = stg.session_end

                # Converte para UTC se naive
                if started_at and started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                if ended_at and ended_at.tzinfo is None:
                    ended_at = ended_at.replace(tzinfo=timezone.utc)

                station_id = station_map.get(stg.station_source_id or "")
                if not station_id:
                    stats["skipped"] += 1
                    logger.debug(
                        "Sessão stg_id=%d sem station_id mapeado — skipped",
                        stg.id,
                    )
                    # Não marca como processado: tenta de novo após ETL de estações
                    continue

                duration_minutes = None
                if started_at and ended_at:
                    duration_minutes = int(
                        (ended_at - started_at).total_seconds() / 60
                    )

                avg_power_kw = None
                if stg.energy_kwh and duration_minutes and duration_minutes > 0:
                    avg_power_kw = round(
                        stg.energy_kwh / (duration_minutes / 60), 4
                    )

                temporal = _compute_temporal_features(started_at)

                rows_to_insert.append({
                    "station_id": station_id,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration_minutes": duration_minutes,
                    "energy_kwh": stg.energy_kwh,
                    "peak_power_kw": stg.peak_power_kw,
                    "avg_power_kw": avg_power_kw,
                    **temporal,
                })
                processed_ids.append(stg.id)

            except Exception as exc:
                logger.warning(
                    "Erro ao transformar sessão stg_id=%d: %s", stg.id, exc
                )
                stats["errors"] += 1

        if rows_to_insert:
            stmt = pg_insert(ChargingSession).values(rows_to_insert)
            # Sessões: se já existe (station_id + started_at), atualiza métricas
            stmt = stmt.on_conflict_do_nothing()
            result = session.execute(stmt)
            stats["inserted"] = result.rowcount

        if processed_ids:
            session.execute(
                update(StgChargingSession)
                .where(StgChargingSession.id.in_(processed_ids))
                .values(is_processed=True)
            )

        logger.info(
            "Sessões: inserted=%d skipped=%d errors=%d",
            stats["inserted"], stats["skipped"], stats["errors"],
        )

    return stats
