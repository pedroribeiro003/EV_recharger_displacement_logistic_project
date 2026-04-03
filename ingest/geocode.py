from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging import get_logger

logger = get_logger(__name__)


class GeocodeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def update_station_geoms(self) -> None:
        """Set geom = ST_MakePoint(lng, lat) for stations missing geometry."""
        logger.info("Geocode: updating station geometries from lat/lng")
        result = self.session.execute(
            text(
                "UPDATE stations "
                "SET geom = ST_SetSRID(ST_MakePoint(lng, lat), 4326) "
                "WHERE lat IS NOT NULL AND lng IS NOT NULL AND geom IS NULL"
            )
        )
        self.session.commit()
        logger.info("Geocode: updated %d station geoms", result.rowcount)

    def assign_municipalities_via_polygon(self) -> None:
        """Spatial join: assign municipality_id using boundary polygons."""
        logger.info("Geocode: assigning municipalities via polygon containment")
        sql = text(
            """
            UPDATE stations s
            SET municipality_id = b.municipality_id
            FROM ibge_municipality_boundaries b
            WHERE s.geom IS NOT NULL
              AND b.geom IS NOT NULL
              AND ST_Within(s.geom, b.geom)
              AND s.municipality_id IS NULL
            """
        )
        result = self.session.execute(sql)
        self.session.commit()
        logger.info("Geocode: assigned %d stations via polygon", result.rowcount)

    def assign_municipalities_via_centroid(self) -> None:
        """Fallback KNN: assign municipality_id using nearest centroid (<-> operator)."""
        logger.info("Geocode: assigning remaining stations via KNN centroid")
        sql = text(
            """
            UPDATE stations s
            SET municipality_id = sub.id
            FROM LATERAL (
                SELECT m.id
                FROM ibge_municipalities m
                WHERE m.geom IS NOT NULL
                ORDER BY s.geom <-> m.geom
                LIMIT 1
            ) sub
            WHERE s.geom IS NOT NULL
              AND s.municipality_id IS NULL
            """
        )
        result = self.session.execute(sql)
        self.session.commit()
        logger.info("Geocode: assigned %d stations via KNN centroid", result.rowcount)

    def calculate_poi_distances(self) -> None:
        """Compute station→POI distances via lateral join and upsert."""
        logger.info("Geocode: calculating POI distances")
        sql = text(
            """
            INSERT INTO station_poi_distances (station_id, category, nearest_poi_id, distance_m)
            SELECT
                s.id,
                nearest.category,
                nearest.osm_id,
                ST_Distance(s.geom::geography, nearest.geom::geography)
            FROM stations s
            CROSS JOIN LATERAL (
                SELECT op.osm_id, op.category, op.geom
                FROM osm_pois op
                WHERE op.geom IS NOT NULL
                  AND s.geom IS NOT NULL
                ORDER BY s.geom <-> op.geom
                LIMIT 1
            ) nearest
            WHERE s.geom IS NOT NULL
            ON CONFLICT (station_id, category) DO UPDATE
                SET nearest_poi_id = EXCLUDED.nearest_poi_id,
                    distance_m     = EXCLUDED.distance_m
            """
        )
        self.session.execute(sql)
        self.session.commit()
        logger.info("Geocode: POI distances calculated")

    def run(self) -> None:
        self.update_station_geoms()
        self.assign_municipalities_via_polygon()
        self.assign_municipalities_via_centroid()
        self.calculate_poi_distances()
        logger.info("Geocode service complete")
