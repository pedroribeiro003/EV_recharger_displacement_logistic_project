"""
models/analytics.py
Models SQLAlchemy para o schema ANALYTICS.
Tabelas derivadas com features pré-computadas para ML.
Alimentadas por jobs ETL sobre o schema core.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.settings import get_settings
from models.base import Base, TimestampMixin

settings = get_settings()
SCHEMA = settings.db_schema_analytics
CORE = settings.db_schema_core


class StationHourlyDemand(Base, TimestampMixin):
    """
    Série temporal horária agregada por estação.
    Principal tabela de features para previsão de demanda.

    Atualizada por job ETL a cada hora.
    Inclui lag features pré-computadas para evitar data leakage em treino.
    """

    __tablename__ = "station_hourly_demand"
    __table_args__ = (
        UniqueConstraint("station_id", "hour_bucket", name="uq_ana_shd_station_hour"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CORE}.charging_stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hour_bucket: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Hora truncada — date_trunc('hour', started_at)",
    )

    # ── Métricas do bucket ─────────────────────────────────
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    peak_kw: Mapped[float | None] = mapped_column(Numeric(10, 2))
    avg_kw: Mapped[float | None] = mapped_column(Numeric(10, 2))
    utilization_rate: Mapped[float | None] = mapped_column(
        Numeric(6, 4),
        comment="session_count / num_connectors — proxy de saturação",
    )

    # ── Features temporais (ML) ────────────────────────────
    hour_of_day: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    month_of_year: Mapped[int | None] = mapped_column(SmallInteger)
    is_weekend: Mapped[bool | None] = mapped_column(Boolean)
    is_holiday: Mapped[bool | None] = mapped_column(
        Boolean,
        comment="Feriado nacional ou municipal",
    )

    # ── Lag features (ML) — calculadas com dados ANTERIORES ao bucket ──
    lag_1h_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    lag_24h_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    lag_168h_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    rolling_7d_avg_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    rolling_7d_std_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))
    rolling_30d_avg_kwh: Mapped[float | None] = mapped_column(Numeric(14, 3))

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Momento em que as features foram calculadas — controle de leakage",
    )

    def __repr__(self) -> str:
        return (
            f"<StationHourlyDemand station={self.station_id} "
            f"hour={self.hour_bucket} kwh={self.total_kwh}>"
        )


class LocationCandidateFeatures(Base, TimestampMixin):
    """
    Features de pontos candidatos para instalação de estações.
    Alimenta o modelo de otimização de localização.
    Grade gerada via H3 ou OSM sobre municípios selecionados.
    """

    __tablename__ = "location_candidate_features"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CORE}.municipalities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    h3_index: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        comment="Índice H3 res=9 do ponto candidato (~174m de lado)",
    )
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))

    # ── Features espaciais (ML) ────────────────────────────
    ev_density_5km: Mapped[float | None] = mapped_column(
        Numeric(12, 4),
        comment="EVs / km² no raio de 5 km",
    )
    ev_count_5km: Mapped[int | None] = mapped_column(Integer)
    nearest_station_m: Mapped[int | None] = mapped_column(
        Integer,
        comment="Distância até estação operacional mais próxima (metros)",
    )
    nearest_substation_m: Mapped[int | None] = mapped_column(
        Integer,
        comment="Distância até subestação — proxy de custo de conexão",
    )
    stations_within_2km: Mapped[int | None] = mapped_column(
        Integer,
        comment="Saturação do entorno imediato",
    )
    stations_within_5km: Mapped[int | None] = mapped_column(Integer)

    # ── Features urbanas (ML) ─────────────────────────────
    zone_type: Mapped[str | None] = mapped_column(
        String(32),
        comment="residential | commercial | industrial | mixed",
    )
    poi_density: Mapped[float | None] = mapped_column(Numeric(10, 4))
    road_density_km: Mapped[float | None] = mapped_column(Numeric(10, 4))
    traffic_score: Mapped[float | None] = mapped_column(Numeric(6, 4))

    # ── Features de rede (ML) ─────────────────────────────
    grid_capacity_score: Mapped[float | None] = mapped_column(
        Numeric(6, 4),
        comment="Capacidade residual normalizada [0,1]",
    )
    has_power_grid: Mapped[bool | None] = mapped_column(Boolean)

    # ── Score composto ─────────────────────────────────────
    demand_score: Mapped[float | None] = mapped_column(
        Numeric(8, 6),
        comment="Score de demanda estimada — variável alvo para ranking de localização",
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<LocationCandidateFeatures h3={self.h3_index} "
            f"score={self.demand_score}>"
        )


class MunicipalityEVFeatures(Base, TimestampMixin):
    """
    Features mensais por município para o modelo de localização.
    Série temporal que combina frota + estações + sessões.
    """

    __tablename__ = "municipality_ev_features"
    __table_args__ = (
        UniqueConstraint(
            "municipality_id", "reference_month",
            name="uq_ana_mef_mun_month",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CORE}.municipalities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference_month: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        index=True,
    )

    # Frota
    ev_count: Mapped[int | None] = mapped_column(Integer)
    phev_count: Mapped[int | None] = mapped_column(Integer)
    ev_per_km2: Mapped[float | None] = mapped_column(Numeric(12, 6))
    ev_penetration_pct: Mapped[float | None] = mapped_column(Numeric(6, 4))
    yoy_ev_growth_pct: Mapped[float | None] = mapped_column(Numeric(6, 4))

    # Infraestrutura
    charger_count: Mapped[int | None] = mapped_column(Integer)
    connector_count: Mapped[int | None] = mapped_column(Integer)
    ev_per_charger: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        comment="Ratio de saturação — EVs / ponto de carregamento",
    )

    # Demanda histórica
    avg_session_kwh: Mapped[float | None] = mapped_column(Numeric(10, 3))
    avg_daily_sessions: Mapped[float | None] = mapped_column(Numeric(10, 2))
    peak_hour: Mapped[int | None] = mapped_column(
        SmallInteger,
        comment="Hora do pico histórico de demanda (0-23)",
    )
    peak_day_of_week: Mapped[int | None] = mapped_column(SmallInteger)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MunicipalityEVFeatures mun={self.municipality_id} "
            f"month={self.reference_month:%Y-%m} evs={self.ev_count}>"
        )


class MLDemandPrediction(Base):
    """
    Predições de demanda armazenadas — rastreabilidade de modelos.
    Permite comparar versões de modelos e calcular métricas offline.
    """

    __tablename__ = "ml_demand_predictions"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "predicted_for", "model_version",
            name="uq_ana_pred_station_hour_model",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CORE}.charging_stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    predicted_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Hora para a qual a demanda foi prevista",
    )
    predicted_kwh: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    confidence_low: Mapped[float | None] = mapped_column(Numeric(14, 3))
    confidence_high: Mapped[float | None] = mapped_column(Numeric(14, 3))
    model_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="ex: xgb-v2.1.3 | lstm-v1.0.0",
    )
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Quando a predição foi gerada",
    )
    actual_kwh: Mapped[float | None] = mapped_column(
        Numeric(14, 3),
        comment="Valor real — preenchido a posteriori para cálculo de erro",
    )

    def __repr__(self) -> str:
        return (
            f"<MLDemandPrediction station={self.station_id} "
            f"for={self.predicted_for} model={self.model_version}>"
        )
