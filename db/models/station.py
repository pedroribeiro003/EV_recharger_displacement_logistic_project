from typing import Optional, List

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class Station(Base, TimestampMixin):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    operator: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    state_uf: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    municipality_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ibge_municipalities.id"), nullable=True
    )
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    connectors: Mapped[List["Connector"]] = relationship(
        "Connector", back_populates="station", cascade="all, delete-orphan"
    )
    snapshots: Mapped[List["StatusSnapshot"]] = relationship(
        "StatusSnapshot", back_populates="station"
    )


class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stations.id"), nullable=False, index=True
    )
    connector_code: Mapped[str] = mapped_column(String(64), nullable=False)
    plug_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    power_kw: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    is_fast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    station: Mapped["Station"] = relationship("Station", back_populates="connectors")
