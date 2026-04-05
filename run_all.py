"""
run_all.py
==========
Verifica o estado do banco, roda os ingestores que estiverem vazios
e termina no poll contínuo do Tupi.

Uso:
    python run_all.py
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from core.logging import get_logger
from db.engine import SessionLocal

logger = get_logger("run_all")

# Tabela → query de checagem
CHECKS = {
    "ibge": "SELECT COUNT(*) FROM ibge_municipalities",
    "anp":  "SELECT COUNT(*) FROM anp_gas_stations",
    "ipea": "SELECT COUNT(*) FROM ipea_values",
    "osm":  "SELECT COUNT(*) FROM osm_pois",
    "tupi": "SELECT COUNT(*) FROM stations",
}


# ── helpers ──────────────────────────────��─────────────────────────────

def _count(sql: str) -> int:
    with SessionLocal() as s:
        return s.execute(text(sql)).scalar() or 0


def _run(name: str, fn):
    try:
        fn()
        logger.info("[%s] concluido", name)
    except Exception as exc:
        logger.exception("[%s] falhou: %s", name, exc)


# ── checagem ───────────────────────────────────────────────────────────

def check_db() -> set[str]:
    """Retorna o conjunto de ingestores com tabela vazia."""
    logger.info("Verificando estado do banco...")
    missing = set()
    for name, sql in CHECKS.items():
        try:
            count = _count(sql)
        except Exception:
            # tabela pode não existir antes da migration
            count = 0
        status = "OK (%d registros)" % count if count else "VAZIO"
        logger.info("  %-6s -> %s", name, status)
        if count == 0:
            missing.add(name)
    return missing


# ── ingestores ─────────────────────────────────────────────────────────

def run_ibge():
    from ingest.ibge import IbgeIngester
    with SessionLocal() as s:
        IbgeIngester(s).run()


def run_anp():
    from ingest.anp import AnpIngester
    with SessionLocal() as s:
        AnpIngester(s).run()


def run_ipea():
    from ingest.ipea import IpeaIngester
    with SessionLocal() as s:
        IpeaIngester(s).run()


def run_osm():
    from ingest.osm import OsmIngester
    with SessionLocal() as s:
        OsmIngester(s).run()


def run_tupi_enrich():
    from ingest.tupi import TupiIngester
    with SessionLocal() as s:
        TupiIngester(s).run_enrich()


def run_tupi_poll():
    from ingest.tupi import TupiIngester
    with SessionLocal() as s:
        TupiIngester(s).run_poll()


# ── pipeline ───────────────────────────────────────────────────────────

def run_missing(missing: set[str]) -> None:
    if not missing:
        logger.info("Banco completo — pulando ingestores.")
        return

    logger.info("Ingestores faltantes: %s", sorted(missing))

    # Fase 1 — independentes em paralelo (max 3 workers)
    phase1 = {
        name: fn
        for name, fn in [("ibge", run_ibge), ("anp", run_anp), ("tupi", run_tupi_enrich)]
        if name in missing
    }
    if phase1:
        logger.info("Fase 1 (paralelo): %s", list(phase1))
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {pool.submit(_run, name, fn): name for name, fn in phase1.items()}
            for fut in as_completed(futs):
                fut.result()

    # Fase 2 — IPEA precisa do IBGE; OSM precisa do Tupi (distâncias)
    phase2 = {
        name: fn
        for name, fn in [("ipea", run_ipea), ("osm", run_osm)]
        if name in missing
    }
    if phase2:
        logger.info("Fase 2 (paralelo): %s", list(phase2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {pool.submit(_run, name, fn): name for name, fn in phase2.items()}
            for fut in as_completed(futs):
                fut.result()


# ── entry point ────────────────────────────────────────────────────────

def main():
    logger.info("========== run_all iniciando ==========")

    missing = check_db()
    run_missing(missing)

    logger.info("========== iniciando poll Tupi ==========")
    run_tupi_poll()  # loop infinito


if __name__ == "__main__":
    main()
