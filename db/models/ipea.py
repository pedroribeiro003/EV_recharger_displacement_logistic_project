from typing import Optional
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class IpeaSeries(Base, TimestampMixin):
    __tablename__ = "ipea_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    values: Mapped[list["IpeaValue"]] = relationship(
        "IpeaValue", back_populates="series", cascade="all, delete-orphan"
    )


class IpeaValue(Base, TimestampMixin):
    __tablename__ = "ipea_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(
        Integer, __import__("sqlalchemy").ForeignKey("ipea_series.id"), nullable=False, index=True
    )
    municipality_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reference_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)

    series: Mapped["IpeaSeries"] = relationship("IpeaSeries", back_populates="values")
