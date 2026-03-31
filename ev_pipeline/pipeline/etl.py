"""
pipeline/etl.py
Orquestrador completo do ETL: ingestão + transforms + features.

Fluxo:
  1. Ingestão (staging)     — fontes externas → staging.*
  2. Transforms (core)      — staging.* → core.*
  3. Features (analytics)   — core.* → analytics.*

Uso:
  python -m pipeline.etl --full
  python -m pipeline.etl --step ingest
  python -m pipeline.etl --step transform
  python -m pipeline.etl --step features --hours 72
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from pipeline.orchestrator import run_pipeline
from pipeline.transforms.municipalities import transform_municipalities
from pipeline.transforms.stations import transform_stations
from pipeline.transforms.sessions import transform_sessions
from pipeline.transforms.features import compute_hourly_demand
from utils.logger import get_logger

logger = get_logger("pipeline.etl")


def step_ingest(**kwargs) -> bool:
    logger.info("━━━ ETAPA 1: INGESTÃO (staging) ━━━")
    report = run_pipeline(**kwargs)
    return report.success


def step_transform(batch_size: int = 500) -> bool:
    logger.info("━━━ ETAPA 2: TRANSFORMS (staging → core) ━━━")
    ok = True

    logger.info("── Municípios")
    stats = transform_municipalities(batch_size=batch_size)
    logger.info("   %s", stats)

    logger.info("── Estações")
    stats = transform_stations(batch_size=min(batch_size, 200))
    logger.info("   %s", stats)

    logger.info("── Sessões")
    stats = transform_sessions(batch_size=batch_size * 2)
    logger.info("   %s", stats)

    return ok


def step_features(lookback_hours: int = 48) -> bool:
    logger.info("━━━ ETAPA 3: FEATURES (core → analytics) ━━━")
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)
    stats = compute_hourly_demand(start=start, end=now)
    logger.info("   %s", stats)
    return stats.get("errors", 0) == 0


def run_full_etl(lookback_hours: int = 48, **ingest_kwargs) -> bool:
    logger.info("=" * 55)
    logger.info("EV Pipeline — ETL Completo")
    logger.info("=" * 55)

    t0 = datetime.now(timezone.utc)
    results = []

    results.append(step_ingest(**ingest_kwargs))
    results.append(step_transform())
    results.append(step_features(lookback_hours=lookback_hours))

    duration = (datetime.now(timezone.utc) - t0).total_seconds()
    success = all(results)

    logger.info("=" * 55)
    logger.info(
        "ETL finalizado em %.1fs — %s",
        duration, "SUCESSO" if success else "FALHA",
    )
    logger.info("=" * 55)
    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="EV Charging — ETL pipeline completo")
    parser.add_argument("--full", action="store_true", help="Executa todas as etapas")
    parser.add_argument("--step",
                        choices=["ingest", "transform", "features"],
                        help="Etapa específica")
    parser.add_argument("--hours", type=int, default=48,
                        help="Janela de lookback para features (default: 48h)")

    # Argumentos de ingestão (repassados ao orchestrator)
    parser.add_argument("--senatran-csv", type=str, default=None)
    parser.add_argument("--month", type=str, default=None)
    parser.add_argument("--energy-csv", type=str, default=None)
    parser.add_argument("--energy-region", type=str, default=None)
    parser.add_argument("--acn-csv", type=str, default=None)

    args = parser.parse_args()

    ingest_kwargs = {
        "senatran_csv_path": args.senatran_csv,
        "senatran_month": args.month,
        "energy_csv_path": args.energy_csv,
        "energy_region_code": args.energy_region,
        "acn_csv_path": args.acn_csv,
    }

    if args.full:
        success = run_full_etl(lookback_hours=args.hours, **ingest_kwargs)
    elif args.step == "ingest":
        success = step_ingest(**ingest_kwargs)
    elif args.step == "transform":
        success = step_transform()
    elif args.step == "features":
        success = step_features(lookback_hours=args.hours)
    else:
        parser.print_help()
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
