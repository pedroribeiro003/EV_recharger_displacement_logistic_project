"""
pipeline/sources/open_charge_map.py
Ingestor para a API Open Charge Map (OCM).
Documentação: https://openchargemap.org/site/develop/api

Parâmetros principais:
  - countrycode: BR
  - maxresults: até 500 por request
  - compact=false: payload completo
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from models.staging import StgChargingStation
from pipeline.base_ingester import BaseIngester
from utils.http_client import build_session, get_json
from utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

OCM_BASE_URL = "https://api.openchargemap.io/v3/poi"


class OpenChargeMapIngester(BaseIngester):
    source_name = "open_charge_map"

    def __init__(self) -> None:
        super().__init__()
        self._http = build_session()

    def fetch_raw(
        self,
        country_code: str = "BR",
        max_results: int = 1000,
        latitude: float | None = None,
        longitude: float | None = None,
        distance: int | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Busca estações da OCM.
        Sem lat/lon: retorna estações do país inteiro (paginado internamente).
        Com lat/lon + distance: busca no raio em km.
        """
        params: dict[str, Any] = {
            "key": settings.open_charge_map_api_key,
            "output": "json",
            "countrycode": country_code,
            "maxresults": min(max_results, 500),  # OCM limita em 500
            "compact": "false",
            "verbose": "false",
        }
        if latitude is not None and longitude is not None:
            params["latitude"] = latitude
            params["longitude"] = longitude
            params["distance"] = distance or 50
            params["distanceunit"] = "km"

        all_records: list[dict[str, Any]] = []
        page = 0

        while True:
            params["startindex"] = page * params["maxresults"]
            self.logger.debug(
                "OCM request: startindex=%d maxresults=%d",
                params["startindex"],
                params["maxresults"],
            )
            batch = get_json(OCM_BASE_URL, params=params, session=self._http)

            if not batch:
                break

            all_records.extend(batch)
            self.logger.info(
                "OCM batch %d: %d registros (total acumulado: %d)",
                page + 1, len(batch), len(all_records),
            )

            if len(batch) < params["maxresults"]:
                break  # última página

            page += 1
            if len(all_records) >= max_results:
                break

        return all_records[:max_results]

    def parse(self, raw_records: list[dict[str, Any]]) -> list[StgChargingStation]:
        records: list[StgChargingStation] = []
        for item in raw_records:
            try:
                addr = item.get("AddressInfo", {})
                records.append(
                    StgChargingStation(
                        source_id=str(item["ID"]),
                        source_name=self.source_name,
                        raw_json=item,
                        latitude=addr.get("Latitude"),
                        longitude=addr.get("Longitude"),
                        station_name=addr.get("Title"),
                        operator=(
                            item.get("OperatorInfo", {}) or {}
                        ).get("Title"),
                        country_code=addr.get("Country", {}).get("ISOCode"),
                        status_type=(
                            item.get("StatusType", {}) or {}
                        ).get("Title"),
                    )
                )
            except (KeyError, TypeError) as exc:
                self.logger.warning("Erro ao parsear registro OCM id=%s: %s", item.get("ID"), exc)

        self.logger.debug("Parsed %d / %d registros OCM", len(records), len(raw_records))
        return records

    def upsert(
        self,
        session: Session,
        records: list[StgChargingStation],
    ) -> tuple[int, int]:
        """
        UPSERT via ON CONFLICT (source_name, source_id).
        Atualiza raw_json e campos extraídos a cada ingestão.
        """
        if not records:
            return 0, 0

        rows = [
            {
                "source_id": r.source_id,
                "source_name": r.source_name,
                "raw_json": r.raw_json,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "station_name": r.station_name,
                "operator": r.operator,
                "country_code": r.country_code,
                "status_type": r.status_type,
                "is_processed": False,
            }
            for r in records
        ]

        stmt = pg_insert(StgChargingStation).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stg_station_source",
            set_={
                "raw_json": stmt.excluded.raw_json,
                "latitude": stmt.excluded.latitude,
                "longitude": stmt.excluded.longitude,
                "station_name": stmt.excluded.station_name,
                "operator": stmt.excluded.operator,
                "status_type": stmt.excluded.status_type,
                "is_processed": False,  # marca para reprocessamento
                "ingested_at": stmt.excluded.ingested_at,
            },
        )

        result = session.execute(stmt)
        # rowcount em upsert postgresql = inserted + updated
        total = result.rowcount
        inserted = total  # sem diferenciar insert/update no bulk upsert
        self.logger.info("OCM upsert: %d linhas afetadas", total)
        return inserted, 0
