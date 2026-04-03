from typing import Optional
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class AneelDistributor(Base, TimestampMixin):
    __tablename__ = "aneel_distributors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state_uf: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    concession_area: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tariffs: Mapped[list["AneelTariff"]] = relationship(
        "AneelTariff", back_populates="distributor"
    )
    outages: Mapped[list["AneelOutage"]] = relationship(
        "AneelOutage", back_populates="distributor"
    )


class AneelTariff(Base, TimestampMixin):
    __tablename__ = "aneel_tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distributor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("aneel_distributors.id"), nullable=False, index=True
    )
    tariff_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tariff_subgroup: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    supply_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    te_kwh: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)
    tusd_kwh: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    distributor: Mapped["AneelDistributor"] = relationship(
        "AneelDistributor", back_populates="tariffs"
    )


class AneelOutage(Base, TimestampMixin):
    __tablename__ = "aneel_outages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distributor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("aneel_distributors.id"), nullable=False, index=True
    )
    municipality_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outage_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    outage_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    affected_consumers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cause: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    distributor: Mapped["AneelDistributor"] = relationship(
        "AneelDistributor", back_populates="outages"
    )
