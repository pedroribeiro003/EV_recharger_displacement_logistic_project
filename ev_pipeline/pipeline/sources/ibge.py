"""
pipeline/sources/ibge.py
Ingestor para a API pública do IBGE.
Busca municípios brasileiros com dados demográficos.

Endpoints usados:
  - /localidades/municipios          → lista de municípios
  - /agregados/...                   → dados censitários (população, PIB)

Documentação: https://servicodados.ibge.gov.br/api/docs
"""

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from models.staging import StgMunicipality
from pipeline.base_ingester import BaseIngester
from utils.http_client import build_session, get_json

settings = get_settings()
BASE_URL = settings.ibge_api_base_url

# Mapeamento de código de UF → nome de região
_REGION_MAP: dict[str, str] = {
    "11": "Norte", "12": "Norte", "13": "Norte", "14": "Norte",
    "15": "Norte", "16": "Norte", "17": "Norte",
    "21": "Nordeste", "22": "Nordeste", "23": "Nordeste", "24": "Nordeste",
    "25": "Nordeste", "26": "Nordeste", "27": "Nordeste", "28": "Nordeste",
    "29": "Nordeste",
    "31": "Sudeste", "32": "Sudeste", "33": "Sudeste", "35": "Sudeste",
    "41": "Sul", "42": "Sul", "43": "Sul",
    "50": "Centro-Oeste", "51": "Centro-Oeste",
    "52": "Centro-Oeste", "53": "Centro-Oeste",
}


class IBGEMunicipalityIngester(BaseIngester):
    source_name = "ibge"

    def __init__(self) -> None:
        super().__init__()
        self._http = build_session()

    def fetch_raw(
        self,
        state_code: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Busca todos os municípios (ou apenas de uma UF).
        Enriquece com informações de microrregião.
        """
        if state_code:
            url = f"{BASE_URL}/localidades/estados/{state_code.upper()}/municipios"
        else:
            url = f"{BASE_URL}/localidades/municipios"

        municipalities = get_json(url, session=self._http)
        self.logger.info("IBGE: %d municípios obtidos", len(municipalities))
        return municipalities

    def parse(self, raw_records: list[dict[str, Any]]) -> list[StgMunicipality]:
        records: list[StgMunicipality] = []
        for item in raw_records:
            try:
                ibge_code = str(item["id"])
                uf_code = ibge_code[:2]
                state_code = item.get("microrregiao", {}).get(
                    "mesorregiao", {}
                ).get("UF", {}).get("sigla")

                # Fallback: deduz UF pelo prefixo do código IBGE
                if not state_code:
                    uf_item = item.get("microrregiao", {}).get(
                        "mesorregiao", {}
                    ).get("UF", {})
                    state_code = uf_item.get("sigla", "??")

                records.append(
                    StgMunicipality(
                        source="ibge",
                        ibge_code=ibge_code,
                        raw_json=item,
                        name=item.get("nome"),
                        state_code=state_code,
                        region=_REGION_MAP.get(uf_code),
                    )
                )
            except (KeyError, TypeError) as exc:
                self.logger.warning(
                    "Erro ao parsear município IBGE id=%s: %s",
                    item.get("id"), exc,
                )

        self.logger.debug("IBGE parsed: %d municípios", len(records))
        return records

    def upsert(
        self,
        session: Session,
        records: list[StgMunicipality],
    ) -> tuple[int, int]:
        if not records:
            return 0, 0

        rows = [
            {
                "source": r.source,
                "ibge_code": r.ibge_code,
                "raw_json": r.raw_json,
                "name": r.name,
                "state_code": r.state_code,
                "region": r.region,
                "is_processed": False,
            }
            for r in records
        ]

        stmt = pg_insert(StgMunicipality).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stg_mun_source_code",
            set_={
                "raw_json": stmt.excluded.raw_json,
                "name": stmt.excluded.name,
                "state_code": stmt.excluded.state_code,
                "region": stmt.excluded.region,
                "is_processed": False,
                "ingested_at": stmt.excluded.ingested_at,
            },
        )

        result = session.execute(stmt)
        self.logger.info("IBGE upsert: %d linhas afetadas", result.rowcount)
        return result.rowcount, 0
