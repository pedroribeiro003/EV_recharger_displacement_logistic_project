import csv
import io

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.ibge import IbgeMunicipality, IbgeState

logger = get_logger(__name__)

_IBGE_BASE = "https://servicodados.ibge.gov.br/api"
_CENTROIDS_CSV_URL = (
    "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv"
)


class IbgeIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(
            base_url=_IBGE_BASE, headers={"Accept": "application/json"}, timeout=60
        )

    # ------------------------------------------------------------------
    # Fetchers
    # ------------------------------------------------------------------

    def fetch_states(self) -> None:
        logger.info("IBGE: fetching states")
        resp = self.client.get("/v1/localidades/estados")
        resp.raise_for_status()
        states = resp.json()
        rows = [
            {
                "id": s["id"],
                "name": s["nome"],
                "uf": s["sigla"],
                "region": s["regiao"]["nome"],
            }
            for s in states
        ]
        stmt = pg_insert(IbgeState).on_conflict_do_update(
            index_elements=["id"],
            set_={"name": pg_insert(IbgeState).excluded.name,
                  "uf": pg_insert(IbgeState).excluded.uf,
                  "region": pg_insert(IbgeState).excluded.region},
        )
        self.session.execute(stmt, rows)
        self.session.commit()
        logger.info("IBGE: upserted %d states", len(rows))

    def fetch_municipalities(self) -> None:
        logger.info("IBGE: fetching municipalities")
        resp = self.client.get("/v1/localidades/municipios")
        resp.raise_for_status()
        municipalities = resp.json()
        rows = [
            {
                "id": m["id"],
                "name": m["nome"],
                "state_id": m["microrregiao"]["mesorregiao"]["UF"]["id"],
            }
            for m in municipalities
        ]
        stmt = pg_insert(IbgeMunicipality).on_conflict_do_update(
            index_elements=["id"],
            set_={"name": pg_insert(IbgeMunicipality).excluded.name,
                  "state_id": pg_insert(IbgeMunicipality).excluded.state_id},
        )
        self.session.execute(stmt, rows)
        self.session.commit()
        logger.info("IBGE: upserted %d municipalities", len(rows))

    def fetch_centroids(self) -> None:
        """Download centroids CSV from kelvins/municipios-brasileiros and patch lat/lng/geom."""
        logger.info("IBGE: downloading centroids CSV")
        resp = httpx.get(_CENTROIDS_CSV_URL, timeout=120, follow_redirects=True)
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        updated = 0
        for row in reader:
            ibge_code = row.get("codigo_ibge") or row.get("id")
            lat = row.get("latitude") or row.get("lat")
            lng = row.get("longitude") or row.get("lng") or row.get("lon")
            if not ibge_code or not lat or not lng:
                continue
            try:
                mun = self.session.get(IbgeMunicipality, int(ibge_code))
                if mun:
                    mun.lat = float(lat)
                    mun.lng = float(lng)
                    updated += 1
            except (ValueError, TypeError):
                continue

        # Flush lat/lng then update geom via SQL
        self.session.flush()
        self.session.execute(
            __import__("sqlalchemy").text(
                "UPDATE ibge_municipalities "
                "SET geom = ST_SetSRID(ST_MakePoint(lng, lat), 4326) "
                "WHERE lat IS NOT NULL AND lng IS NOT NULL"
            )
        )
        self.session.commit()
        logger.info("IBGE: updated %d municipality centroids", updated)

    def run(self) -> None:
        self.fetch_states()
        self.fetch_municipalities()
        self.fetch_centroids()
        logger.info("IBGE ingestion complete")
