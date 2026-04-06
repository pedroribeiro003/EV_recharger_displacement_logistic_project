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
        rows = []
        for m in municipalities:
            try:
                state_id = m["microrregiao"]["mesorregiao"]["UF"]["id"]
            except (KeyError, TypeError):
                logger.warning(
                    "IBGE: skipping municipality %s (%s) — missing UF nesting",
                    m.get("id"), m.get("nome"),
                )
                continue
            rows.append({"id": m["id"], "name": m["nome"], "state_id": state_id})
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

    def fetch_population_area(self) -> None:
        """Patch population (2025 estimate) and area_km2 (Censo 2022) via SIDRA v3."""
        logger.info("IBGE: fetching population estimates (2025)")
        resp_pop = self.client.get(
            "/v3/agregados/6579/periodos/2025/variaveis/9324",
            params={"localidades": "N6"},
        )
        resp_pop.raise_for_status()

        logger.info("IBGE: fetching area km2 (Censo 2022)")
        resp_area = self.client.get(
            "/v3/agregados/4714/periodos/2022/variaveis/6318",
            params={"localidades": "N6"},
        )
        resp_area.raise_for_status()

        def _parse(data: list, period: str) -> dict[int, float]:
            out: dict[int, float] = {}
            for var_block in data:
                for resultado in var_block.get("resultados", []):
                    for series in resultado.get("series", []):
                        loc_id = series["localidade"]["id"]
                        raw = series["serie"].get(period)
                        if raw and raw != "-":
                            try:
                                out[int(loc_id)] = float(str(raw).replace(",", "."))
                            except (ValueError, TypeError):
                                pass
            return out

        pop_by_id = _parse(resp_pop.json(), "2025")
        area_by_id = _parse(resp_area.json(), "2022")

        updated = 0
        for mun_id, pop in pop_by_id.items():
            self.session.execute(
                __import__("sqlalchemy").text(
                    "UPDATE ibge_municipalities SET population = :pop WHERE id = :id"
                ).bindparams(pop=int(pop), id=mun_id)
            )
            updated += 1

        for mun_id, area in area_by_id.items():
            self.session.execute(
                __import__("sqlalchemy").text(
                    "UPDATE ibge_municipalities SET area_km2 = :area WHERE id = :id"
                ).bindparams(area=area, id=mun_id)
            )

        self.session.commit()
        logger.info("IBGE: updated population for %d municipalities", updated)

    def run(self) -> None:
        self.fetch_states()
        self.fetch_municipalities()
        self.fetch_centroids()
        self.fetch_population_area()
        logger.info("IBGE ingestion complete")
