"""
db_report.py — Database health & data analysis report
======================================================

Usage (on the server, inside the project virtualenv):
    python scripts/db_report.py

Prints a full diagnostic: row counts, pipeline completeness,
EV station distribution, connector status, recent activity, and more.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlalchemy import func, text

# Allow running from the project root
sys.path.insert(0, ".")

from db.engine import SessionLocal  # noqa: E402

# ── Formatting helpers ─────────────────────────────────────────────────────────

W = 70

def header(title: str) -> None:
    print(f"\n{'─' * W}")
    print(f"  {title}")
    print(f"{'─' * W}")


def row(label: str, value, width: int = 40) -> None:
    print(f"  {label:<{width}} {value}")


def table(columns: list[str], rows: list[tuple], col_widths: list[int] | None = None) -> None:
    if not rows:
        print("  (sem dados)")
        return
    if col_widths is None:
        col_widths = [max(len(str(r[i])) for r in rows + [columns]) + 2 for i in range(len(columns))]
    fmt = "  " + "".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*columns))
    print("  " + " ".join("-" * (w - 1) for w in col_widths))
    for r in rows:
        print(fmt.format(*[str(v) if v is not None else "—" for v in r]))


# ── Report sections ────────────────────────────────────────────────────────────

def section_overview(session) -> None:
    header("VISAO GERAL — contagem por tabela")

    checks = [
        ("stations",                  "Estacoes EV (Tupi)"),
        ("connectors",                "Conectores"),
        ("status_snapshots",          "Snapshots de status"),
        ("ibge_states",               "Estados (IBGE)"),
        ("ibge_municipalities",       "Municipios (IBGE)"),
        ("ibge_municipality_boundaries", "Poligonos municipais"),
        ("anp_gas_stations",          "Postos ANP"),
        ("anp_fuel_prices",           "Precos de combustivel (ANP)"),
        ("aneel_distributors",        "Distribuidoras (ANEEL)"),
        ("aneel_tariffs",             "Tarifas (ANEEL)"),
        ("aneel_outages",             "Interrupcoes (ANEEL)"),
        ("senatran_fleet",            "Frota SENATRAN"),
        ("ipea_series",               "Series IPEA"),
        ("ipea_values",               "Valores IPEA"),
        ("osm_pois",                  "POIs (OSM)"),
        ("station_poi_distances",     "Distancias estacao-POI"),
    ]

    total_rows = 0
    for tbl, label in checks:
        try:
            n = session.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0
            status = f"{n:>10,}" if n > 0 else f"{'0':>10}  ⚠  VAZIO"
            total_rows += n
        except Exception:
            status = f"{'—':>10}  ✗  TABELA NAO EXISTE"
        row(label, status)

    print(f"\n  {'Total de registros no banco:':<40} {total_rows:>10,}")


def section_ev_stations(session) -> None:
    header("ESTACOES EV — distribuicao por estado")

    results = session.execute(text("""
        SELECT
            COALESCE(state_uf, 'N/D') AS uf,
            COUNT(*)                  AS estacoes,
            SUM(CASE WHEN is_public THEN 1 ELSE 0 END) AS publicas,
            COUNT(DISTINCT municipality_id)             AS municipios
        FROM stations
        GROUP BY state_uf
        ORDER BY estacoes DESC
        LIMIT 30
    """)).fetchall()

    table(
        ["UF", "Estacoes", "Publicas", "Municipios"],
        [(r[0], f"{r[1]:,}", f"{r[2]:,}", f"{r[3]:,}") for r in results],
        [8, 12, 12, 14],
    )

    total = session.execute(text("SELECT COUNT(*) FROM stations")).scalar() or 0
    sem_municipio = session.execute(text(
        "SELECT COUNT(*) FROM stations WHERE municipality_id IS NULL"
    )).scalar() or 0

    print(f"\n  {'Total de estacoes:':<40} {total:,}")
    if sem_municipio:
        print(f"  {'Sem municipio (geocode pendente):':<40} {sem_municipio:,}  ⚠")


def section_connectors(session) -> None:
    header("CONECTORES — status atual e tipos")

    status_rows = session.execute(text("""
        SELECT
            COALESCE(current_status, 'DESCONHECIDO') AS status,
            COUNT(*)                                  AS qtd,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM connectors
        GROUP BY current_status
        ORDER BY qtd DESC
    """)).fetchall()

    table(
        ["Status", "Conectores", "%"],
        [(r[0], f"{r[1]:,}", f"{r[2]}%") for r in status_rows],
        [20, 14, 8],
    )

    print()

    plug_rows = session.execute(text("""
        SELECT
            COALESCE(plug_type, 'N/D') AS plug,
            COUNT(*)                   AS qtd,
            ROUND(AVG(power_kw)::numeric, 1) AS media_kw,
            SUM(CASE WHEN is_fast THEN 1 ELSE 0 END) AS rapidos
        FROM connectors
        GROUP BY plug_type
        ORDER BY qtd DESC
    """)).fetchall()

    header("CONECTORES — tipos de plug e potencia")
    table(
        ["Tipo de Plug", "Qtd", "Media kW", "Rapidos"],
        [(r[0], f"{r[1]:,}", f"{r[2] or '—'}", f"{r[3]:,}") for r in plug_rows],
        [20, 8, 12, 10],
    )


def section_snapshots(session) -> None:
    header("SNAPSHOTS — atividade recente")

    counts = session.execute(text("""
        SELECT
            DATE_TRUNC('day', captured_at)::date AS dia,
            COUNT(*)                             AS snapshots,
            COUNT(DISTINCT station_id)           AS estacoes
        FROM status_snapshots
        WHERE captured_at >= NOW() - INTERVAL '7 days'
        GROUP BY 1
        ORDER BY 1 DESC
    """)).fetchall()

    table(
        ["Dia", "Snapshots", "Estacoes monitoradas"],
        [(str(r[0]), f"{r[1]:,}", f"{r[2]:,}") for r in counts],
        [14, 14, 22],
    )

    last = session.execute(text(
        "SELECT MAX(captured_at) FROM status_snapshots"
    )).scalar()
    if last:
        ago = datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)
        mins = int(ago.total_seconds() / 60)
        print(f"\n  {'Ultimo snapshot capturado:':<40} {last.strftime('%Y-%m-%d %H:%M:%S')} UTC  ({mins} min atras)")


def section_fleet(session) -> None:
    header("FROTA SENATRAN — ultimos dados disponiveis")

    results = session.execute(text("""
        SELECT
            year,
            month,
            fuel_type,
            SUM(count)        AS total,
            SUM(ev_count)     AS ev,
            SUM(hybrid_count) AS hibrido
        FROM senatran_fleet
        GROUP BY year, month, fuel_type
        ORDER BY year DESC, month DESC, total DESC
        LIMIT 20
    """)).fetchall()

    table(
        ["Ano", "Mes", "Combustivel", "Total", "EV", "Hibrido"],
        [(r[0], r[1] or "—", r[2], f"{r[3]:,}", f"{r[4]:,}" if r[4] else "—", f"{r[5]:,}" if r[5] else "—")
         for r in results],
        [6, 5, 22, 12, 10, 10],
    )


def section_tariffs(session) -> None:
    header("TARIFAS ANEEL — media por estado")

    results = session.execute(text("""
        SELECT
            d.state_uf                          AS uf,
            COUNT(DISTINCT d.id)                AS distribuidoras,
            ROUND(AVG(t.te_kwh + t.tusd_kwh)::numeric, 6) AS tarifa_media_kwh
        FROM aneel_tariffs t
        JOIN aneel_distributors d ON d.id = t.distributor_id
        WHERE t.te_kwh IS NOT NULL AND t.tusd_kwh IS NOT NULL
        GROUP BY d.state_uf
        ORDER BY tarifa_media_kwh DESC
        LIMIT 30
    """)).fetchall()

    table(
        ["UF", "Distribuidoras", "Tarifa media (R$/kWh)"],
        [(r[0] or "N/D", r[1], f"R$ {r[2]}" if r[2] else "—") for r in results],
        [6, 16, 24],
    )


def section_osm(session) -> None:
    header("OSM — POIs por categoria")

    results = session.execute(text("""
        SELECT
            category,
            COUNT(*) AS pois,
            COUNT(DISTINCT municipality_id) AS municipios
        FROM osm_pois
        GROUP BY category
        ORDER BY pois DESC
    """)).fetchall()

    table(
        ["Categoria", "POIs", "Municipios"],
        [(r[0], f"{r[1]:,}", f"{r[2]:,}") for r in results],
        [24, 10, 12],
    )


def section_coverage(session) -> None:
    header("COBERTURA — municipios com e sem estacao EV")

    result = session.execute(text("""
        SELECT
            COUNT(DISTINCT m.id)                                          AS total_municipios,
            COUNT(DISTINCT s.municipality_id)                             AS com_estacao,
            COUNT(DISTINCT m.id) - COUNT(DISTINCT s.municipality_id)     AS sem_estacao,
            ROUND(
                COUNT(DISTINCT s.municipality_id) * 100.0 / NULLIF(COUNT(DISTINCT m.id), 0), 1
            )                                                             AS pct_cobertura
        FROM ibge_municipalities m
        LEFT JOIN stations s ON s.municipality_id = m.id
    """)).fetchone()

    if result:
        row("Total de municipios brasileiros:", f"{result[0]:,}")
        row("Com ao menos 1 estacao EV:", f"{result[1]:,}")
        row("Sem nenhuma estacao EV:", f"{result[2]:,}")
        row("Cobertura:", f"{result[3]}%")

    # Top 10 municipios com mais estacoes
    print()
    top = session.execute(text("""
        SELECT
            m.name,
            s_uf.uf,
            COUNT(s.id)      AS estacoes,
            SUM(c.cnt)       AS conectores,
            m.population
        FROM stations s
        JOIN ibge_municipalities m ON m.id = s.municipality_id
        JOIN ibge_states s_uf ON s_uf.id = m.state_id
        LEFT JOIN (
            SELECT station_id, COUNT(*) AS cnt FROM connectors GROUP BY station_id
        ) c ON c.station_id = s.id
        GROUP BY m.name, s_uf.uf, m.population
        ORDER BY estacoes DESC
        LIMIT 10
    """)).fetchall()

    print("  Top 10 municipios por numero de estacoes:")
    table(
        ["Municipio", "UF", "Estacoes", "Conectores", "Populacao"],
        [(r[0], r[1], r[2], f"{r[3]:,}" if r[3] else "—", f"{r[4]:,}" if r[4] else "—")
         for r in top],
        [30, 5, 10, 12, 12],
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * W)
    print(f"  EV DEMAND — Relatorio do Banco de Dados")
    print(f"  Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * W)

    with SessionLocal() as session:
        section_overview(session)
        section_ev_stations(session)
        section_connectors(session)
        section_snapshots(session)
        section_fleet(session)
        section_tariffs(session)
        section_osm(session)
        section_coverage(session)

    print(f"\n{'═' * W}\n")


if __name__ == "__main__":
    main()
