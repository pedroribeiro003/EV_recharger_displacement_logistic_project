"""
pipeline/transforms/stations.py
ETL: staging.stg_charging_stations → core.charging_stations

Enriquece com municipality_id via lookup pelo código IBGE
inferido da localização (PostGIS ST_Within) ou por nome/UF.
"""

from datetime import date

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.database import get_session
from config.settings import get_settings
from models.staging import StgChargingStation
from models.core import ChargingStation, Municipality
from utils.logger import get_logger

settings = get_settings()
logger = get_logger("transforms.stations")
CORE = settings.db_schema_core


def _resolve_municipality_id(
    session: Session,
    latitude: float | None,
    longitude: float | None,
) -> int | None:
    """
    Tenta associar uma estação a um município via ST_Within (PostGIS).
    Fallback: None (estação fica sem município até próximo ETL).
    """
    if latitude is None or longitude is None:
        return None
    try:
        row = session.execute(
            text(f"""
                SELECT id FROM {CORE}.municipalities
                WHERE ST_Within(
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                    geometry
                )
                LIMIT 1
            """),
            {"lat": latitude, "lon": longitude},
        ).fetchone()
        return row[0] if row else None
    except Exception as exc:
        # Fallback se geometry ainda não está populada
        logger.debug("ST_Within falhou (geometry não disponível?): %s", exc)
        return None


def _extract_station_fields(raw: dict) -> dict:
    """Extrai campos relevantes do payload bruto da OCM."""
    addr = raw.get("AddressInfo", {}) or {}
    connections = raw.get("Connections", []) or []
    op = raw.get("OperatorInfo", {}) or {}
    status = raw.get("StatusType", {}) or {}

    # Potência máxima: maior PowerKW entre os conectores
    max_power = None
    connector_types = []
    for conn in connections:
        if conn.get("PowerKW"):
            try:
                kw = float(conn["PowerKW"])
                max_power = max(max_power or 0, kw)
            except (ValueError, TypeError):
                pass
        ct = (conn.get("ConnectionType", {}) or {}).get("FormalName")
        if ct and ct not in connector_types:
            connector_types.append(ct)

    return {
        "name": addr.get("Title"),
        "operator": op.get("Title"),
        "address": addr.get("AddressLine1"),
        "latitude": addr.get("Latitude"),
        "longitude": addr.get("Longitude"),
        "max_power_kw": max_power,
        "num_connectors": len(connections),
        "is_public": raw.get("UsageType", {}).get("IsPayAtLocation") is not None,
        "is_operational": (status.get("IsOperational") or True),
    }


def transform_stations(batch_size: int = 200) -> dict[str, int]:
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    with get_session() as session:
        pending = (
            session.execute(
                select(StgChargingStation)
                .where(StgChargingStation.is_processed == False)
                .order_by(StgChargingStation.ingested_at)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        if not pending:
            logger.info("Nenhuma estação pendente no staging.")
            return stats

        logger.info("Transformando %d estações...", len(pending))

        rows_to_upsert = []
        processed_ids = []

        for stg in pending:
            try:
                fields = _extract_station_fields(stg.raw_json or {})
                municipality_id = _resolve_municipality_id(
                    session, fields["latitude"], fields["longitude"]
                )

                rows_to_upsert.append({
                    "external_id": stg.source_id,
                    "source": stg.source_name,
                    "municipality_id": municipality_id,
                    "name": fields["name"],
                    "operator": fields["operator"],
                    "address": fields["address"],
                    "latitude": fields["latitude"],
                    "longitude": fields["longitude"],
                    "max_power_kw": fields["max_power_kw"],
                    "num_connectors": fields["num_connectors"],
                    "is_public": fields["is_public"],
                    "is_operational": fields["is_operational"],
                })
                processed_ids.append(stg.id)

            except Exception as exc:
                logger.warning(
                    "Erro ao transformar estação stg_id=%d: %s", stg.id, exc
                )
                stats["errors"] += 1

        if rows_to_upsert:
            stmt = pg_insert(ChargingStation).values(rows_to_upsert)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_core_station_source_ext",
                set_={
                    "municipality_id": stmt.excluded.municipality_id,
                    "name": stmt.excluded.name,
                    "operator": stmt.excluded.operator,
                    "address": stmt.excluded.address,
                    "latitude": stmt.excluded.latitude,
                    "longitude": stmt.excluded.longitude,
                    "max_power_kw": stmt.excluded.max_power_kw,
                    "num_connectors": stmt.excluded.num_connectors,
                    "is_operational": stmt.excluded.is_operational,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            result = session.execute(stmt)
            stats["inserted"] = result.rowcount

        if processed_ids:
            session.execute(
                update(StgChargingStation)
                .where(StgChargingStation.id.in_(processed_ids))
                .values(is_processed=True)
            )

        logger.info(
            "Estações: inserted/updated=%d errors=%d",
            stats["inserted"], stats["errors"],
        )

    return stats
