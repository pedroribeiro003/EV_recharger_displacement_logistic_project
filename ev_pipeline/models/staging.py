"""
models/staging.py
Models SQLAlchemy para o schema STAGING.
Recebem dados brutos das APIs sem transformação.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from config.settings import get_settings
from models.base import Base, StagingMixin

settings = get_settings()
SCHEMA = settings.db_schema_staging


class StgChargingStation(Base, StagingMixin):
    """
    Dados brutos de estações de carregamento.
    Fontes: Open Charge Map, PlugShare, operadores.
    """

    __tablename__ = "stg_charging_stations"
    __table_args__ = (
        UniqueConstraint("source_name", "source_id", name="uq_stg_station_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="open_charge_map | plugshare | ev_network",
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Payload completo da API para reprocessamento",
    )
    # Campos extraídos antecipadamente para facilitar deduplicação
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    station_name: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(3))
    status_type: Mapped[str | None] = mapped_column(String(64))

    def __repr__(self) -> str:
        return f"<StgChargingStation source={self.source_name} id={self.source_id}>"


class StgChargingSession(Base, StagingMixin):
    """
    Dados brutos de sessões de carregamento.
    Fontes: ACN-Data, ChargePoint, EV Connect.
    """

    __tablename__ = "stg_charging_sessions"
    __table_args__ = (
        UniqueConstraint("source_name", "source_session_id", name="uq_stg_session_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="acn_data | chargepoint | ev_connect",
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    station_source_id: Mapped[str | None] = mapped_column(
        String(128),
        comment="ID da estação no sistema de origem",
    )
    session_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    session_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    energy_kwh: Mapped[float | None] = mapped_column(Numeric(10, 3))
    peak_power_kw: Mapped[float | None] = mapped_column(Numeric(8, 2))

    def __repr__(self) -> str:
        return f"<StgChargingSession source={self.source_name} id={self.source_session_id}>"


class StgVehicleFleet(Base, StagingMixin):
    """
    Dados brutos de frota de veículos elétricos por município.
    Fontes: SENATRAN, DENATRAN.
    """

    __tablename__ = "stg_vehicle_fleet"
    __table_args__ = (
        UniqueConstraint(
            "source", "municipality_code", "reference_month",
            name="uq_stg_fleet_source_mun_month",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="senatran | denatran | antt",
    )
    reference_month: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        comment="Primeiro dia do mês de competência (AAAA-MM-01)",
        index=True,
    )
    municipality_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Código IBGE do município (7 dígitos)",
        index=True,
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Campos pré-extraídos
    total_ev: Mapped[int | None] = mapped_column(BigInteger)
    total_phev: Mapped[int | None] = mapped_column(BigInteger)
    total_vehicles: Mapped[int | None] = mapped_column(BigInteger)

    def __repr__(self) -> str:
        return (
            f"<StgVehicleFleet source={self.source} "
            f"mun={self.municipality_code} month={self.reference_month:%Y-%m}>"
        )


class StgEnergyReading(Base, StagingMixin):
    """
    Leituras brutas de carga da rede elétrica.
    Fontes: ONS, ANEEL, concessionárias.
    """

    __tablename__ = "stg_energy_readings"
    __table_args__ = (
        UniqueConstraint(
            "source", "region_code", "reading_at",
            name="uq_stg_energy_source_region_ts",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="ons | aneel | concessionaria",
    )
    region_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Código da subestação ou setor elétrico",
        index=True,
    )
    reading_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp da leitura",
        index=True,
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Campos pré-extraídos
    load_mw: Mapped[float | None] = mapped_column(Numeric(10, 3))
    available_capacity_mw: Mapped[float | None] = mapped_column(Numeric(10, 3))

    def __repr__(self) -> str:
        return (
            f"<StgEnergyReading source={self.source} "
            f"region={self.region_code} at={self.reading_at}>"
        )


class StgMunicipality(Base, StagingMixin):
    """
    Dados brutos de municípios brasileiros.
    Fonte: API IBGE.
    """

    __tablename__ = "stg_municipalities"
    __table_args__ = (
        UniqueConstraint("source", "ibge_code", name="uq_stg_mun_source_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="ibge")
    ibge_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(32))

    def __repr__(self) -> str:
        return f"<StgMunicipality ibge={self.ibge_code} name={self.name}>"
