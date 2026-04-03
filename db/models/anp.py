from typing import Optional
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class AnpGasStation(Base, TimestampMixin):
    __tablename__ = "anp_gas_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cnpj: Mapped[str] = mapped_column(String(18), unique=True, nullable=False, index=True)
    trade_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    municipality_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    state_uf: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    has_ev_charger: Mapped[bool] = mapped_column(
        __import__("sqlalchemy").Boolean, default=False, nullable=False
    )

    fuel_prices: Mapped[list["AnpFuelPrice"]] = relationship(
        "AnpFuelPrice", back_populates="gas_station"
    )


class AnpFuelPrice(Base, TimestampMixin):
    __tablename__ = "anp_fuel_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gas_station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anp_gas_stations.id"), nullable=False, index=True
    )
    fuel_type: Mapped[str] = mapped_column(String(80), nullable=False)
    price_brl: Mapped[Optional[float]] = mapped_column(Numeric(10, 3), nullable=True)
    survey_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    gas_station: Mapped["AnpGasStation"] = relationship(
        "AnpGasStation", back_populates="fuel_prices"
    )
