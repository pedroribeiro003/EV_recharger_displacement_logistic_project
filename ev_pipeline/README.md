# EV Charging — Data Pipeline

Pipeline de dados brutos para análise de infraestrutura de carregamento de veículos elétricos.
Arquitetura em três camadas: **staging → core → analytics**.

---

## Estrutura do projeto

```
ev_pipeline/
├── .env.example                   # Template de variáveis de ambiente
├── requirements.txt
├── ddl.sql                        # DDL completo com particionamento e índices
│
├── config/
│   ├── settings.py                # Carrega .env via pydantic-settings
│   └── database.py                # Engine SQLAlchemy + session factory
│
├── models/
│   ├── base.py                    # DeclarativeBase + TimestampMixin + StagingMixin
│   ├── staging.py                 # Models das tabelas staging.*
│   ├── core.py                    # Models das tabelas core.*
│   └── analytics.py               # Models das tabelas analytics.*
│
├── pipeline/
│   ├── base_ingester.py           # Classe base com contrato fetch→parse→upsert
│   ├── orchestrator.py            # Orquestrador de ingestão (staging)
│   ├── etl.py                     # Orquestrador completo (ingest + transform + features)
│   │
│   ├── sources/                   # Ingestores por fonte
│   │   ├── open_charge_map.py     # API Open Charge Map
│   │   ├── ibge.py                # API IBGE (municípios)
│   │   ├── acn_data.py            # ACN-Data (sessões) — API + CSV
│   │   ├── senatran.py            # SENATRAN (frota EV) — CSV
│   │   └── energy.py              # ONS/ANEEL (rede elétrica) — CSV
│   │
│   └── transforms/                # ETL staging → core → analytics
│       ├── municipalities.py
│       ├── stations.py
│       ├── sessions.py
│       └── features.py            # Computa lag features para ML
│
└── utils/
    ├── logger.py
    └── http_client.py
```

---

## Setup

### 1. Instalar dependências

```bash
pip install -r requirements.txt
```

### 2. Configurar o banco

```bash
cp .env.example .env
# Editar .env com suas credenciais
```

Criar o banco no PostgreSQL:

```sql
CREATE DATABASE ev_charging;
CREATE USER ev_user WITH PASSWORD 'sua_senha';
GRANT ALL PRIVILEGES ON DATABASE ev_charging TO ev_user;
```

Executar o DDL completo:

```bash
psql -U ev_user -d ev_charging -f ddl.sql
```

### 3. Instalar PostGIS (Ubuntu/Debian)

```bash
sudo apt install postgresql-15-postgis-3
```

---

## Uso

### ETL completo (ingest + transform + features)

```bash
# Todas as fontes que têm acesso via API (OCM + IBGE)
python -m pipeline.etl --full

# Com arquivos CSV locais
python -m pipeline.etl --full \
  --senatran-csv /data/frota_2024_01.csv --month 2024-01 \
  --energy-csv /data/ons_carga_2024.csv --energy-region SE \
  --acn-csv /data/acn_sessions.csv
```

### Apenas ingestão (staging)

```bash
# Todas as fontes de API
python -m pipeline.orchestrator --all

# Fonte específica
python -m pipeline.orchestrator --source ocm --ocm-max 10000
python -m pipeline.orchestrator --source ibge --state SP
python -m pipeline.orchestrator --source senatran \
    --senatran-csv /data/frota_2024_01.csv --month 2024-01
python -m pipeline.orchestrator --source energy \
    --energy-csv /data/ons.csv --energy-region SECO
```

### Apenas transforms (staging → core)

```bash
python -m pipeline.etl --step transform
```

### Apenas features (core → analytics)

```bash
# Recomputa últimas 72 horas
python -m pipeline.etl --step features --hours 72
```

---

## Fluxo de dados

```
APIs externas / CSVs
        │
        ▼
[staging.*]          ← dados brutos, raw_json preservado, upsert idempotente
        │
        ▼ transforms/
[core.*]             ← entidades normalizadas, FK, PostGIS geometry
        │
        ▼ features/
[analytics.*]        ← features ML pré-computadas, lag features, scores
```

### Controle de pipeline

Cada tabela staging tem `is_processed` (bool). Os transforms:
1. Leem apenas registros com `is_processed = false`
2. Fazem upsert no core
3. Marcam `is_processed = true`

Isso permite reprocessamento seguro: basta setar `is_processed = false` no staging.

```sql
-- Reprocessar todas as estações
UPDATE staging.stg_charging_stations SET is_processed = false;
```

---

## Adicionando uma nova fonte

1. Criar `pipeline/sources/nova_fonte.py` herdando de `BaseIngester`
2. Implementar `fetch_raw()`, `parse()`, `upsert()`
3. Adicionar model em `models/staging.py` se necessário
4. Registrar no `pipeline/orchestrator.py`

---

## Prevenção de data leakage

As lag features em `analytics.station_hourly_demand` são calculadas com dados **estritamente anteriores** ao `hour_bucket`. Ao treinar modelos:

```python
# Correto: treinar com dados onde computed_at < hora prevista
df = df[df['computed_at'] < df['hour_bucket']]

# Nunca usar total_kwh do próprio bucket como feature — só como target
X = df[['lag_1h_kwh', 'lag_24h_kwh', 'lag_168h_kwh', 'rolling_7d_avg_kwh',
        'hour_of_day', 'day_of_week', 'is_weekend', 'is_holiday']]
y = df['total_kwh']
```

---

## Particionamento

`core.charging_sessions` e `core.energy_readings` são particionadas por mês.
Para criar novas partições:

```sql
-- Criar partição para março/2024
CREATE TABLE core.charging_sessions_2024_03
    PARTITION OF core.charging_sessions
    FOR VALUES FROM ('2024-03-01 00:00:00+00') TO ('2024-04-01 00:00:00+00');

-- Arquivar partição antiga (sem deletar dados)
ALTER TABLE core.charging_sessions
    DETACH PARTITION core.charging_sessions_2023_01;
```

---

## Consultas analíticas úteis

```sql
-- Estações dentro de 5km de um ponto
SELECT name, operator,
    ST_Distance(location::geography,
        ST_MakePoint(-46.63, -23.55)::geography) AS dist_m
FROM core.charging_stations
WHERE ST_DWithin(location::geography,
    ST_MakePoint(-46.63, -23.55)::geography, 5000)
  AND is_operational = true
ORDER BY dist_m;

-- Pico de demanda por hora e dia da semana
SELECT hour_of_day, day_of_week,
    AVG(total_kwh) AS avg_demand,
    MAX(peak_kw)   AS max_peak
FROM analytics.station_hourly_demand
WHERE station_id = 1
  AND hour_bucket >= now() - INTERVAL '90 days'
GROUP BY hour_of_day, day_of_week
ORDER BY avg_demand DESC;

-- Top candidatos para nova estação
SELECT * FROM analytics.v_top_candidate_locations LIMIT 20;
```
