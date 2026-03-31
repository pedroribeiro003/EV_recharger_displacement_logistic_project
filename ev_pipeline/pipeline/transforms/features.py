"""
pipeline/transforms/features.py
ETL: core → analytics.station_hourly_demand

Computa features horárias por estação com lag features corretas
(sem data leakage: cada bucket usa apenas dados anteriores a ele).

Executar após transform_sessions().
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.database import get_session
from config.settings import get_settings
from models.core import ChargingSession, ChargingStation
from models.analytics import StationHourlyDemand
from utils.logger import get_logger

settings = get_settings()
logger = get_logger("transforms.features")
CORE = settings.db_schema_core
ANA = settings.db_schema_analytics


def _compute_lag(
    session: Session,
    station_id: int,
    hour_bucket: datetime,
    lag_hours: int,
) -> float | None:
    """Busca o total_kwh do bucket exatamente `lag_hours` atrás."""
    target = hour_bucket - timedelta(hours=lag_hours)
    row = session.execute(
        select(StationHourlyDemand.total_kwh)
        .where(
            StationHourlyDemand.station_id == station_id,
            StationHourlyDemand.hour_bucket == target,
        )
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _compute_rolling_avg(
    session: Session,
    station_id: int,
    before: datetime,
    days: int,
) -> tuple[float | None, float | None]:
    """
    Média e desvio padrão do total_kwh nos últimos `days` dias
    com hour_bucket < `before` (garante ausência de leakage).
    """
    cutoff = before - timedelta(days=days)
    rows = session.execute(
        select(
            func.avg(StationHourlyDemand.total_kwh),
            func.stddev(StationHourlyDemand.total_kwh),
        )
        .where(
            StationHourlyDemand.station_id == station_id,
            StationHourlyDemand.hour_bucket >= cutoff,
            StationHourlyDemand.hour_bucket < before,
            StationHourlyDemand.total_kwh.is_not(None),
        )
    ).fetchone()
    if rows:
        avg = float(rows[0]) if rows[0] is not None else None
        std = float(rows[1]) if rows[1] is not None else None
        return avg, std
    return None, None


def compute_hourly_demand(
    start: datetime | None = None,
    end: datetime | None = None,
    station_ids: list[int] | None = None,
) -> dict[str, int]:
    """
    Agrega sessões em buckets horários e calcula lag features.

    Args:
      start        → início do intervalo a processar (default: últimas 48h)
      end          → fim do intervalo (default: agora)
      station_ids  → lista de estações específicas (default: todas)
    """
    now = datetime.now(timezone.utc)
    end = end or now
    start = start or (now - timedelta(hours=48))

    stats = {"rows_computed": 0, "errors": 0}

    with get_session() as session:
        # Consulta SQL para agregar sessões em buckets horários
        agg_query = text(f"""
            SELECT
                s.station_id,
                date_trunc('hour', s.started_at) AS hour_bucket,
                COUNT(*)                          AS session_count,
                SUM(s.energy_kwh)                 AS total_kwh,
                MAX(s.peak_power_kw)              AS peak_kw,
                AVG(s.avg_power_kw)               AS avg_kw,
                EXTRACT(HOUR FROM date_trunc('hour', s.started_at))::int  AS hour_of_day,
                EXTRACT(DOW  FROM date_trunc('hour', s.started_at))::int  AS day_of_week,
                EXTRACT(MONTH FROM date_trunc('hour', s.started_at))::int AS month_of_year,
                CASE WHEN EXTRACT(DOW FROM date_trunc('hour', s.started_at)) IN (0,6)
                     THEN TRUE ELSE FALSE END      AS is_weekend
            FROM {CORE}.charging_sessions s
            WHERE s.started_at >= :start
              AND s.started_at <  :end
              {"AND s.station_id = ANY(:station_ids)" if station_ids else ""}
            GROUP BY s.station_id, date_trunc('hour', s.started_at)
            ORDER BY s.station_id, hour_bucket
        """)

        params: dict = {"start": start, "end": end}
        if station_ids:
            params["station_ids"] = station_ids

        buckets = session.execute(agg_query, params).fetchall()
        logger.info(
            "Buckets horários a processar: %d (período: %s → %s)",
            len(buckets), start.isoformat(), end.isoformat(),
        )

        # Lookup de capacidade de conectores por estação
        station_connectors: dict[int, int] = {}

        rows_to_upsert = []
        computed_at = datetime.now(timezone.utc)

        for row in buckets:
            try:
                sid = row.station_id
                hb = row.hour_bucket

                # Utilization rate: precisa do num_connectors da estação
                if sid not in station_connectors:
                    st = session.get(ChargingStation, sid)
                    station_connectors[sid] = st.num_connectors or 1 if st else 1

                connectors = station_connectors[sid]
                utilization = (
                    round(row.session_count / connectors, 4)
                    if connectors > 0 else None
                )

                # Lag features — só dados anteriores ao bucket (sem leakage)
                lag_1h = _compute_lag(session, sid, hb, 1)
                lag_24h = _compute_lag(session, sid, hb, 24)
                lag_168h = _compute_lag(session, sid, hb, 168)
                avg_7d, std_7d = _compute_rolling_avg(session, sid, hb, 7)
                avg_30d, _ = _compute_rolling_avg(session, sid, hb, 30)

                rows_to_upsert.append({
                    "station_id": sid,
                    "hour_bucket": hb,
                    "session_count": row.session_count,
                    "total_kwh": float(row.total_kwh) if row.total_kwh else None,
                    "peak_kw": float(row.peak_kw) if row.peak_kw else None,
                    "avg_kw": float(row.avg_kw) if row.avg_kw else None,
                    "utilization_rate": utilization,
                    "hour_of_day": row.hour_of_day,
                    "day_of_week": row.day_of_week,
                    "month_of_year": row.month_of_year,
                    "is_weekend": row.is_weekend,
                    "is_holiday": None,  # enriquecido por job separado
                    "lag_1h_kwh": lag_1h,
                    "lag_24h_kwh": lag_24h,
                    "lag_168h_kwh": lag_168h,
                    "rolling_7d_avg_kwh": avg_7d,
                    "rolling_7d_std_kwh": std_7d,
                    "rolling_30d_avg_kwh": avg_30d,
                    "computed_at": computed_at,
                })

            except Exception as exc:
                logger.warning(
                    "Erro ao computar features para station=%d bucket=%s: %s",
                    row.station_id, row.hour_bucket, exc,
                )
                stats["errors"] += 1

        if rows_to_upsert:
            stmt = pg_insert(StationHourlyDemand).values(rows_to_upsert)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ana_shd_station_hour",
                set_={
                    "session_count": stmt.excluded.session_count,
                    "total_kwh": stmt.excluded.total_kwh,
                    "peak_kw": stmt.excluded.peak_kw,
                    "avg_kw": stmt.excluded.avg_kw,
                    "utilization_rate": stmt.excluded.utilization_rate,
                    "lag_1h_kwh": stmt.excluded.lag_1h_kwh,
                    "lag_24h_kwh": stmt.excluded.lag_24h_kwh,
                    "lag_168h_kwh": stmt.excluded.lag_168h_kwh,
                    "rolling_7d_avg_kwh": stmt.excluded.rolling_7d_avg_kwh,
                    "rolling_7d_std_kwh": stmt.excluded.rolling_7d_std_kwh,
                    "rolling_30d_avg_kwh": stmt.excluded.rolling_30d_avg_kwh,
                    "computed_at": stmt.excluded.computed_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            result = session.execute(stmt)
            stats["rows_computed"] = result.rowcount

        logger.info(
            "Features horárias: %d upserted, %d erros",
            stats["rows_computed"], stats["errors"],
        )

    return stats
