-- =============================================================
-- ev_charging — DDL completo
-- Schemas: staging | core | analytics
-- Requer: PostgreSQL 14+ com PostGIS e uuid-ossp
-- =============================================================

-- ── Extensões ─────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- full-text search em nomes

-- ── Schemas ───────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;


-- =============================================================
-- STAGING — dados brutos das APIs
-- =============================================================

CREATE TABLE IF NOT EXISTS staging.stg_municipalities (
    id                 bigserial PRIMARY KEY,
    source             varchar(64)  NOT NULL DEFAULT 'ibge',
    ibge_code          varchar(10)  NOT NULL,
    raw_json           jsonb        NOT NULL,
    name               text,
    state_code         char(2),
    region             varchar(32),
    ingested_at        timestamptz  NOT NULL DEFAULT now(),
    is_processed       boolean      NOT NULL DEFAULT false,
    CONSTRAINT uq_stg_mun_source_code UNIQUE (source, ibge_code)
);
CREATE INDEX IF NOT EXISTS idx_stg_mun_unprocessed
    ON staging.stg_municipalities (is_processed, ingested_at)
    WHERE is_processed = false;


CREATE TABLE IF NOT EXISTS staging.stg_charging_stations (
    id                 bigserial PRIMARY KEY,
    source_id          varchar(128) NOT NULL,
    source_name        varchar(64)  NOT NULL,
    raw_json           jsonb        NOT NULL,
    latitude           numeric(10,7),
    longitude          numeric(10,7),
    station_name       text,
    operator           text,
    country_code       char(3),
    status_type        varchar(64),
    ingested_at        timestamptz  NOT NULL DEFAULT now(),
    is_processed       boolean      NOT NULL DEFAULT false,
    CONSTRAINT uq_stg_station_source UNIQUE (source_name, source_id)
);
CREATE INDEX IF NOT EXISTS idx_stg_station_unprocessed
    ON staging.stg_charging_stations (is_processed, ingested_at)
    WHERE is_processed = false;


CREATE TABLE IF NOT EXISTS staging.stg_charging_sessions (
    id                 bigserial PRIMARY KEY,
    source_session_id  varchar(128) NOT NULL,
    source_name        varchar(64)  NOT NULL,
    raw_json           jsonb        NOT NULL,
    station_source_id  varchar(128),
    session_start      timestamptz,
    session_end        timestamptz,
    energy_kwh         numeric(10,3),
    peak_power_kw      numeric(8,2),
    ingested_at        timestamptz  NOT NULL DEFAULT now(),
    is_processed       boolean      NOT NULL DEFAULT false,
    CONSTRAINT uq_stg_session_source UNIQUE (source_name, source_session_id)
);
CREATE INDEX IF NOT EXISTS idx_stg_session_start
    ON staging.stg_charging_sessions (session_start DESC);
CREATE INDEX IF NOT EXISTS idx_stg_session_unprocessed
    ON staging.stg_charging_sessions (is_processed, ingested_at)
    WHERE is_processed = false;


CREATE TABLE IF NOT EXISTS staging.stg_vehicle_fleet (
    id                 bigserial PRIMARY KEY,
    source             varchar(64)  NOT NULL DEFAULT 'senatran',
    reference_month    date         NOT NULL,
    municipality_code  varchar(10)  NOT NULL,
    raw_json           jsonb        NOT NULL,
    total_ev           bigint,
    total_phev         bigint,
    total_vehicles     bigint,
    ingested_at        timestamptz  NOT NULL DEFAULT now(),
    is_processed       boolean      NOT NULL DEFAULT false,
    CONSTRAINT uq_stg_fleet_source_mun_month
        UNIQUE (source, municipality_code, reference_month)
);
CREATE INDEX IF NOT EXISTS idx_stg_fleet_mun
    ON staging.stg_vehicle_fleet (municipality_code, reference_month DESC);


CREATE TABLE IF NOT EXISTS staging.stg_energy_readings (
    id                 bigserial PRIMARY KEY,
    source             varchar(64)  NOT NULL,
    region_code        varchar(64)  NOT NULL,
    reading_at         timestamptz  NOT NULL,
    raw_json           jsonb        NOT NULL,
    load_mw            numeric(10,3),
    available_capacity_mw numeric(10,3),
    ingested_at        timestamptz  NOT NULL DEFAULT now(),
    is_processed       boolean      NOT NULL DEFAULT false,
    CONSTRAINT uq_stg_energy_source_region_ts
        UNIQUE (source, region_code, reading_at)
);
CREATE INDEX IF NOT EXISTS idx_stg_energy_ts
    ON staging.stg_energy_readings (reading_at DESC);


-- =============================================================
-- CORE — entidades limpas e normalizadas
-- =============================================================

CREATE TABLE IF NOT EXISTS core.municipalities (
    id               serial PRIMARY KEY,
    ibge_code        char(7)        NOT NULL,
    name             text           NOT NULL,
    state_code       char(2)        NOT NULL,
    region           varchar(32),
    population       bigint,
    area_km2         numeric(12,3),
    gdp_per_capita   numeric(12,2),
    urban_pop_pct    numeric(5,2),
    geometry         geometry(MultiPolygon,4326),
    centroid         geometry(Point,4326)
                     GENERATED ALWAYS AS (ST_Centroid(geometry)) STORED,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    CONSTRAINT uq_core_mun_ibge_code UNIQUE (ibge_code)
);
CREATE INDEX IF NOT EXISTS idx_core_mun_geometry
    ON core.municipalities USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_core_mun_centroid
    ON core.municipalities USING GIST (centroid);
CREATE INDEX IF NOT EXISTS idx_core_mun_state
    ON core.municipalities (state_code);


CREATE TABLE IF NOT EXISTS core.urban_zones (
    id                  serial PRIMARY KEY,
    municipality_id     int NOT NULL REFERENCES core.municipalities(id) ON DELETE CASCADE,
    zone_type           varchar(32)   NOT NULL
                        CHECK (zone_type IN ('residential','commercial','industrial','mixed')),
    poi_density         numeric(8,2),
    road_density_km     numeric(10,3),
    avg_daily_traffic   int,
    has_power_grid      boolean       DEFAULT true,
    grid_capacity_kva   numeric(10,2),
    geometry            geometry(Polygon,4326),
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_core_zone_geometry
    ON core.urban_zones USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_core_zone_mun
    ON core.urban_zones (municipality_id);


CREATE TABLE IF NOT EXISTS core.charging_stations (
    id                  serial PRIMARY KEY,
    municipality_id     int REFERENCES core.municipalities(id) ON DELETE SET NULL,
    external_id         varchar(128)  NOT NULL,
    source              varchar(64)   NOT NULL,
    name                text,
    operator            text,
    address             text,
    latitude            numeric(10,7),
    longitude           numeric(10,7),
    location            geometry(Point,4326)
                        GENERATED ALWAYS AS (
                            CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                            THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                            END
                        ) STORED,
    max_power_kw        numeric(8,2),
    num_connectors      smallint,
    is_public           boolean       NOT NULL DEFAULT true,
    is_operational      boolean       NOT NULL DEFAULT true,
    operational_since   date,
    grid_connection_kva numeric(10,2),
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT uq_core_station_source_ext UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_core_station_location
    ON core.charging_stations USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_core_station_mun
    ON core.charging_stations (municipality_id);
CREATE INDEX IF NOT EXISTS idx_core_station_operational
    ON core.charging_stations (is_operational)
    WHERE is_operational = true;


-- Sessões de carregamento — PARTICIONADA por mês
CREATE TABLE IF NOT EXISTS core.charging_sessions (
    id               bigserial,
    station_id       int           NOT NULL REFERENCES core.charging_stations(id) ON DELETE CASCADE,
    started_at       timestamptz   NOT NULL,
    ended_at         timestamptz,
    duration_minutes int,
    energy_kwh       numeric(10,3),
    peak_power_kw    numeric(8,2),
    avg_power_kw     numeric(8,2),
    vehicle_type     varchar(16)   CHECK (vehicle_type IN ('BEV','PHEV','HEV')),
    connector_type   varchar(32),
    hour_of_day      smallint      CHECK (hour_of_day BETWEEN 0 AND 23),
    day_of_week      smallint      CHECK (day_of_week BETWEEN 0 AND 6),
    is_weekend       boolean,
    month_of_year    smallint      CHECK (month_of_year BETWEEN 1 AND 12),
    created_at       timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (id, started_at)
) PARTITION BY RANGE (started_at);

-- Partições existentes — criar uma por mês conforme necessidade
-- Exemplo:
CREATE TABLE IF NOT EXISTS core.charging_sessions_2024_01
    PARTITION OF core.charging_sessions
    FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2024-02-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS core.charging_sessions_2024_02
    PARTITION OF core.charging_sessions
    FOR VALUES FROM ('2024-02-01 00:00:00+00') TO ('2024-03-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS core.charging_sessions_default
    PARTITION OF core.charging_sessions DEFAULT;

-- Índices são herdados automaticamente por todas as partições
CREATE INDEX IF NOT EXISTS idx_core_session_station_ts
    ON core.charging_sessions (station_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_core_session_hour_dow
    ON core.charging_sessions (hour_of_day, day_of_week);


CREATE TABLE IF NOT EXISTS core.vehicle_fleet_snapshots (
    id                  serial PRIMARY KEY,
    municipality_id     int           NOT NULL REFERENCES core.municipalities(id),
    reference_month     date          NOT NULL,
    total_ev            bigint        NOT NULL DEFAULT 0,
    total_phev          bigint        NOT NULL DEFAULT 0,
    total_hev           bigint        NOT NULL DEFAULT 0,
    total_vehicles      bigint        NOT NULL DEFAULT 0,
    ev_penetration_pct  numeric(6,4),
    yoy_ev_growth_pct   numeric(6,4),
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT uq_core_fleet_mun_month UNIQUE (municipality_id, reference_month)
);
CREATE INDEX IF NOT EXISTS idx_core_fleet_mun_month
    ON core.vehicle_fleet_snapshots (municipality_id, reference_month DESC);


CREATE TABLE IF NOT EXISTS core.energy_readings (
    id                      bigserial,
    municipality_id         int REFERENCES core.municipalities(id) ON DELETE SET NULL,
    source                  varchar(64)  NOT NULL,
    region_code             varchar(64)  NOT NULL,
    read_at                 timestamptz  NOT NULL,
    load_mw                 numeric(10,3),
    available_capacity_mw   numeric(10,3),
    voltage_kv              numeric(8,2),
    frequency_hz            numeric(6,3),
    load_pct                numeric(5,2),
    created_at              timestamptz  NOT NULL DEFAULT now(),
    updated_at              timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (id, read_at),
    CONSTRAINT uq_core_energy_source_region_ts
        UNIQUE (source, region_code, read_at)
) PARTITION BY RANGE (read_at);

CREATE TABLE IF NOT EXISTS core.energy_readings_2024_01
    PARTITION OF core.energy_readings
    FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2024-02-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS core.energy_readings_default
    PARTITION OF core.energy_readings DEFAULT;

CREATE INDEX IF NOT EXISTS idx_core_energy_region_ts
    ON core.energy_readings (region_code, read_at DESC);
CREATE INDEX IF NOT EXISTS idx_core_energy_mun_ts
    ON core.energy_readings (municipality_id, read_at DESC);


-- =============================================================
-- ANALYTICS — features pré-computadas para ML
-- =============================================================

CREATE TABLE IF NOT EXISTS analytics.station_hourly_demand (
    id                   bigserial PRIMARY KEY,
    station_id           int          NOT NULL REFERENCES core.charging_stations(id) ON DELETE CASCADE,
    hour_bucket          timestamptz  NOT NULL,
    session_count        int          NOT NULL DEFAULT 0,
    total_kwh            numeric(14,3),
    peak_kw              numeric(10,2),
    avg_kw               numeric(10,2),
    utilization_rate     numeric(6,4),
    hour_of_day          smallint,
    day_of_week          smallint,
    month_of_year        smallint,
    is_weekend           boolean,
    is_holiday           boolean,
    lag_1h_kwh           numeric(14,3),
    lag_24h_kwh          numeric(14,3),
    lag_168h_kwh         numeric(14,3),
    rolling_7d_avg_kwh   numeric(14,3),
    rolling_7d_std_kwh   numeric(14,3),
    rolling_30d_avg_kwh  numeric(14,3),
    computed_at          timestamptz  NOT NULL,
    created_at           timestamptz  NOT NULL DEFAULT now(),
    updated_at           timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_ana_shd_station_hour UNIQUE (station_id, hour_bucket)
);
CREATE INDEX IF NOT EXISTS idx_ana_shd_station_bucket
    ON analytics.station_hourly_demand (station_id, hour_bucket DESC);
CREATE INDEX IF NOT EXISTS idx_ana_shd_hour_dow
    ON analytics.station_hourly_demand (hour_of_day, day_of_week);


CREATE TABLE IF NOT EXISTS analytics.location_candidate_features (
    id                    bigserial PRIMARY KEY,
    municipality_id       int         NOT NULL REFERENCES core.municipalities(id) ON DELETE CASCADE,
    h3_index              varchar(20) UNIQUE,
    latitude              numeric(10,7),
    longitude             numeric(10,7),
    candidate_location    geometry(Point,4326)
                          GENERATED ALWAYS AS (
                              CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                              THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                              END
                          ) STORED,
    ev_density_5km        numeric(12,4),
    ev_count_5km          int,
    nearest_station_m     int,
    nearest_substation_m  int,
    stations_within_2km   int,
    stations_within_5km   int,
    zone_type             varchar(32),
    poi_density           numeric(10,4),
    road_density_km       numeric(10,4),
    traffic_score         numeric(6,4),
    grid_capacity_score   numeric(6,4),
    has_power_grid        boolean,
    demand_score          numeric(8,6),
    computed_at           timestamptz NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ana_lcf_location
    ON analytics.location_candidate_features USING GIST (candidate_location);
CREATE INDEX IF NOT EXISTS idx_ana_lcf_mun
    ON analytics.location_candidate_features (municipality_id);
CREATE INDEX IF NOT EXISTS idx_ana_lcf_score
    ON analytics.location_candidate_features (demand_score DESC NULLS LAST);


CREATE TABLE IF NOT EXISTS analytics.municipality_ev_features (
    id                   serial PRIMARY KEY,
    municipality_id      int         NOT NULL REFERENCES core.municipalities(id) ON DELETE CASCADE,
    reference_month      date        NOT NULL,
    ev_count             int,
    phev_count           int,
    ev_per_km2           numeric(12,6),
    ev_penetration_pct   numeric(6,4),
    yoy_ev_growth_pct    numeric(6,4),
    charger_count        int,
    connector_count      int,
    ev_per_charger       numeric(10,2),
    avg_session_kwh      numeric(10,3),
    avg_daily_sessions   numeric(10,2),
    peak_hour            smallint,
    peak_day_of_week     smallint,
    computed_at          timestamptz NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ana_mef_mun_month UNIQUE (municipality_id, reference_month)
);
CREATE INDEX IF NOT EXISTS idx_ana_mef_mun_month
    ON analytics.municipality_ev_features (municipality_id, reference_month DESC);


CREATE TABLE IF NOT EXISTS analytics.ml_demand_predictions (
    id              bigserial PRIMARY KEY,
    station_id      int          NOT NULL REFERENCES core.charging_stations(id) ON DELETE CASCADE,
    predicted_for   timestamptz  NOT NULL,
    predicted_kwh   numeric(14,3) NOT NULL,
    confidence_low  numeric(14,3),
    confidence_high numeric(14,3),
    model_version   varchar(64)  NOT NULL,
    predicted_at    timestamptz  NOT NULL DEFAULT now(),
    actual_kwh      numeric(14,3),
    CONSTRAINT uq_ana_pred_station_hour_model
        UNIQUE (station_id, predicted_for, model_version)
);
CREATE INDEX IF NOT EXISTS idx_ana_pred_station_ts
    ON analytics.ml_demand_predictions (station_id, predicted_for DESC);
CREATE INDEX IF NOT EXISTS idx_ana_pred_model
    ON analytics.ml_demand_predictions (model_version, predicted_for DESC);


-- =============================================================
-- VIEWS ANALÍTICAS úteis
-- =============================================================

CREATE OR REPLACE VIEW analytics.v_stations_demand_summary AS
SELECT
    s.id               AS station_id,
    s.name             AS station_name,
    m.ibge_code,
    m.name             AS municipality,
    m.state_code,
    AVG(shd.total_kwh) AS avg_hourly_kwh,
    MAX(shd.peak_kw)   AS max_peak_kw,
    SUM(shd.session_count) AS total_sessions,
    COUNT(DISTINCT shd.hour_bucket::date) AS days_with_data
FROM core.charging_stations s
JOIN analytics.station_hourly_demand shd ON shd.station_id = s.id
LEFT JOIN core.municipalities m ON m.id = s.municipality_id
GROUP BY s.id, s.name, m.ibge_code, m.name, m.state_code;


CREATE OR REPLACE VIEW analytics.v_top_candidate_locations AS
SELECT
    lcf.id,
    lcf.h3_index,
    lcf.latitude,
    lcf.longitude,
    m.name  AS municipality,
    m.state_code,
    lcf.ev_density_5km,
    lcf.nearest_station_m,
    lcf.stations_within_5km,
    lcf.grid_capacity_score,
    lcf.demand_score,
    lcf.computed_at
FROM analytics.location_candidate_features lcf
JOIN core.municipalities m ON m.id = lcf.municipality_id
WHERE lcf.demand_score IS NOT NULL
ORDER BY lcf.demand_score DESC;
