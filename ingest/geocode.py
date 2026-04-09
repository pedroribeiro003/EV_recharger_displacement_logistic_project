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

    def assign_municipalities_via_centroid(self, batch_size: int = 100) -> None:
        """Fallback KNN: assign municipality_id using nearest centroid (<-> operator).

        Processes stations in small batches to avoid connection timeouts on
        large datasets (2k+ stations × 5k+ municipalities).
        """
        logger.info("Geocode: assigning remaining stations via KNN centroid (batched)")

        # Fetch IDs of all stations that still need assignment
        pending_ids: list[int] = [
            row[0]
            for row in self.session.execute(text(
                "SELECT id FROM stations WHERE geom IS NOT NULL AND municipality_id IS NULL"
            )).fetchall()
        ]

        if not pending_ids:
            logger.info("Geocode: no stations pending KNN assignment")
            return

        logger.info("Geocode: %d stations to assign via KNN", len(pending_ids))
        total_updated = 0

        for i in range(0, len(pending_ids), batch_size):
            batch = pending_ids[i : i + batch_size]
            result = self.session.execute(
                text(
                    """
                    UPDATE stations s
                    SET municipality_id = sub.municipality_id
                    FROM (
                        SELECT DISTINCT ON (s.id)
                            s.id AS station_id,
                            m.id AS municipality_id
                        FROM stations s
                        JOIN ibge_municipalities m ON m.geom IS NOT NULL
                        WHERE s.id = ANY(:ids)
                          AND s.geom IS NOT NULL
                        ORDER BY s.id, s.geom <-> m.geom
                    ) sub
                    WHERE s.id = sub.station_id
                    """
                ),
                {"ids": batch},
            )
            self.session.commit()
            total_updated += result.rowcount
            logger.info(
                "Geocode: KNN batch %d/%d — %d assigned (total: %d)",
                i // batch_size + 1,
                -(-len(pending_ids) // batch_size),
                result.rowcount,
                total_updated,
            )

        logger.info("Geocode: assigned %d stations via KNN centroid", total_updated)

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
