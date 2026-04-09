# EV Recharger Displacement & Logistics Platform

A data integration and spatial analysis platform that maps demand for electric vehicle (EV) charging infrastructure across Brazil. It aggregates data from multiple government and commercial APIs, persists them in a PostGIS-enabled PostgreSQL database, and exposes a unified CLI for pipeline orchestration.

---

## Table of Contents

- [Architecture](#architecture)
- [Data Sources](#data-sources)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Local Development](#local-development)
  - [Server Deployment (Ubuntu)](#server-deployment-ubuntu)
- [CLI Reference](#cli-reference)
- [Development](#development)
  - [Code Quality](#code-quality)
  - [Tests](#tests)
  - [Database Migrations](#database-migrations)
- [Project Structure](#project-structure)
- [Environment Variables](#environment-variables)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        CLI  (main.py)                          │
└───────────────────────────────┬────────────────────────────────┘
                                │
          ┌─────────────────────▼──────────────────────┐
          │            Ingest Module (ingest/)          │
          │  ibge · tupi · anp · aneel · senatran       │
          │  ipea · osm · geocode                       │
          └─────────────────────┬──────────────────────┘
                                │
          ┌─────────────────────▼──────────────────────┐
          │          Database Layer  (db/)              │
          │  models · repositories · migrations         │
          └─────────────────────┬──────────────────────┘
                                │
          ┌─────────────────────▼──────────────────────┐
          │    PostgreSQL 15 + PostGIS 3  (Ubuntu)      │
          └────────────────────────────────────────────┘
```

**Pipeline phases:**

| Phase | Ingestors | Dependency |
|-------|-----------|------------|
| 1 | IBGE, Tupi, ANP, ANEEL, SENATRAN | None — run in parallel |
| 2 | IPEA, OSM | Requires Phase 1 complete |
| 3 | Geocode | Requires Phase 2 complete |
| ∞ | Tupi poll | Continuous loop — managed by systemd |

---

## Data Sources

| Source | Description |
|--------|-------------|
| **Tupi** | Real-time EV charging station status (connectors, availability) |
| **IBGE** | Brazilian states, municipalities, centroids, population, area |
| **ANP** | Fuel station network with EV charger flags |
| **ANEEL** | Electricity tariffs and outage data by distributor |
| **SENATRAN** | Vehicle fleet composition by municipality (EV/hybrid breakdown) |
| **IPEA** | Socioeconomic time-series indicators |
| **OSM** | Points of interest via Overpass API (malls, hospitals, hotels, etc.) |

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 15 + PostGIS 3
- `make` (optional but recommended)

### Local Development

```bash
# 1. Clone the repository
git clone <repo-url>
cd ev-recharger-displacement-logistic-project

# 2. Create a virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 3. Install dependencies (prod + dev + pre-commit hooks)
make install-dev

# 4. Configure environment
cp .env.example .env
# Edit .env: set DATABASE_URL pointing to your local PostgreSQL

# 5. Apply migrations
make migrate

# 6. Run the full ingestion pipeline
make ingest-all
```

### Server Deployment (Ubuntu)

#### First-time setup

Run once as root on the Ubuntu server. The script installs PostgreSQL+PostGIS, creates the database user, installs the app to `/opt/ev-demand`, and registers it as a systemd service.

```bash
# On the server, inside the project directory
sudo bash scripts/setup.sh
```

After setup completes, review `/opt/ev-demand/.env` to add any missing API keys, then start the service:

```bash
sudo systemctl start ev-demand
sudo journalctl -u ev-demand -f   # follow logs
```

#### Deploying updates

```bash
# Pull latest code locally, then on the server:
sudo bash scripts/deploy.sh
```

Or use make shortcuts from the server:

```bash
make deploy         # sync code + migrate + restart service
make service-logs   # follow logs
make service-status # check service health
```

#### Service management

```bash
sudo systemctl start ev-demand
sudo systemctl stop ev-demand
sudo systemctl restart ev-demand
sudo systemctl status ev-demand
sudo journalctl -u ev-demand -f
```

---

## Relatório PDF

Gera um PDF com análise completa do banco (estações, conectores, frota, tarifas, cobertura municipal) e envia direto para o seu computador — nenhum arquivo fica salvo no servidor.

**Já dentro do servidor (tmux ou SSH):**

```bash
# 1. Gera o PDF em /tmp (não persiste no app)
python scripts/db_report.py /tmp/ev_relatorio.pdf

# 2. Numa nova aba do terminal no seu Mac, baixa o arquivo
scp user@seu-servidor:/tmp/ev_relatorio.pdf ~/Desktop/
```

**Fora do servidor, em uma linha só:**

```bash
ssh user@seu-servidor \
  "cd /opt/ev-demand && source venv/bin/activate && python scripts/db_report.py" \
  > ~/Desktop/ev_relatorio.pdf
```

---

## CLI Reference

```
python main.py <command> [options]
```

| Command | Description |
|---------|-------------|
| `ingest ibge` | Ingest IBGE geographic and demographic data |
| `ingest tupi [--enrich\|--poll]` | One-shot enrich or continuous poll of Tupi EV stations |
| `ingest anp` | Ingest ANP fuel station network |
| `ingest ipea` | Ingest IPEA socioeconomic series |
| `ingest osm` | Ingest OSM points of interest via Overpass |
| `ingest all` | Run full ingestion pipeline (phases 1–3) |
| `geocode` | Run spatial assignment of stations to municipalities |

**Examples:**

```bash
python main.py ingest ibge
python main.py ingest tupi --poll   # continuous polling
python main.py ingest all           # full pipeline
```

---

## Development

### Code Quality

```bash
make lint       # Lint with ruff
make format     # Auto-fix style and imports
make typecheck  # Static analysis with mypy
```

Pre-commit hooks run automatically on every `git commit` after `make install-dev`.

### Tests

```bash
make test             # All tests
make test-unit        # Unit tests (no DB required)
make test-integration # Integration tests (requires DATABASE_URL)
make coverage         # Tests + HTML coverage report
```

### Database Migrations

```bash
make migrate                          # Apply pending migrations
make migrate-new MSG="add column x"   # Create a new migration
make db-status                        # Show current revision
```

---

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── ci.yml              # CI: lint, typecheck, unit & integration tests
├── core/
│   ├── config.py               # Pydantic Settings — environment configuration
│   └── logging.py              # Rotating file + stdout logger
├── db/
│   ├── engine.py               # SQLAlchemy engine & session factory
│   ├── migrations/             # Alembic migration scripts
│   │   └── versions/
│   ├── models/                 # ORM models (station, ibge, anp, aneel, ...)
│   └── repositories/           # Data-access layer (queries, upserts)
├── deploy/
│   └── ev-demand.service       # systemd unit file for Ubuntu server
├── ingest/
│   ├── tupi.py                 # EV charging station ingestor (enrich + poll)
│   ├── ibge.py                 # IBGE municipalities & demographics
│   ├── anp.py                  # ANP fuel stations
│   ├── aneel.py                # ANEEL electricity tariffs
│   ├── senatran.py             # SENATRAN vehicle fleet
│   ├── ipea.py                 # IPEA socioeconomic indicators
│   ├── osm.py                  # OSM POIs via Overpass
│   └── geocode.py              # Spatial assignment: stations → municipalities
├── scripts/
│   ├── bootstrap.py            # Entrypoint: check DB → ingest missing → poll
│   ├── setup.sh                # First-time Ubuntu server provisioning
│   └── deploy.sh               # Update and restart on the server
├── tests/
│   ├── conftest.py             # Shared pytest fixtures (DB session with rollback)
│   ├── unit/                   # Fast tests, no external dependencies
│   └── integration/            # Tests requiring PostgreSQL/PostGIS
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── alembic.ini
├── main.py                     # CLI entry point
├── Makefile                    # Dev & deployment task automation
├── pyproject.toml              # Project metadata, tool config (ruff, mypy, pytest)
└── requirements.txt            # Production dependencies
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string — **required** |
| `TUPI_BASE_URL` | Tupi API base URL |
| `TUPI_ORIGIN` | Origin header for Tupi requests |
| `POLL_INTERVAL` | Seconds between Tupi polling cycles (default: `300`) |
| `ANP_BASE_URL` | ANP API base URL |
| `IPEA_BASE_URL` | IPEA OData API URL |
| `OVERPASS_URL` | Overpass API URL |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `LOG_FILE` | Log file path (default: `ev_demand.log`) |
