from typing import Optional

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin


class SenatranFleet(Base, TimestampMixin):
    __tablename__ = "senatran_fleet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fuel_type: Mapped[str] = mapped_column(String(50), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(80), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ev_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hybrid_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
