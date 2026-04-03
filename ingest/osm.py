import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.config import settings
from core.logging import get_logger
from db.models.osm import OsmPoi, StationPoiDistance

logger = get_logger(__name__)

# Overpass QL queries per category — bounding box covers Brazil
_BR_BBOX = "(-33.75,-73.99,5.27,-28.85)"

QUERIES: dict[str, str] = {
    "shopping_mall": f"""
        [out:json][timeout:180];
        (
          node["shop"="mall"]{_BR_BBOX};
          way["shop"="mall"]{_BR_BBOX};
          relation["shop"="mall"]{_BR_BBOX};
        );
        out center;
    """,
    "hotel": f"""
        [out:json][timeout:180];
        (
          node["tourism"="hotel"]{_BR_BBOX};
          way["tourism"="hotel"]{_BR_BBOX};
        );
        out center;
    """,
    "fuel": f"""
        [out:json][timeout:180];
        (
          node["amenity"="fuel"]{_BR_BBOX};
          way["amenity"="fuel"]{_BR_BBOX};
        );
        out center;
    """,
    "hospital": f"""
        [out:json][timeout:180];
        (
          node["amenity"="hospital"]{_BR_BBOX};
          way["amenity"="hospital"]{_BR_BBOX};
        );
        out center;
    """,
    "aerodrome": f"""
        [out:json][timeout:180];
        (
          node["aeroway"="aerodrome"]{_BR_BBOX};
          way["aeroway"="aerodrome"]{_BR_BBOX};
        );
        out center;
    """,
    "supermarket": f"""
        [out:json][timeout:180];
        (
          node["shop"="supermarket"]{_BR_BBOX};
          way["shop"="supermarket"]{_BR_BBOX};
        );
        out center;
    """,
    "workplace": f"""
        [out:json][timeout:180];
        (
          node["office"]{_BR_BBOX};
          way["office"]{_BR_BBOX};
        );
        out center;
    """,
}


class OsmIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(timeout=300)

    def fetch_pois(self, category: str, query: str) -> list[dict]:
        logger.info("OSM: fetching category=%s", category)
        resp = self.client.post(settings.overpass_url, data={"data": query})
        resp.raise_for_status()
        elements = resp.json().get("elements", [])

        pois = []
        for el in elements:
            tags = el.get("tags") or {}
            # Overpass returns center for ways/relations
            center = el.get("center") or {}
            lat = el.get("lat") or center.get("lat")
            lng = el.get("lon") or center.get("lon")
            if lat is None or lng is None:
                continue
            pois.append(
                {
                    "osm_id": el["id"],
                    "osm_type": el["type"],
                    "name": tags.get("name"),
                    "category": category,
                    "amenity": tags.get("amenity") or tags.get("shop") or tags.get("tourism"),
                    "lat": float(lat),
                    "lng": float(lng),
                }
            )
        logger.info("OSM: %d POIs for category=%s", len(pois), category)
        return pois

    def _upsert_pois(self, pois: list[dict]) -> None:
        if not pois:
            return
        stmt = pg_insert(OsmPoi).on_conflict_do_update(
            index_elements=["osm_id"],
            set_={k: pg_insert(OsmPoi).excluded[k] for k in pois[0] if k != "osm_id"},
        )
        batch_size = 500
        for i in range(0, len(pois), batch_size):
            self.session.execute(stmt, pois[i : i + batch_size])
        self.session.flush()

    def _update_geom(self, category: str) -> None:
        self.session.execute(
            text(
                "UPDATE osm_pois "
                "SET geom = ST_SetSRID(ST_MakePoint(lng, lat), 4326) "
                "WHERE category = :cat AND lat IS NOT NULL AND lng IS NOT NULL"
            ).bindparams(cat=category)
        )

    def _calculate_distances(self) -> None:
        """INSERT station_poi_distances using PostGIS lateral join."""
        logger.info("OSM: calculating station→POI distances")
        sql = text(
            """
            INSERT INTO station_poi_distances (station_id, category, nearest_poi_id, distance_m)
            SELECT
                s.id          AS station_id,
                p.category,
                p.osm_id      AS nearest_poi_id,
                ST_Distance(s.geom::geography, p.geom::geography) AS distance_m
            FROM stations s
            CROSS JOIN LATERAL (
                SELECT osm_id, category, geom
                FROM osm_pois op
                WHERE op.geom IS NOT NULL
                  AND s.geom IS NOT NULL
                ORDER BY s.geom <-> op.geom
                LIMIT 1
            ) p
            ON CONFLICT (station_id, category) DO UPDATE
                SET nearest_poi_id = EXCLUDED.nearest_poi_id,
                    distance_m     = EXCLUDED.distance_m
            """
        )
        self.session.execute(sql)
        self.session.commit()
        logger.info("OSM: distance calculation complete")

    def run(self) -> None:
        for category, query in QUERIES.items():
            try:
                pois = self.fetch_pois(category, query)
                self._upsert_pois(pois)
                self._update_geom(category)
                self.session.commit()
            except Exception as exc:
                logger.exception("OSM: error processing %s: %s", category, exc)
                self.session.rollback()

        self._calculate_distances()
        logger.info("OSM ingestion complete")
