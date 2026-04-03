import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.config import settings
from core.logging import get_logger
from db.models.anp import AnpGasStation

logger = get_logger(__name__)

_PAGE_SIZE = 1000


class AnpIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(
            base_url=settings.anp_base_url,
            headers={"Accept": "application/json"},
            timeout=60,
        )

    def fetch_all_stations(self) -> list[dict]:
        """Paginate GET /Combustivel until exhausted."""
        results: list[dict] = []
        page = 1
        while True:
            try:
                resp = self.client.get(
                    "/Combustivel",
                    params={"page": page, "per_page": _PAGE_SIZE},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("ANP page %d failed: %s", page, exc)
                break

            batch = data if isinstance(data, list) else data.get("data") or data.get("items") or []
            if not batch:
                break
            results.extend(batch)
            logger.debug("ANP: page %d → %d records", page, len(batch))
            if len(batch) < _PAGE_SIZE:
                break
            page += 1

        logger.info("ANP: fetched %d gas stations total", len(results))
        return results

    @staticmethod
    def _to_row(raw: dict) -> dict:
        return {
            "cnpj": str(raw.get("cnpj") or raw.get("CNPJ") or "").strip(),
            "trade_name": raw.get("nomeFantasia") or raw.get("razaoSocial") or raw.get("name"),
            "municipality_id": raw.get("municipioId") or raw.get("codMunicipio"),
            "state_uf": raw.get("uf") or raw.get("siglaUF"),
            "address": raw.get("endereco") or raw.get("logradouro"),
            "lat": raw.get("latitude") or raw.get("lat"),
            "lng": raw.get("longitude") or raw.get("lng"),
            "has_ev_charger": bool(raw.get("possuiEletrico") or raw.get("hasEvCharger", False)),
        }

    def run(self) -> None:
        records = self.fetch_all_stations()
        rows = [self._to_row(r) for r in records if r.get("cnpj") or r.get("CNPJ")]

        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(AnpGasStation).on_conflict_do_update(
                index_elements=["cnpj"],
                set_={k: pg_insert(AnpGasStation).excluded[k] for k in batch[0] if k != "cnpj"},
            )
            self.session.execute(stmt, batch)
            self.session.commit()
            logger.info("ANP: upserted batch %d/%d", i + len(batch), len(rows))

        logger.info("ANP ingestion complete: %d stations", len(rows))
