import time
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging import get_logger

logger = get_logger(__name__)

_CKAN_SQL = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search_sql"
_RESOURCE_ID = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
_LIMIT = 2000


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
            timeout=120,
            follow_redirects=True,
        )

    def _get_with_retry(self, **kwargs) -> httpx.Response:
        """GET with exponential backoff for 5xx errors and timeouts (max 4 attempts)."""
        for attempt in range(1, 5):
            try:
                resp = self.client.get(**kwargs)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
                wait = 15 * attempt
                logger.warning("ANEEL: HTTP %d (attempt %d/4) — retrying in %ds",
                               resp.status_code, attempt, wait)
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
                wait = 15 * attempt
                logger.warning("ANEEL: timeout (attempt %d/4) — retrying in %ds: %s",
                               attempt, wait, exc)
            time.sleep(wait)
        raise RuntimeError("ANEEL: gave up after 4 attempts")

    def fetch_current_tariffs(self) -> list[dict]:
        """Paginate via SQL endpoint using keyset (_id > last_id) to avoid offset limits."""
        today = date.today().isoformat()
        records: list[dict] = []
        last_id = 0

        while True:
            sql = (
                f'SELECT "_id","SigAgente","NumCNPJDistribuidora",'
                f'"DatInicioVigencia","DatFimVigencia","DscBaseTarifaria",'
                f'"DscSubGrupo","DscModalidadeTarifaria","DscUnidadeTerciaria",'
                f'"VlrTUSD","VlrTE" '
                f'FROM "{_RESOURCE_ID}" '
                f'WHERE "_id" > {last_id} '
                f"AND \"DscUnidadeTerciaria\" = 'MWh' "
                f"AND \"DatFimVigencia\" >= '{today}' "
                f'ORDER BY "_id" '
                f'LIMIT {_LIMIT}'
            )
            resp = self._get_with_retry(url=_CKAN_SQL, params={"sql": sql})
            raw_batch = resp.json().get("result", {}).get("records", [])

            if not raw_batch:
                break

            last_id = raw_batch[-1]["_id"]

            # Filter DscBaseTarifaria in Python to avoid accent encoding issues
            active = [r for r in raw_batch
                      if "Aplica" in (r.get("DscBaseTarifaria") or "")]
            records.extend(active)
            logger.debug("ANEEL: fetched %d records so far (last_id=%d)", len(records), last_id)

            if len(batch) < _LIMIT:
                break

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
