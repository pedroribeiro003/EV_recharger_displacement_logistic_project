"""
pipeline/orchestrator.py
Orquestrador da pipeline de dados brutos (staging).
Executa os ingestores em sequência com logging e relatório final.

Uso:
  python -m pipeline.orchestrator --all
  python -m pipeline.orchestrator --source ocm
  python -m pipeline.orchestrator --source senatran --csv /data/frota_2024_01.csv --month 2024-01
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.database import ensure_schemas_exist
from models.base import Base
from config.database import engine
from pipeline.base_ingester import IngestionResult
from pipeline.sources.acn_data import ACNDataIngester
from pipeline.sources.energy import EnergyReadingIngester
from pipeline.sources.ibge import IBGEMunicipalityIngester
from pipeline.sources.open_charge_map import OpenChargeMapIngester
from pipeline.sources.senatran import SenatranIngester
from utils.logger import get_logger

logger = get_logger("pipeline.orchestrator")


@dataclass
class PipelineReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    results: list[IngestionResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def total_inserted(self) -> int:
        return sum(r.records_inserted for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.results)

    def finish(self) -> "PipelineReport":
        self.finished_at = datetime.now(timezone.utc)
        return self

    def print_summary(self) -> None:
        duration = (
            (self.finished_at - self.started_at).total_seconds()
            if self.finished_at else 0
        )
        print("\n" + "=" * 60)
        print(f"  Pipeline de Ingestão — Relatório Final")
        print("=" * 60)
        for r in self.results:
            status = "OK" if r.success else "ERRO"
            print(
                f"  [{status}] {r.source:<25} "
                f"fetched={r.records_fetched:>6} "
                f"upserted={r.records_inserted:>6} "
                f"erros={len(r.errors):>3}"
            )
        print("=" * 60)
        print(f"  Total inserido/atualizado : {self.total_inserted}")
        print(f"  Total de erros            : {self.total_errors}")
        print(f"  Duração total             : {duration:.1f}s")
        print(f"  Status final              : {'SUCESSO' if self.success else 'FALHA'}")
        print("=" * 60 + "\n")


def run_pipeline(
    sources: list[str] | None = None,
    ocm_max_results: int = 5000,
    ibge_state: str | None = None,
    acn_csv_path: str | None = None,
    acn_site: str = "caltech",
    senatran_csv_path: str | None = None,
    senatran_month: str | None = None,
    energy_csv_path: str | None = None,
    energy_region_code: str | None = None,
    energy_source: str = "ons",
) -> PipelineReport:
    """
    Executa a pipeline de ingestão para as fontes selecionadas.

    Args:
      sources → lista de fontes a executar, ou None para todas.
                Valores: "ocm", "ibge", "acn", "senatran", "energy"
    """
    report = PipelineReport()
    run_all = not sources

    logger.info("=" * 50)
    logger.info("Iniciando pipeline de ingestão staging")
    logger.info("Fontes selecionadas: %s", sources or "todas")

    # ── Garante schemas e extensões ──────────────────────────
    logger.info("Verificando schemas e extensões PostGIS...")
    ensure_schemas_exist()
    Base.metadata.create_all(engine)
    logger.info("Schemas OK.")

    # ── 1. IBGE — municípios ─────────────────────────────────
    if run_all or "ibge" in sources:
        logger.info("--- IBGE: Municípios ---")
        ingester = IBGEMunicipalityIngester()
        result = ingester.run(state_code=ibge_state)
        report.results.append(result)

    # ── 2. Open Charge Map — estações ────────────────────────
    if run_all or "ocm" in sources:
        logger.info("--- Open Charge Map: Estações ---")
        ingester = OpenChargeMapIngester()
        result = ingester.run(country_code="BR", max_results=ocm_max_results)
        report.results.append(result)

    # ── 3. ACN-Data — sessões de carregamento ────────────────
    if run_all or "acn" in sources:
        logger.info("--- ACN-Data: Sessões de Carregamento ---")
        ingester = ACNDataIngester()
        result = ingester.run(
            site=acn_site,
            csv_path=acn_csv_path,
        )
        report.results.append(result)

    # ── 4. SENATRAN — frota EV por município ─────────────────
    if (run_all or "senatran" in sources) and senatran_csv_path and senatran_month:
        logger.info("--- SENATRAN: Frota EV ---")
        ingester = SenatranIngester()
        result = ingester.run(
            csv_path=senatran_csv_path,
            reference_month=senatran_month,
        )
        report.results.append(result)
    elif (run_all or "senatran" in sources):
        logger.warning(
            "SENATRAN ignorado: forneça --senatran-csv e --month"
        )

    # ── 5. Energia — leituras ONS/concessionária ─────────────
    if (run_all or "energy" in sources) and energy_csv_path:
        logger.info("--- Energia: Leituras da Rede ---")
        ingester = EnergyReadingIngester(source_name=energy_source)
        result = ingester.run(
            csv_path=energy_csv_path,
            region_code_override=energy_region_code,
        )
        report.results.append(result)
    elif (run_all or "energy" in sources):
        logger.warning("Energia ignorado: forneça --energy-csv")

    report.finish()
    report.print_summary()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de ingestão de dados brutos (staging)")

    parser.add_argument("--all", action="store_true", help="Executa todas as fontes")
    parser.add_argument("--source", nargs="+",
                        choices=["ocm", "ibge", "acn", "senatran", "energy"],
                        help="Fontes específicas a executar")

    # OCM
    parser.add_argument("--ocm-max", type=int, default=5000,
                        help="Máximo de estações OCM (default: 5000)")

    # IBGE
    parser.add_argument("--state", type=str, default=None,
                        help="UF para filtrar municípios IBGE (ex: SP)")

    # ACN-Data
    parser.add_argument("--acn-csv", type=str, default=None,
                        help="Caminho para CSV do ACN-Data")
    parser.add_argument("--acn-site", type=str, default="caltech",
                        help="Site ACN-Data (default: caltech)")

    # SENATRAN
    parser.add_argument("--senatran-csv", type=str, default=None,
                        help="Caminho para CSV do SENATRAN")
    parser.add_argument("--month", type=str, default=None,
                        help="Mês de referência SENATRAN ex: 2024-01")

    # Energia
    parser.add_argument("--energy-csv", type=str, default=None,
                        help="Caminho para CSV de energia (ONS ou concessionária)")
    parser.add_argument("--energy-region", type=str, default=None,
                        help="Código de região/subestação para as leituras")
    parser.add_argument("--energy-source", type=str, default="ons",
                        help="Nome da fonte de energia (default: ons)")

    args = parser.parse_args()

    sources = args.source if not args.all else None

    report = run_pipeline(
        sources=sources,
        ocm_max_results=args.ocm_max,
        ibge_state=args.state,
        acn_csv_path=args.acn_csv,
        acn_site=args.acn_site,
        senatran_csv_path=args.senatran_csv,
        senatran_month=args.month,
        energy_csv_path=args.energy_csv,
        energy_region_code=args.energy_region,
        energy_source=args.energy_source,
    )

    sys.exit(0 if report.success else 1)


if __name__ == "__main__":
    main()
