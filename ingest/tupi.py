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
        import json as _json
        params = {"plugTypes": _json.dumps(_PLUG_TYPES), "fast": "false", "searchText": ""}
        resp = self.client.get("/stationsShortVersion", params=params)
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
        address_raw = merged.get("address") or {}
        address_str = address_raw if isinstance(address_raw, str) else (
            address_raw.get("street") or merged.get("fullAddress") or ""
        )
        return {
            "station_code": str(
                merged.get("_id") or merged.get("id") or merged.get("stationId") or ""
            ),
            "name": merged.get("name") or merged.get("title"),
            "operator": merged.get("operator") or merged.get("network"),
            "address": address_str or None,
            "city": merged.get("city"),
            "state_uf": merged.get("state") or merged.get("state_uf"),
            "lat": float(location.get("lat") or merged.get("lat") or 0) or None,
            "lng": float(location.get("lng") or merged.get("lng") or 0) or None,
            "is_public": not bool(merged.get("private", False)),
            "raw_json": json.dumps(raw, ensure_ascii=False),
        }

    def _parse_connectors(self, raw: dict) -> list[dict]:
        connectors = (
            raw.get("connectedPlugs")
            or raw.get("connectors")
            or raw.get("plugs")
            or []
        )
        result = []
        for c in connectors:
            result.append(
                {
                    "connector_code": str(
                        c.get("_id") or c.get("id") or c.get("connectorId") or ""
                    ),
                    "plug_type": c.get("type") or c.get("plugType") or c.get("current"),
                    "power_kw": c.get("power") or c.get("powerKw") or c.get("maxPower"),
                    "is_fast": c.get("current", "").upper() == "DC",
                    "current_status": str(
                        c.get("stateName") or c.get("status") or c.get("state") or "UNKNOWN"
                    ).upper(),
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
            sid = str(raw.get("_id") or raw.get("id") or raw.get("stationId") or "")
            if not sid:
                continue

            detail = self.fetch_station_detail(sid)
            station_data = self._parse_station(raw, detail)
            station = self.station_repo.upsert(station_data)

            # Upsert connectors
            if detail or raw.get("connectedPlugs") or raw.get("connectors"):
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
        from sqlalchemy import select
        from db.models.station import Station

        logger.info("Tupi poll: starting (interval=%ds)", settings.poll_interval)
        while True:
            try:
                stations_api = self.fetch_all_stations()

                # Preload all known station codes in one query to avoid N+1
                all_db_stations: dict[str, int] = {
                    s.station_code: s.id
                    for s in self.session.scalars(select(Station)).all()
                }

                snapshots: list[StatusSnapshot] = []
                for raw in stations_api:
                    station_code = str(raw.get("_id") or raw.get("id") or raw.get("stationId") or "")
                    if not station_code:
                        continue
                    station_id = all_db_stations.get(station_code)
                    if station_id is None:
                        continue
                    connectors = (
                        raw.get("connectedPlugs")
                        or raw.get("connectors")
                        or raw.get("plugs")
                        or []
                    )
                    for connector in connectors:
                        snapshots.append(StatusSnapshot.from_tupi(station_id, connector))

                inserted = self.snapshot_repo.bulk_insert(snapshots)
                self.session.commit()
                logger.info("Tupi poll: inserted %d snapshots", inserted)
            except Exception as exc:
                logger.exception("Tupi poll error: %s", exc)
                self.session.rollback()

            time.sleep(settings.poll_interval)
