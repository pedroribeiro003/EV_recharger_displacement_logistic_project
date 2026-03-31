"""
pipeline/transforms/municipalities.py
ETL: staging.stg_municipalities → core.municipalities

Lê registros não processados do staging, normaliza e faz
upsert na tabela core. Marca registros como processados ao final.
"""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.database import get_session
from config.settings import get_settings
from models.staging import StgMunicipality
from models.core import Municipality
from utils.logger import get_logger

settings = get_settings()
logger = get_logger("transforms.municipalities")

# Mapeamento código UF → nome da região
_REGION_MAP: dict[str, str] = {
    **{c: "Norte" for c in ["11","12","13","14","15","16","17"]},
    **{c: "Nordeste" for c in ["21","22","23","24","25","26","27","28","29"]},
    **{c: "Sudeste" for c in ["31","32","33","35"]},
    **{c: "Sul" for c in ["41","42","43"]},
    **{c: "Centro-Oeste" for c in ["50","51","52","53"]},
}


def transform_municipalities(batch_size: int = 500) -> dict[str, int]:
    """
    Processa municípios do staging para o core.
    Retorna contadores: inserted, updated, skipped, errors.
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    with get_session() as session:
        # Busca apenas registros não processados
        pending = (
            session.execute(
                select(StgMunicipality)
                .where(StgMunicipality.is_processed == False)
                .order_by(StgMunicipality.ingested_at)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        if not pending:
            logger.info("Nenhum município pendente no staging.")
            return stats

        logger.info("Transformando %d municípios...", len(pending))

        rows_to_upsert = []
        processed_ids = []

        for stg in pending:
            try:
                raw = stg.raw_json or {}
                ibge_code = stg.ibge_code.strip()[:7]
                uf_prefix = ibge_code[:2]

                # Extrai estado: do raw_json ou do campo extraído
                state_code = (
                    stg.state_code
                    or (raw.get("microrregiao", {})
                        .get("mesorregiao", {})
                        .get("UF", {})
                        .get("sigla"))
                    or "??"
                )

                rows_to_upsert.append({
                    "ibge_code": ibge_code,
                    "name": stg.name or raw.get("nome", ""),
                    "state_code": state_code.upper()[:2],
                    "region": _REGION_MAP.get(uf_prefix),
                })
                processed_ids.append(stg.id)

            except Exception as exc:
                logger.warning(
                    "Erro ao transformar município stg_id=%d: %s", stg.id, exc
                )
                stats["errors"] += 1

        if rows_to_upsert:
            stmt = pg_insert(Municipality).values(rows_to_upsert)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_core_mun_ibge_code",
                set_={
                    "name": stmt.excluded.name,
                    "state_code": stmt.excluded.state_code,
                    "region": stmt.excluded.region,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            result = session.execute(stmt)
            stats["inserted"] = result.rowcount

        # Marca staging como processado
        if processed_ids:
            session.execute(
                update(StgMunicipality)
                .where(StgMunicipality.id.in_(processed_ids))
                .values(is_processed=True)
            )

        logger.info(
            "Municípios: inserted/updated=%d errors=%d",
            stats["inserted"], stats["errors"],
        )

    return stats
