from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class OsmPoi(Base, TimestampMixin):
    __tablename__ = "osm_pois"

    osm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    osm_type: Mapped[str] = mapped_column(String(10), nullable=False)  # node / way / relation
    name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    amenity: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    municipality_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ibge_municipalities.id"), nullable=True
    )

    __table_args__ = (
        Index("ix_osm_pois_geom", "geom", postgresql_using="gist"),
        Index("ix_osm_pois_category", "category"),
    )


class StationPoiDistance(Base):
    """Pre-computed distance from each station to the nearest POI per category."""

    __tablename__ = "station_poi_distances"

    station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stations.id"), primary_key=True, nullable=False
    )
    category: Mapped[str] = mapped_column(String(80), primary_key=True, nullable=False)
    nearest_poi_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("osm_pois.osm_id"), nullable=True
    )
    distance_m: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)

    __table_args__ = (Index("ix_station_poi_distances_station_id", "station_id"),)
