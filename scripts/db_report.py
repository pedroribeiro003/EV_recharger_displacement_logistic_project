"""
db_report.py — EV Demand: relatório de análise em PDF
======================================================

O PDF é escrito no stdout (bytes) e nunca salvo no servidor.
Redirecione para o seu computador local via SSH:

    ssh user@servidor \\
        "cd /opt/ev-demand && source venv/bin/activate && python scripts/db_report.py" \\
        > ~/Desktop/ev_relatorio.pdf

Ou com make:
    ssh user@servidor "cd /opt/ev-demand && make db-report" > ~/Desktop/ev_relatorio.pdf
"""

from __future__ import annotations

import io
import sys
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, ".")
from db.engine import SessionLocal  # noqa: E402

# ── ReportLab imports ──────────────────────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Group
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF

# ── Paleta de cores ────────────────────────────────────────────────────────────
C_PRIMARY   = colors.HexColor("#1A3C5E")   # azul escuro
C_ACCENT    = colors.HexColor("#27AE60")   # verde EV
C_LIGHT     = colors.HexColor("#EAF4FB")   # azul bem claro
C_GRAY      = colors.HexColor("#7F8C8D")   # cinza texto
C_GRAY_LITE = colors.HexColor("#F4F6F7")   # fundo linha par
C_RED       = colors.HexColor("#E74C3C")
C_ORANGE    = colors.HexColor("#F39C12")
C_WHITE     = colors.white
C_BLACK     = colors.HexColor("#1C2833")

PIE_COLORS = [
    colors.HexColor("#27AE60"),
    colors.HexColor("#E74C3C"),
    colors.HexColor("#F39C12"),
    colors.HexColor("#3498DB"),
    colors.HexColor("#9B59B6"),
    colors.HexColor("#1ABC9C"),
    colors.HexColor("#E67E22"),
]

BAR_COLORS = [colors.HexColor("#2471A3"), colors.HexColor("#27AE60")]

PW, PH = A4
MARGIN = 2 * cm

# ── Estilos ───────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()

    def s(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "cover_title": s("cover_title",
            fontName="Helvetica-Bold", fontSize=32, textColor=C_WHITE,
            alignment=TA_CENTER, leading=40, spaceAfter=10),
        "cover_sub": s("cover_sub",
            fontName="Helvetica", fontSize=14, textColor=colors.HexColor("#AED6F1"),
            alignment=TA_CENTER, leading=20),
        "cover_meta": s("cover_meta",
            fontName="Helvetica", fontSize=10, textColor=colors.HexColor("#85C1E9"),
            alignment=TA_CENTER, spaceAfter=4),
        "section": s("section",
            fontName="Helvetica-Bold", fontSize=16, textColor=C_PRIMARY,
            spaceBefore=18, spaceAfter=6, leading=20),
        "subsection": s("subsection",
            fontName="Helvetica-Bold", fontSize=12, textColor=C_PRIMARY,
            spaceBefore=12, spaceAfter=4),
        "body": s("body",
            fontName="Helvetica", fontSize=9, textColor=C_BLACK,
            leading=14, spaceAfter=4),
        "caption": s("caption",
            fontName="Helvetica-Oblique", fontSize=8, textColor=C_GRAY,
            alignment=TA_CENTER, spaceBefore=2, spaceAfter=8),
        "kpi_label": s("kpi_label",
            fontName="Helvetica", fontSize=8, textColor=C_GRAY,
            alignment=TA_CENTER, leading=10),
        "kpi_value": s("kpi_value",
            fontName="Helvetica-Bold", fontSize=22, textColor=C_PRIMARY,
            alignment=TA_CENTER, leading=26),
        "footer": s("footer",
            fontName="Helvetica", fontSize=8, textColor=C_GRAY,
            alignment=TA_CENTER),
        "alert": s("alert",
            fontName="Helvetica-Bold", fontSize=9, textColor=C_RED),
    }


# ── Componentes visuais ────────────────────────────────────────────────────────

def divider() -> HRFlowable:
    return HRFlowable(width="100%", thickness=1, color=C_LIGHT,
                      spaceBefore=4, spaceAfter=8)


def kpi_table(items: list[tuple[str, str]], st: dict) -> Table:
    """Linha de cards KPI: [(label, value), ...]"""
    n = len(items)
    col_w = (PW - 2 * MARGIN) / n
    header_cells = [Paragraph(v, st["kpi_value"]) for _, v in items]
    label_cells  = [Paragraph(l, st["kpi_label"]) for l, _ in items]
    t = Table(
        [header_cells, label_cells],
        colWidths=[col_w] * n,
        rowHeights=[32, 16],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), C_LIGHT),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_LIGHT, C_WHITE]),
        ("BOX",         (0, 0), (-1, -1), 0.5, C_PRIMARY),
        ("INNERGRID",   (0, 0), (-1, -1), 0.25, colors.HexColor("#D5E8F5")),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING",(0, 1),(-1, 1), 8),
    ]))
    return t


def data_table(headers: list[str], rows: list[list],
               col_widths: list[float] | None = None,
               highlight_col: int | None = None) -> Table:
    """Tabela de dados com cabeçalho estilizado e zebra."""
    if not rows:
        rows = [["(sem dados)"] + [""] * (len(headers) - 1)]

    all_rows = [[Paragraph(str(h), ParagraphStyle(
        "th", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=C_WHITE, alignment=TA_CENTER)
    ) for h in headers]]

    for i, r in enumerate(rows):
        bg = C_GRAY_LITE if i % 2 == 0 else C_WHITE
        all_rows.append([
            Paragraph(str(c) if c is not None else "—",
                      ParagraphStyle("td", fontName="Helvetica", fontSize=8.5,
                                     textColor=C_BLACK, alignment=TA_LEFT,
                                     leading=11))
            for c in r
        ])

    t = Table(all_rows, colWidths=col_widths, repeatRows=1)

    style = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_PRIMARY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_GRAY_LITE, C_WHITE]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#D0D3D4")),
        ("LINEBELOW",     (0, 0), (-1, 0), 1, C_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    if highlight_col is not None:
        style.append(("FONTNAME", (highlight_col, 1), (highlight_col, -1), "Helvetica-Bold"))
        style.append(("TEXTCOLOR", (highlight_col, 1), (highlight_col, -1), C_PRIMARY))

    t.setStyle(TableStyle(style))
    return t


def pie_chart(labels: list[str], values: list[float],
              width: float = 200, height: float = 160) -> Drawing:
    d = Drawing(width, height)
    pie = Pie()
    pie.x = width // 2 - 55
    pie.y = height // 2 - 55
    pie.width = 110
    pie.height = 110
    pie.data = values
    pie.labels = [f"{l}\n{v:,.0f}" for l, v in zip(labels, values)]
    pie.sideLabels = True
    pie.slices.strokeWidth = 0.5
    pie.slices.strokeColor = C_WHITE
    for i, c in enumerate(PIE_COLORS[: len(values)]):
        pie.slices[i].fillColor = c
    d.add(pie)
    return d


def bar_chart(categories: list[str], values: list[float],
              width: float = 380, height: float = 180,
              bar_color=None) -> Drawing:
    if bar_color is None:
        bar_color = C_PRIMARY
    d = Drawing(width, height)
    bc = VerticalBarChart()
    bc.x = 40
    bc.y = 20
    bc.width = width - 60
    bc.height = height - 40
    bc.data = [values]
    bc.categoryAxis.categoryNames = categories
    bc.categoryAxis.labels.angle = 45
    bc.categoryAxis.labels.fontSize = 7
    bc.categoryAxis.labels.dx = -10
    bc.categoryAxis.labels.dy = -10
    bc.valueAxis.labels.fontSize = 7
    bc.bars[0].fillColor = bar_color
    bc.bars[0].strokeColor = None
    bc.groupSpacing = 8
    d.add(bc)
    return d


# ── Templates de página ────────────────────────────────────────────────────────

def _on_cover(canvas, doc):
    canvas.saveState()
    # Fundo degradê simulado com dois retângulos
    canvas.setFillColor(C_PRIMARY)
    canvas.rect(0, 0, PW, PH, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#0D2137"))
    canvas.rect(0, 0, PW, PH * 0.38, fill=1, stroke=0)
    # Faixa accent
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PH * 0.38, PW, 4, fill=1, stroke=0)
    canvas.restoreState()


def _on_page(canvas, doc):
    canvas.saveState()
    # Cabeçalho
    canvas.setFillColor(C_PRIMARY)
    canvas.rect(0, PH - 1.2 * cm, PW, 1.2 * cm, fill=1, stroke=0)
    canvas.setFillColor(C_WHITE)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(MARGIN, PH - 0.75 * cm, "EV Demand — Relatório de Análise")
    canvas.setFont("Helvetica", 8)
    now_str = datetime.now().strftime("%d/%m/%Y")
    canvas.drawRightString(PW - MARGIN, PH - 0.75 * cm, now_str)
    # Rodapé
    canvas.setFillColor(C_GRAY_LITE)
    canvas.rect(0, 0, PW, 0.9 * cm, fill=1, stroke=0)
    canvas.setFillColor(C_GRAY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawCentredString(PW / 2, 0.32 * cm, f"Página {doc.page}")
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, 0.88 * cm, PW, 0.1 * cm, fill=1, stroke=0)
    canvas.restoreState()


# ── Coleta de dados ────────────────────────────────────────────────────────────

def fetch_all(session) -> dict:
    def q(sql):
        return session.execute(text(sql)).fetchall()

    def scalar(sql):
        return session.execute(text(sql)).scalar() or 0

    def safe_scalar(sql):
        try:
            return session.execute(text(sql)).scalar() or 0
        except Exception:
            return 0

    counts = {}
    for tbl in ["stations", "connectors", "status_snapshots",
                "ibge_states", "ibge_municipalities",
                "anp_gas_stations", "anp_fuel_prices",
                "aneel_distributors", "aneel_tariffs", "aneel_outages",
                "senatran_fleet", "ipea_series", "ipea_values",
                "osm_pois", "station_poi_distances"]:
        counts[tbl] = safe_scalar(f"SELECT COUNT(*) FROM {tbl}")

    stations_by_state = q("""
        SELECT COALESCE(state_uf,'N/D'), COUNT(*),
               SUM(CASE WHEN is_public THEN 1 ELSE 0 END),
               COUNT(DISTINCT municipality_id)
        FROM stations GROUP BY state_uf ORDER BY COUNT(*) DESC LIMIT 20
    """)

    connector_status = q("""
        SELECT COALESCE(current_status,'DESCONHECIDO'), COUNT(*)
        FROM connectors GROUP BY current_status ORDER BY COUNT(*) DESC
    """)

    plug_types = q("""
        SELECT COALESCE(plug_type,'N/D'), COUNT(*),
               ROUND(AVG(power_kw)::numeric,1),
               SUM(CASE WHEN is_fast THEN 1 ELSE 0 END)
        FROM connectors GROUP BY plug_type ORDER BY COUNT(*) DESC LIMIT 12
    """)

    snapshots_7d = q("""
        SELECT DATE_TRUNC('day', captured_at)::date, COUNT(*),
               COUNT(DISTINCT station_id)
        FROM status_snapshots
        WHERE captured_at >= NOW() - INTERVAL '7 days'
        GROUP BY 1 ORDER BY 1 DESC
    """)

    last_snapshot = session.execute(text(
        "SELECT MAX(captured_at) FROM status_snapshots"
    )).scalar()

    fleet = q("""
        SELECT year, month, fuel_type,
               SUM(count), SUM(ev_count), SUM(hybrid_count)
        FROM senatran_fleet
        GROUP BY year, month, fuel_type
        ORDER BY year DESC, month DESC NULLS LAST, SUM(count) DESC LIMIT 15
    """)

    tariffs = q("""
        SELECT d.state_uf, COUNT(DISTINCT d.id),
               ROUND(AVG(t.te_kwh + t.tusd_kwh)::numeric, 5)
        FROM aneel_tariffs t
        JOIN aneel_distributors d ON d.id = t.distributor_id
        WHERE t.te_kwh IS NOT NULL AND t.tusd_kwh IS NOT NULL
        GROUP BY d.state_uf ORDER BY 3 DESC LIMIT 27
    """)

    osm_cats = q("""
        SELECT category, COUNT(*), COUNT(DISTINCT municipality_id)
        FROM osm_pois GROUP BY category ORDER BY COUNT(*) DESC
    """)

    coverage = session.execute(text("""
        SELECT
          COUNT(DISTINCT m.id),
          COUNT(DISTINCT s.municipality_id),
          COUNT(DISTINCT m.id) - COUNT(DISTINCT s.municipality_id),
          ROUND(COUNT(DISTINCT s.municipality_id)*100.0/NULLIF(COUNT(DISTINCT m.id),0),1)
        FROM ibge_municipalities m
        LEFT JOIN stations s ON s.municipality_id = m.id
    """)).fetchone()

    top_cities = q("""
        SELECT m.name, st.uf, COUNT(s.id),
               SUM(c.cnt), m.population
        FROM stations s
        JOIN ibge_municipalities m ON m.id = s.municipality_id
        JOIN ibge_states st ON st.id = m.state_id
        LEFT JOIN (
            SELECT station_id, COUNT(*) cnt FROM connectors GROUP BY station_id
        ) c ON c.station_id = s.id
        GROUP BY m.name, st.uf, m.population
        ORDER BY COUNT(s.id) DESC LIMIT 15
    """)

    sem_geocode = safe_scalar(
        "SELECT COUNT(*) FROM stations WHERE municipality_id IS NULL"
    )

    return dict(
        counts=counts,
        stations_by_state=stations_by_state,
        connector_status=connector_status,
        plug_types=plug_types,
        snapshots_7d=snapshots_7d,
        last_snapshot=last_snapshot,
        fleet=fleet,
        tariffs=tariffs,
        osm_cats=osm_cats,
        coverage=coverage,
        top_cities=top_cities,
        sem_geocode=sem_geocode,
    )


# ── Construção do PDF ──────────────────────────────────────────────────────────

def build_pdf(data: dict, buf: io.BytesIO) -> None:
    st = _styles()
    now_str = datetime.now().strftime("%d/%m/%Y às %H:%M")
    available_w = PW - 2 * MARGIN

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=1.6 * cm,
        bottomMargin=1.4 * cm,
        title="EV Demand — Relatório de Análise",
        author="EV Demand Platform",
    )

    cover_frame = Frame(0, 0, PW, PH, id="cover")
    body_frame  = Frame(MARGIN, 1.2 * cm, available_w, PH - 2.8 * cm, id="body")

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=_on_cover),
        PageTemplate(id="Body",  frames=[body_frame],  onPage=_on_page),
    ])

    story = []

    # ── Capa ──────────────────────────────────────────────────────────────────
    story.append(NextPageTemplate("Body"))
    story.append(Spacer(1, 6.5 * cm))
    story.append(Paragraph("EV DEMAND", st["cover_title"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Relatório de Análise da Infraestrutura de<br/>Recarga de Veículos Elétricos no Brasil",
                            st["cover_sub"]))
    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph(f"Gerado em {now_str}", st["cover_meta"]))
    story.append(Paragraph("Dados: Tupi · IBGE · ANP · ANEEL · SENATRAN · IPEA · OSM",
                            st["cover_meta"]))
    story.append(PageBreak())

    # ── Visão Geral ───────────────────────────────────────────────────────────
    c = data["counts"]
    story.append(Paragraph("Visão Geral", st["section"]))
    story.append(divider())
    story.append(Paragraph(
        "Resumo de todos os dados ingeridos no banco de dados PostgreSQL/PostGIS.",
        st["body"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(kpi_table([
        (f"{c['stations']:,}", "Estações EV"),
        (f"{c['connectors']:,}", "Conectores"),
        (f"{c['status_snapshots']:,}", "Snapshots"),
        (f"{c['ibge_municipalities']:,}", "Municípios IBGE"),
    ], st))
    story.append(Spacer(1, 0.25 * cm))
    story.append(kpi_table([
        (f"{c['anp_gas_stations']:,}", "Postos ANP"),
        (f"{c['aneel_tariffs']:,}", "Tarifas ANEEL"),
        (f"{c['senatran_fleet']:,}", "Reg. Frota"),
        (f"{c['osm_pois']:,}", "POIs OSM"),
    ], st))
    story.append(Spacer(1, 0.4 * cm))

    # Tabela detalhada de contagens
    count_rows = [
        ["Estações EV (Tupi)",        _fmt(c["stations"])],
        ["Conectores",                 _fmt(c["connectors"])],
        ["Snapshots de status",        _fmt(c["status_snapshots"])],
        ["Estados (IBGE)",             _fmt(c["ibge_states"])],
        ["Municípios (IBGE)",          _fmt(c["ibge_municipalities"])],
        ["Postos ANP",                 _fmt(c["anp_gas_stations"])],
        ["Preços de combustível (ANP)",_fmt(c["anp_fuel_prices"])],
        ["Distribuidoras (ANEEL)",     _fmt(c["aneel_distributors"])],
        ["Tarifas (ANEEL)",            _fmt(c["aneel_tariffs"])],
        ["Interrupções (ANEEL)",       _fmt(c["aneel_outages"])],
        ["Frota SENATRAN",             _fmt(c["senatran_fleet"])],
        ["Séries IPEA",                _fmt(c["ipea_series"])],
        ["Valores IPEA",               _fmt(c["ipea_values"])],
        ["POIs OSM",                   _fmt(c["osm_pois"])],
        ["Distâncias Estação–POI",     _fmt(c["station_poi_distances"])],
    ]
    story.append(data_table(
        ["Tabela / Fonte", "Registros"],
        count_rows,
        col_widths=[available_w * 0.72, available_w * 0.28],
        highlight_col=1,
    ))

    # ── Estações EV ───────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Estações EV por Estado", st["section"]))
    story.append(divider())

    sem_geo = data["sem_geocode"]
    if sem_geo:
        story.append(Paragraph(
            f"⚠  {sem_geo:,} estações sem município atribuído (geocode pendente).",
            st["alert"]))
        story.append(Spacer(1, 0.2 * cm))

    sb = data["stations_by_state"]
    if sb:
        # Gráfico de barras — top 10 por estações
        top10 = sb[:10]
        cats   = [r[0] for r in top10]
        vals   = [float(r[1]) for r in top10]
        story.append(bar_chart(cats, vals, width=available_w, height=200,
                               bar_color=C_PRIMARY))
        story.append(Paragraph("Figura 1 — Top 10 estados por número de estações EV",
                                st["caption"]))

    state_rows = [
        [r[0], _fmt(r[1]), _fmt(r[2]), _fmt(r[3])]
        for r in sb
    ]
    story.append(data_table(
        ["UF", "Estações", "Públicas", "Municípios com EV"],
        state_rows,
        col_widths=[available_w * 0.12, available_w * 0.22,
                    available_w * 0.22, available_w * 0.44],
    ))

    # ── Conectores ────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Conectores", st["section"]))
    story.append(divider())

    cs = data["connector_status"]
    if cs:
        labels = [r[0] for r in cs]
        vals   = [float(r[1]) for r in cs]
        pie    = pie_chart(labels, vals, width=available_w, height=200)
        story.append(pie)
        story.append(Paragraph("Figura 2 — Distribuição de status dos conectores",
                                st["caption"]))

    story.append(data_table(
        ["Status", "Conectores"],
        [[r[0], _fmt(r[1])] for r in cs],
        col_widths=[available_w * 0.65, available_w * 0.35],
        highlight_col=1,
    ))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Tipos de Plug e Potência", st["subsection"]))
    pt = data["plug_types"]
    story.append(data_table(
        ["Tipo de Plug", "Qtd", "Média kW", "Rápidos"],
        [[r[0], _fmt(r[1]), str(r[2]) if r[2] else "—", _fmt(r[3])] for r in pt],
        col_widths=[available_w * 0.40, available_w * 0.18,
                    available_w * 0.22, available_w * 0.20],
    ))

    # ── Snapshots ─────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Atividade de Monitoramento (últimos 7 dias)", st["section"]))
    story.append(divider())

    ls = data["last_snapshot"]
    if ls:
        ago = datetime.now(timezone.utc) - ls.replace(tzinfo=timezone.utc)
        mins = int(ago.total_seconds() / 60)
        story.append(Paragraph(
            f"Último snapshot capturado: <b>{ls.strftime('%d/%m/%Y %H:%M:%S')} UTC</b> "
            f"({mins} minutos atrás)",
            st["body"]))
        story.append(Spacer(1, 0.3 * cm))

    snaps = data["snapshots_7d"]
    if snaps:
        cats = [str(r[0]) for r in snaps]
        vals = [float(r[1]) for r in snaps]
        story.append(bar_chart(cats, vals, width=available_w, height=180,
                               bar_color=C_ACCENT))
        story.append(Paragraph("Figura 3 — Snapshots capturados por dia",
                                st["caption"]))

    story.append(data_table(
        ["Dia", "Snapshots capturados", "Estações monitoradas"],
        [[str(r[0]), _fmt(r[1]), _fmt(r[2])] for r in snaps] if snaps else [],
        col_widths=[available_w * 0.30, available_w * 0.35, available_w * 0.35],
    ))

    # ── Frota SENATRAN ────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Frota de Veículos — SENATRAN", st["section"]))
    story.append(divider())

    fleet = data["fleet"]
    story.append(data_table(
        ["Ano", "Mês", "Combustível", "Total", "Elétricos", "Híbridos"],
        [
            [r[0], r[1] or "—", r[2],
             _fmt(r[3]), _fmt(r[4]) if r[4] else "—", _fmt(r[5]) if r[5] else "—"]
            for r in fleet
        ],
        col_widths=[
            available_w * 0.09, available_w * 0.07, available_w * 0.30,
            available_w * 0.18, available_w * 0.18, available_w * 0.18,
        ],
    ))

    # ── Tarifas ANEEL ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("Tarifas de Energia — ANEEL", st["section"]))
    story.append(divider())

    tariffs = data["tariffs"]
    if tariffs:
        cats = [r[0] or "N/D" for r in tariffs[:12]]
        vals = [float(r[2]) if r[2] else 0.0 for r in tariffs[:12]]
        story.append(bar_chart(cats, vals, width=available_w, height=170,
                               bar_color=colors.HexColor("#8E44AD")))
        story.append(Paragraph("Figura 4 — Tarifa média (TE + TUSD) por estado (R$/kWh)",
                                st["caption"]))

    story.append(data_table(
        ["UF", "Distribuidoras", "Tarifa média (R$/kWh)"],
        [[r[0] or "N/D", _fmt(r[1]), f"R$ {r[2]}" if r[2] else "—"] for r in tariffs],
        col_widths=[available_w * 0.15, available_w * 0.30, available_w * 0.55],
    ))

    # ── OSM POIs ──────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Pontos de Interesse — OpenStreetMap", st["section"]))
    story.append(divider())

    osm = data["osm_cats"]
    if osm:
        cats = [r[0] for r in osm]
        vals = [float(r[1]) for r in osm]
        story.append(pie_chart(cats, vals, width=available_w, height=200))
        story.append(Paragraph("Figura 5 — POIs por categoria (OSM)",
                                st["caption"]))

    story.append(data_table(
        ["Categoria", "POIs", "Municípios"],
        [[r[0], _fmt(r[1]), _fmt(r[2])] for r in osm],
        col_widths=[available_w * 0.50, available_w * 0.25, available_w * 0.25],
    ))

    # ── Cobertura ─────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Cobertura Municipal de EV", st["section"]))
    story.append(divider())

    cov = data["coverage"]
    if cov:
        story.append(kpi_table([
            (f"{cov[0]:,}", "Municípios Brasil"),
            (f"{cov[1]:,}", "Com estação EV"),
            (f"{cov[2]:,}", "Sem estação EV"),
            (f"{cov[3] or 0}%", "Cobertura"),
        ], st))
        story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Top 15 Municípios por Número de Estações EV",
                            st["subsection"]))
    tc = data["top_cities"]
    story.append(data_table(
        ["Município", "UF", "Estações", "Conectores", "População"],
        [
            [r[0], r[1], _fmt(r[2]),
             _fmt(r[3]) if r[3] else "—",
             _fmt(r[4]) if r[4] else "—"]
            for r in tc
        ],
        col_widths=[
            available_w * 0.36, available_w * 0.08, available_w * 0.16,
            available_w * 0.18, available_w * 0.22,
        ],
        highlight_col=2,
    ))

    # ── Construir ─────────────────────────────────────────────────────────────
    doc.build(story)


def _fmt(n) -> str:
    if n is None:
        return "—"
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    output = sys.argv[1] if len(sys.argv) > 1 else None

    buf = io.BytesIO()
    with SessionLocal() as session:
        data = fetch_all(session)
    build_pdf(data, buf)

    if output:
        with open(output, "wb") as f:
            f.write(buf.getvalue())
        print(f"PDF salvo em: {output}", file=sys.stderr)
    else:
        sys.stdout.buffer.write(buf.getvalue())


if __name__ == "__main__":
    main()
