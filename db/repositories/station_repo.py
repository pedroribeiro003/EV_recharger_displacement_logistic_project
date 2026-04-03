from typing import Optional, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models.station import Station, Connector
from db.models.senatran import SenatranFleet
from db.repositories.base import BaseRepository


class StationRepository(BaseRepository[Station]):
    model = Station

    def get_by_station_code(self, code: str) -> Optional[Station]:
        stmt = select(Station).where(Station.station_code == code)
        return self.session.scalars(stmt).first()

    def get_by_municipality(self, municipality_id: int) -> Sequence[Station]:
        stmt = select(Station).where(Station.municipality_id == municipality_id)
        return self.session.scalars(stmt).all()

    def get_nearby(
        self,
        lat: float,
        lng: float,
        radius_km: float,
        only_public: bool = True,
    ) -> Sequence[Station]:
        """Return stations within radius_km using PostGIS ST_DWithin (geography)."""
        radius_m = radius_km * 1000
        stmt = select(Station).where(
            text(
                "ST_DWithin("
                "  geom::geography,"
                "  ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,"
                "  :radius_m"
                ")"
            ).bindparams(lat=lat, lng=lng, radius_m=radius_m)
        )
        if only_public:
            stmt = stmt.where(Station.is_public.is_(True))
        return self.session.scalars(stmt).all()

    def upsert(self, data: dict) -> Station:
        """INSERT ... ON CONFLICT (station_code) DO UPDATE."""
        stmt = (
            pg_insert(Station)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["station_code"],
                set_={k: v for k, v in data.items() if k != "station_code"},
            )
            .returning(Station)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.scalars().first()

    def bulk_upsert(self, records: list[dict]) -> int:
        """Batch upsert using a single executemany-style statement."""
        if not records:
            return 0
        stmt = pg_insert(Station).on_conflict_do_update(
            index_elements=["station_code"],
            set_={
                k: pg_insert(Station).excluded[k]
                for k in records[0]
                if k != "station_code"
            },
        )
        self.session.execute(stmt, records)
        self.session.flush()
        return len(records)

    def get_with_low_coverage(self, evs_per_charger_min: int = 10) -> Sequence[Station]:
        """Stations whose municipality has >= evs_per_charger_min EVs per public charger."""
        ev_count = (
            select(
                SenatranFleet.municipality_id,
                func.sum(SenatranFleet.ev_count).label("total_evs"),
            )
            .where(SenatranFleet.ev_count.isnot(None))
            .group_by(SenatranFleet.municipality_id)
            .subquery()
        )
        charger_count = (
            select(
                Station.municipality_id,
                func.count(Station.id).label("charger_count"),
            )
            .where(Station.is_public.is_(True))
            .group_by(Station.municipality_id)
            .subquery()
        )
        ratio = (
            select(
                ev_count.c.municipality_id,
                (ev_count.c.total_evs / func.nullif(charger_count.c.charger_count, 0)).label(
                    "ratio"
                ),
            )
            .join(charger_count, ev_count.c.municipality_id == charger_count.c.municipality_id)
            .subquery()
        )
        stmt = (
            select(Station)
            .join(ratio, Station.municipality_id == ratio.c.municipality_id)
            .where(ratio.c.ratio >= evs_per_charger_min)
        )
        return self.session.scalars(stmt).all()
