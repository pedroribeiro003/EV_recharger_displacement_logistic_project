from datetime import date, datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging import get_logger

logger = get_logger(__name__)

_CKAN_BASE = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"
_RESOURCE_ID = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
_LIMIT = 5000

# Only fetch "Tarifa de Aplicação" (applied tariff, not theoretical base) for energy (MWh unit)
_FILTERS = {
    "DscBaseTarifaria": "Tarifa de Aplicação",
    "DscUnidadeTerciaria": "MWh",
}


def _parse_decimal(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class AneelIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(
            headers={"Accept": "application/json"},
            timeout=60,
            follow_redirects=True,
        )

    def fetch_current_tariffs(self) -> list[dict]:
        """Paginate CKAN datastore for all current active tariffs."""
        today = date.today().isoformat()
        records: list[dict] = []
        offset = 0

        while True:
            resp = self.client.get(
                _CKAN_BASE,
                params={
                    "resource_id": _RESOURCE_ID,
                    "limit": _LIMIT,
                    "offset": offset,
                    "filters": str(_FILTERS).replace("'", '"'),
                },
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            batch = result.get("records", [])
            if not batch:
                break

            # Keep only records still valid today
            active = [
                r for r in batch
                if not r.get("DatFimVigencia") or r["DatFimVigencia"] >= today
            ]
            records.extend(active)
            logger.debug("ANEEL: offset=%d batch=%d active=%d total_so_far=%d",
                         offset, len(batch), len(active), len(records))

            if len(batch) < _LIMIT:
                break
            offset += _LIMIT

        logger.info("ANEEL: fetched %d active tariff records", len(records))
        return records

    def _ingest_distributors(self, records: list[dict]) -> dict[str, int]:
        """Upsert distributors and return {SigAgente: db_id} map."""
        agents = {r["SigAgente"].strip(): r.get("NumCNPJDistribuidora", "").strip()
                  for r in records if r.get("SigAgente")}

        id_map: dict[str, int] = {}
        for agent, cnpj in agents.items():
            # Truncate to fit the code column (max 20 chars)
            code = agent[:20]
            self.session.execute(
                text(
                    "INSERT INTO aneel_distributors (code, name) "
                    "VALUES (:code, :name) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name"
                ),
                {"code": code, "name": agent},
            )
            row = self.session.execute(
                text("SELECT id FROM aneel_distributors WHERE code = :code"),
                {"code": code},
            ).fetchone()
            id_map[agent] = row[0]

        self.session.commit()
        logger.info("ANEEL: upserted %d distributors", len(id_map))
        return id_map

    def _ingest_tariffs(self, records: list[dict], dist_map: dict[str, int]) -> None:
        """Replace tariffs: delete current + insert fresh batch."""
        dist_ids = list(dist_map.values())

        # Delete existing tariffs for these distributors
        if dist_ids:
            self.session.execute(
                text(
                    "DELETE FROM aneel_tariffs WHERE distributor_id = ANY(:ids)"
                ),
                {"ids": dist_ids},
            )

        rows = []
        for r in records:
            agent = (r.get("SigAgente") or "").strip()
            dist_id = dist_map.get(agent)
            if dist_id is None:
                continue

            subgroup = (r.get("DscSubGrupo") or "").strip()
            te_mwh   = _parse_decimal(r.get("VlrTE"))
            tusd_mwh = _parse_decimal(r.get("VlrTUSD"))

            rows.append({
                "distributor_id":   dist_id,
                "tariff_group":     subgroup[0] if subgroup else None,  # "A" or "B"
                "tariff_subgroup":  subgroup,
                "supply_type":      (r.get("DscModalidadeTarifaria") or "").strip() or None,
                "te_kwh":           round(te_mwh / 1000, 6) if te_mwh is not None else None,
                "tusd_kwh":         round(tusd_mwh / 1000, 6) if tusd_mwh is not None else None,
                "valid_from":       _parse_date(r.get("DatInicioVigencia")),
                "valid_to":         _parse_date(r.get("DatFimVigencia")),
            })

        batch_size = 1000
        for i in range(0, len(rows), batch_size):
            self.session.execute(
                text(
                    "INSERT INTO aneel_tariffs "
                    "(distributor_id, tariff_group, tariff_subgroup, supply_type, "
                    " te_kwh, tusd_kwh, valid_from, valid_to) "
                    "VALUES (:distributor_id, :tariff_group, :tariff_subgroup, :supply_type, "
                    "        :te_kwh, :tusd_kwh, :valid_from, :valid_to)"
                ),
                rows[i : i + batch_size],
            )
        self.session.commit()
        logger.info("ANEEL: inserted %d tariff rows", len(rows))

    def run(self) -> None:
        records = self.fetch_current_tariffs()
        dist_map = self._ingest_distributors(records)
        self._ingest_tariffs(records, dist_map)
        logger.info("ANEEL ingestion complete")
