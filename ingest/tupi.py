import json
import time

import httpx
from sqlalchemy.orm import Session

from core.config import settings
from core.logging import get_logger
from db.models.snapshot import StatusSnapshot
from db.repositories.snapshot_repo import SnapshotRepository
from db.repositories.station_repo import StationRepository

logger = get_logger(__name__)

_PLUG_TYPES = ["Tipo 2", "CCS 2", "CHAdeMO"]


class TupiIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.station_repo = StationRepository(session)
        self.snapshot_repo = SnapshotRepository(session)
        self.client = httpx.Client(
            base_url=settings.tupi_base_url,
            headers={
                "Origin": settings.tupi_origin,
                "Referer": settings.tupi_origin + "/",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
        )

    # ------------------------------------------------------------------
    # Fetchers
    # ------------------------------------------------------------------

    def fetch_all_stations(self) -> list[dict]:
        payload = {"plugTypes": _PLUG_TYPES, "fast": False}
        resp = self.client.post("/stationsShortVersion", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("stations") or data.get("data") or []

    def fetch_station_detail(self, station_id: str) -> dict | None:
        try:
            resp = self.client.get(f"/station/{station_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Detail fetch failed for %s: %s", station_id, exc)
            return None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_station(self, raw: dict, detail: dict | None = None) -> dict:
        merged = {**raw, **(detail or {})}
        location = merged.get("location") or {}
        address = merged.get("address") or {}
        return {
            "station_code": str(merged.get("id") or merged.get("stationId") or merged.get("_id")),
            "name": merged.get("name") or merged.get("title"),
            "operator": merged.get("operator") or merged.get("network"),
            "address": (
                address.get("street")
                or merged.get("address")
                or merged.get("fullAddress")
            ),
            "city": address.get("city") or merged.get("city"),
            "state_uf": address.get("state") or merged.get("state"),
            "lat": float(location.get("lat") or merged.get("lat") or 0) or None,
            "lng": float(location.get("lng") or merged.get("lng") or 0) or None,
            "is_public": bool(merged.get("isPublic", True)),
            "raw_json": json.dumps(raw, ensure_ascii=False),
        }

    def _parse_connectors(self, raw: dict) -> list[dict]:
        connectors = raw.get("connectors") or raw.get("plugs") or []
        result = []
        for c in connectors:
            result.append(
                {
                    "connector_code": str(c.get("id") or c.get("connectorId") or ""),
                    "plug_type": c.get("type") or c.get("plugType"),
                    "power_kw": c.get("powerKw") or c.get("maxPower"),
                    "is_fast": bool(c.get("isFast") or c.get("fast", False)),
                    "current_status": str(c.get("status") or c.get("state") or "UNKNOWN").upper(),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    def run_enrich(self, delay: float = 0.3) -> None:
        """Fetch list + detail for each station, upsert into DB."""
        logger.info("Tupi enrich: fetching station list")
        stations = self.fetch_all_stations()
        logger.info("Tupi enrich: %d stations found", len(stations))

        for idx, raw in enumerate(stations, 1):
            sid = str(raw.get("id") or raw.get("stationId") or raw.get("_id") or "")
            if not sid:
                continue

            detail = self.fetch_station_detail(sid)
            station_data = self._parse_station(raw, detail)
            station = self.station_repo.upsert(station_data)

            # Upsert connectors
            if detail or raw.get("connectors"):
                source = detail or raw
                for conn_data in self._parse_connectors(source):
                    from sqlalchemy.dialects.postgresql import insert as pg_insert
                    from db.models.station import Connector

                    conn_data["station_id"] = station.id
                    stmt = (
                        pg_insert(Connector)
                        .values(**conn_data)
                        .on_conflict_do_update(
                            index_elements=["station_id", "connector_code"],
                            set_={k: v for k, v in conn_data.items() if k not in ("station_id", "connector_code")},
                        )
                    )
                    self.session.execute(stmt)

            if idx % 100 == 0:
                logger.info("Tupi enrich: processed %d / %d", idx, len(stations))
                self.session.commit()

            time.sleep(delay)

        self.session.commit()
        logger.info("Tupi enrich: complete")

    def run_poll(self) -> None:
        """Infinite poll loop: snapshot connector statuses."""
        logger.info("Tupi poll: starting (interval=%ds)", settings.poll_interval)
        while True:
            try:
                stations = self.fetch_all_stations()
                snapshots: list[StatusSnapshot] = []
                for raw in stations:
                    station_code = str(
                        raw.get("id") or raw.get("stationId") or raw.get("_id") or ""
                    )
                    if not station_code:
                        continue
                    station = self.station_repo.get_by_station_code(station_code)
                    if station is None:
                        continue
                    for connector in raw.get("connectors") or raw.get("plugs") or []:
                        snapshots.append(StatusSnapshot.from_tupi(station.id, connector))

                inserted = self.snapshot_repo.bulk_insert(snapshots)
                self.session.commit()
                logger.info("Tupi poll: inserted %d snapshots", inserted)
            except Exception as exc:
                logger.exception("Tupi poll error: %s", exc)
                self.session.rollback()

            time.sleep(settings.poll_interval)
