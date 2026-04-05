"""
ev_demand CLI
=============

Usage examples:
  python main.py ingest ibge
  python main.py ingest tupi --enrich
  python main.py ingest tupi --poll
  python main.py ingest anp
  python main.py ingest ipea
  python main.py ingest osm
  python main.py geocode
  python main.py ingest all
"""
import argparse
import sys

from core.logging import get_logger
from db.engine import SessionLocal

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Sub-command handlers
# ------------------------------------------------------------------


def cmd_ingest_ibge(args: argparse.Namespace) -> None:
    from ingest.ibge import IbgeIngester

    with SessionLocal() as session:
        IbgeIngester(session).run()


def cmd_ingest_tupi(args: argparse.Namespace) -> None:
    from ingest.tupi import TupiIngester

    with SessionLocal() as session:
        ingester = TupiIngester(session)
        if args.poll:
            ingester.run_poll()
        else:
            ingester.run_enrich(delay=args.delay)


def cmd_ingest_anp(args: argparse.Namespace) -> None:
    from ingest.anp import AnpIngester

    with SessionLocal() as session:
        AnpIngester(session).run()


def cmd_ingest_ipea(args: argparse.Namespace) -> None:
    from ingest.ipea import IpeaIngester

    with SessionLocal() as session:
        IpeaIngester(session).run()


def cmd_ingest_osm(args: argparse.Namespace) -> None:
    from ingest.osm import OsmIngester

    with SessionLocal() as session:
        OsmIngester(session).run()


def cmd_ingest_all(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from ingest.ibge import IbgeIngester
    from ingest.tupi import TupiIngester
    from ingest.anp import AnpIngester
    from ingest.ipea import IpeaIngester
    from ingest.osm import OsmIngester

    def _ibge():
        with SessionLocal() as s:
            IbgeIngester(s).run()

    def _tupi():
        with SessionLocal() as s:
            TupiIngester(s).run_enrich(delay=0.3)

    def _anp():
        with SessionLocal() as s:
            AnpIngester(s).run()

    def _ipea():
        with SessionLocal() as s:
            IpeaIngester(s).run()

    def _osm():
        with SessionLocal() as s:
            OsmIngester(s).run()

    def _run(name: str, fn):
        try:
            fn()
        except Exception as exc:
            logger.exception("%s failed: %s", name, exc)

    logger.info("Running full ingestion pipeline")

    # Phase 1 — independent sources in parallel
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_run, name, fn): name for name, fn in [
            ("ibge", _ibge), ("tupi", _tupi), ("anp", _anp),
        ]}
        for fut in as_completed(futs):
            fut.result()

    # Phase 2 — IPEA (needs IBGE done) + OSM (needs Tupi done for distances)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(_run, name, fn): name for name, fn in [
            ("ipea", _ipea), ("osm", _osm),
        ]}
        for fut in as_completed(futs):
            fut.result()

    # Phase 3 — geocode depends on stations + OSM distances
    cmd_geocode(args)

    logger.info("Full ingestion pipeline complete")


def cmd_geocode(args: argparse.Namespace) -> None:
    from ingest.geocode import GeocodeService

    with SessionLocal() as session:
        GeocodeService(session).run()


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ev_demand",
        description="EV demand analysis — data ingestion CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- ingest ---
    ingest_p = sub.add_parser("ingest", help="Ingest data from external sources")
    ingest_sub = ingest_p.add_subparsers(dest="source", required=True)

    # ingest ibge
    ingest_sub.add_parser("ibge", help="Ingest IBGE states & municipalities")

    # ingest tupi
    tupi_p = ingest_sub.add_parser("tupi", help="Ingest Tupi EV charging stations")
    tupi_mode = tupi_p.add_mutually_exclusive_group()
    tupi_mode.add_argument("--enrich", dest="poll", action="store_false", default=False,
                           help="One-shot enrich (default)")
    tupi_mode.add_argument("--poll", dest="poll", action="store_true",
                           help="Continuous poll loop")
    tupi_p.add_argument("--delay", type=float, default=0.3,
                        help="Seconds between requests during enrich (default: 0.3)")

    # ingest anp
    ingest_sub.add_parser("anp", help="Ingest ANP gas stations")

    # ingest ipea
    ingest_sub.add_parser("ipea", help="Ingest IPEA socioeconomic series")

    # ingest osm
    ingest_sub.add_parser("osm", help="Ingest OSM POIs via Overpass")

    # ingest all
    ingest_sub.add_parser("all", help="Run full ingestion pipeline")

    # --- geocode ---
    sub.add_parser("geocode", help="Run geocoding and spatial assignment")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        ("ingest", "ibge"): cmd_ingest_ibge,
        ("ingest", "tupi"): cmd_ingest_tupi,
        ("ingest", "anp"): cmd_ingest_anp,
        ("ingest", "ipea"): cmd_ingest_ipea,
        ("ingest", "osm"): cmd_ingest_osm,
        ("ingest", "all"): cmd_ingest_all,
        ("geocode", None): cmd_geocode,
    }

    key = (args.command, getattr(args, "source", None))
    handler = dispatch.get(key)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
