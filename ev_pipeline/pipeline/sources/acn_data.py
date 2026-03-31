"""
pipeline/sources/acn_data.py
Ingestor para sessões de carregamento do ACN-Data (Caltech).
Dataset público: https://ev.caltech.edu/dataset

O ACN-Data disponibiliza CSVs e API REST.
Este ingestor cobre ambos os modos:
  - fetch_from_csv()  → lê arquivo CSV baixado manualmente
  - fetch_raw()       → API REST (requer token acadêmico)

Documentação API: https://ev.caltech.edu/api/v1/sessions
"""

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from models.staging import StgChargingSession
from pipeline.base_ingester import BaseIngester
from utils.http_client import build_session, get_json

settings = get_settings()

ACN_API_BASE = "https://ev.caltech.edu/api/v1"


def _parse_acn_datetime(raw: str | None) -> datetime | None:
    """Converte strings de data do ACN-Data para datetime UTC."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class ACNDataIngester(BaseIngester):
    source_name = "acn_data"

    def __init__(self) -> None:
        super().__init__()
        self._http = build_session()

    # ── Modo 1: CSV ──────────────────────────────────────────────────────────

    def fetch_from_csv(self, file_path: str | Path) -> list[dict[str, Any]]:
        """
        Lê um CSV exportado do ACN-Data.
        Colunas esperadas (subset):
          _id, connectionTime, disconnectTime, doneChargingTime,
          kWhDelivered, stationID, spaceID, userInputs
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV não encontrado: {path}")

        records: list[dict[str, Any]] = []
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                records.append(dict(row))

        self.logger.info("ACN-Data CSV: %d sessões lidas de %s", len(records), path)
        return records

    # ── Modo 2: API REST ──────────────────────────────────────────────────────

    def fetch_raw(
        self,
        site: str = "caltech",
        start_date: str | None = None,
        end_date: str | None = None,
        max_results: int = 1000,
        api_token: str | None = None,
        csv_path: str | Path | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Parâmetros:
          site        → caltech | jpl | office001 ...
          start_date  → "YYYY-MM-DD"
          end_date    → "YYYY-MM-DD"
          csv_path    → se fornecido, lê do CSV em vez da API
        """
        if csv_path:
            return self.fetch_from_csv(csv_path)

        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        params: dict[str, Any] = {
            "site": site,
            "limit": min(max_results, 500),
        }
        if start_date:
            params["startTime"] = start_date
        if end_date:
            params["endTime"] = end_date

        all_records: list[dict[str, Any]] = []
        url = f"{ACN_API_BASE}/sessions"

        while True:
            params["offset"] = len(all_records)
            data = get_json(url, params=params, headers=headers, session=self._http)

            # ACN-Data retorna {"_items": [...], "_meta": {...}}
            items = data.get("_items", []) if isinstance(data, dict) else data
            if not items:
                break

            all_records.extend(items)
            self.logger.info(
                "ACN-Data batch: +%d sessões (total: %d)", len(items), len(all_records)
            )

            if len(items) < params["limit"] or len(all_records) >= max_results:
                break

        return all_records[:max_results]

    def parse(self, raw_records: list[dict[str, Any]]) -> list[StgChargingSession]:
        records: list[StgChargingSession] = []

        for item in raw_records:
            try:
                # Suporta tanto o formato API (campos camelCase) quanto CSV
                session_id = str(
                    item.get("_id") or item.get("sessionID") or item.get("_id", "")
                )
                station_id = str(
                    item.get("stationID") or item.get("station_id", "")
                )
                start_raw = item.get("connectionTime") or item.get("connection_time")
                end_raw = item.get("disconnectTime") or item.get("disconnect_time")

                kwh_raw = item.get("kWhDelivered") or item.get("kwh_delivered")
                energy_kwh = float(kwh_raw) if kwh_raw else None

                records.append(
                    StgChargingSession(
                        source_session_id=session_id,
                        source_name=self.source_name,
                        raw_json=item if isinstance(item, dict) else {"raw": item},
                        station_source_id=station_id or None,
                        session_start=_parse_acn_datetime(start_raw),
                        session_end=_parse_acn_datetime(end_raw),
                        energy_kwh=energy_kwh,
                    )
                )
            except Exception as exc:
                self.logger.warning(
                    "Erro ao parsear sessão ACN id=%s: %s",
                    item.get("_id"), exc,
                )

        self.logger.debug("ACN-Data parsed: %d sessões", len(records))
        return records

    def upsert(
        self,
        session: Session,
        records: list[StgChargingSession],
    ) -> tuple[int, int]:
        if not records:
            return 0, 0

        batch_size = settings.pipeline_batch_size
        total_affected = 0

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            rows = [
                {
                    "source_session_id": r.source_session_id,
                    "source_name": r.source_name,
                    "raw_json": r.raw_json,
                    "station_source_id": r.station_source_id,
                    "session_start": r.session_start,
                    "session_end": r.session_end,
                    "energy_kwh": r.energy_kwh,
                    "is_processed": False,
                }
                for r in batch
            ]

            stmt = pg_insert(StgChargingSession).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stg_session_source",
                set_={
                    "raw_json": stmt.excluded.raw_json,
                    "session_start": stmt.excluded.session_start,
                    "session_end": stmt.excluded.session_end,
                    "energy_kwh": stmt.excluded.energy_kwh,
                    "is_processed": False,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )

            result = session.execute(stmt)
            total_affected += result.rowcount
            self.logger.debug("ACN-Data batch %d/%d upserted", i + batch_size, len(records))

        self.logger.info("ACN-Data upsert: %d linhas afetadas", total_affected)
        return total_affected, 0
