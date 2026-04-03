from typing import Optional, List

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class IbgeState(Base, TimestampMixin):
    __tablename__ = "ibge_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # código IBGE
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), unique=True, nullable=False)
    region: Mapped[str] = mapped_column(String(20), nullable=False)

    municipalities: Mapped[List["IbgeMunicipality"]] = relationship(
        "IbgeMunicipality", back_populates="state"
    )


class IbgeMunicipality(Base, TimestampMixin):
    __tablename__ = "ibge_municipalities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # código IBGE 7 dígitos
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    state_id: Mapped[int] = mapped_column(Integer, ForeignKey("ibge_states.id"), nullable=False)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    population: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    area_km2: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    idhm: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    gdp_per_capita: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    state: Mapped["IbgeState"] = relationship("IbgeState", back_populates="municipalities")
    boundary: Mapped[Optional["IbgeMunicipalityBoundary"]] = relationship(
        "IbgeMunicipalityBoundary", back_populates="municipality", uselist=False
    )


class IbgeMunicipalityBoundary(Base, TimestampMixin):
    __tablename__ = "ibge_municipality_boundaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ibge_municipalities.id"), unique=True, nullable=False
    )
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=True
    )

    municipality: Mapped["IbgeMunicipality"] = relationship(
        "IbgeMunicipality", back_populates="boundary"
    )
