import io
import re
import time
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging import get_logger

logger = get_logger(__name__)

_INDEX_URL = (
    "https://www.gov.br/transportes/pt-br/assuntos/transito/"
    "conteudo-Senatran/frota-de-veiculos-{year}"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.gov.br/transportes/pt-br/assuntos/transito",
}

_EV_FUELS = {"ELETRICO", "ELÉTRICO", "ELETRICO HIBRIDO", "ELÉTRICO HÍBRIDO"}
_HYBRID_FUELS = {"HIBRIDO", "HÍBRIDO", "ELETRICO HIBRIDO", "ELÉTRICO HÍBRIDO"}

_COL_IBGE  = ["codigo_municipio", "codigo_ibge", "cod_municipio", "codigomunicipio"]
_COL_FUEL  = ["combustivel", "tipo_combustivel"]
_COL_VTYPE = ["tipo", "tipo_veiculo", "tipoveiculo"]
_COL_COUNT = ["quantidade", "total", "qtd"]

# Portuguese month names for detecting the most recent file
_MONTH_ORDER = [
    "janeiro", "fevereiro", "março", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str:
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
    raise KeyError(f"None of {candidates} found in columns: {list(df.columns)}")


def _month_rank(href: str) -> int:
    """Return position of month name in href (higher = more recent)."""
    href_lower = href.lower()
    for i, m in enumerate(_MONTH_ORDER):
        if m in href_lower:
            return i
    return -1


def _scrape_fleet_links(client: httpx.Client, year: int) -> list[str]:
    """Return hrefs of fleet-by-municipality files from the year index page."""
    url = _INDEX_URL.format(year=year)
    logger.info("SENATRAN: scraping index %s", url)
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("SENATRAN: index %d unreachable: %s", year, exc)
        return []

    # Find all href values in the HTML
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', resp.text)

    fleet_links = []
    for href in hrefs:
        lower = href.lower()
        if not (lower.endswith(".xlsx") or lower.endswith(".xls")):
            continue
        # Must mention municipio (with possible accent/typo) to be the right file type
        if not re.search(r"munic[íi]?p", lower):
            continue
        # Resolve relative URLs
        if href.startswith("http"):
            fleet_links.append(href)
        elif href.startswith("/"):
            fleet_links.append("https://www.gov.br" + href)
        else:
            fleet_links.append(f"https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-Senatran/{href}")

    logger.info("SENATRAN: found %d fleet file links for %d", len(fleet_links), year)
    return fleet_links


def _download_latest(client: httpx.Client) -> tuple[int, int, bytes]:
    """Scrape index pages and download the most recent available fleet file."""
    today = date.today()
    for year in [today.year, today.year - 1]:
        links = _scrape_fleet_links(client, year)
        if not links:
            continue

        # Sort by month rank descending (most recent month first)
        links_sorted = sorted(links, key=_month_rank, reverse=True)

        for href in links_sorted:
            logger.info("SENATRAN: trying %s", href)
            try:
                resp = client.get(href)
                if resp.status_code == 200 and len(resp.content) > 10_000:
                    month = _month_rank(href) + 1  # 1-indexed
                    if month <= 0:
                        month = 12  # fallback if month not detected
                    logger.info(
                        "SENATRAN: downloaded %s (%d bytes), inferred month=%d",
                        href, len(resp.content), month,
                    )
                    return year, month, resp.content
                logger.debug("SENATRAN: %s → HTTP %d", href, resp.status_code)
            except Exception as exc:
                logger.debug("SENATRAN: %s → %s", href, exc)

    raise RuntimeError("SENATRAN: no fleet file found after scraping index pages")


class SenatranIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(headers=_HEADERS, timeout=120, follow_redirects=True)

    def _parse_xlsx(self, content: bytes) -> pd.DataFrame:
        df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [c.strip() for c in df.columns]
        return df

    def _build_rows(self, df: pd.DataFrame, year: int, month: int) -> list[dict]:
        col_ibge  = _find_col(df, _COL_IBGE)
        col_fuel  = _find_col(df, _COL_FUEL)
        col_vtype = _find_col(df, _COL_VTYPE)
        col_count = _find_col(df, _COL_COUNT)

        rows = []
        for _, row in df.iterrows():
            try:
                mun_id = int(str(row[col_ibge]).strip().split(".")[0])
            except (ValueError, TypeError):
                continue

            fuel  = str(row[col_fuel]).strip().upper()
            vtype = str(row[col_vtype]).strip().upper()
            try:
                count = int(float(str(row[col_count]).replace(",", ".")))
            except (ValueError, TypeError):
                count = 0

            rows.append({
                "municipality_id": mun_id,
                "year":            year,
                "month":           month,
                "fuel_type":       fuel,
                "vehicle_type":    vtype,
                "count":           count,
                "ev_count":        count if fuel in _EV_FUELS else None,
                "hybrid_count":    count if fuel in _HYBRID_FUELS else None,
            })
        return rows

    def run(self) -> None:
        year, month, content = _download_latest(self.client)

        logger.info("SENATRAN: parsing file %d/%02d", year, month)
        df = self._parse_xlsx(content)
        rows = self._build_rows(df, year, month)
        logger.info("SENATRAN: %d rows parsed", len(rows))

        self.session.execute(
            text("DELETE FROM senatran_fleet WHERE year = :y AND month = :m"),
            {"y": year, "m": month},
        )

        batch_size = 1000
        for i in range(0, len(rows), batch_size):
            self.session.execute(
                text(
                    "INSERT INTO senatran_fleet "
                    "(municipality_id, year, month, fuel_type, vehicle_type, "
                    " count, ev_count, hybrid_count) "
                    "VALUES (:municipality_id, :year, :month, :fuel_type, :vehicle_type, "
                    "        :count, :ev_count, :hybrid_count)"
                ),
                rows[i : i + batch_size],
            )
        self.session.commit()
        logger.info("SENATRAN ingestion complete: %d rows for %d/%02d", len(rows), year, month)
