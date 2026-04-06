import io
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = (
    "https://www.gov.br/senatran/pt-br/assuntos/rnatrc/"
    "frota-de-veiculos/{year}/frota_por_municipio_e_tipo_{mon}_{year}.xlsx"
)
_MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
               "jul", "ago", "set", "out", "nov", "dez"]

_EV_FUELS = {"ELETRICO", "ELÉTRICO", "ELETRICO HIBRIDO", "ELÉTRICO HÍBRIDO"}
_HYBRID_FUELS = {"HIBRIDO", "HÍBRIDO", "ELETRICO HIBRIDO", "ELÉTRICO HÍBRIDO"}

# Possible column name variants across file editions
_COL_IBGE   = ["codigo_municipio", "codigo_ibge", "cod_municipio", "codigomunicipio"]
_COL_FUEL   = ["combustivel", "tipo_combustivel"]
_COL_VTYPE  = ["tipo", "tipo_veiculo", "tipoveiculo"]
_COL_COUNT  = ["quantidade", "total", "qtd"]


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str:
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
    raise KeyError(f"None of {candidates} found in columns: {list(df.columns)}")


def _latest_available_file(client: httpx.Client) -> tuple[int, int, bytes]:
    """Try months backwards from today until a file downloads successfully."""
    today = date.today()
    for delta in range(2, 8):  # start 2 months back (data released ~60 days late)
        month = today.month - delta
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        mon_str = _MONTHS_PT[month - 1]
        url = _BASE_URL.format(year=year, mon=mon_str)
        logger.info("SENATRAN: trying %s/%s (%s)", year, mon_str, url)
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                logger.info("SENATRAN: found file %s/%s (%d bytes)", year, mon_str, len(resp.content))
                return year, month, resp.content
            logger.debug("SENATRAN: %s/%s → HTTP %d", year, mon_str, resp.status_code)
        except Exception as exc:
            logger.debug("SENATRAN: %s/%s → %s", year, mon_str, exc)
    raise RuntimeError("SENATRAN: no available file found in the last 8 months")


class SenatranIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.gov.br/senatran/pt-br/assuntos/rnatrc/frota-de-veiculos",
            },
            timeout=120,
            follow_redirects=True,
        )

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
                "year": year,
                "month": month,
                "fuel_type": fuel,
                "vehicle_type": vtype,
                "count": count,
                "ev_count": count if fuel in _EV_FUELS else None,
                "hybrid_count": count if fuel in _HYBRID_FUELS else None,
            })
        return rows

    def run(self) -> None:
        year, month, content = _latest_available_file(self.client)

        logger.info("SENATRAN: parsing file %d/%02d", year, month)
        df = self._parse_xlsx(content)
        rows = self._build_rows(df, year, month)
        logger.info("SENATRAN: %d rows parsed", len(rows))

        # Clear existing data for this reference period before re-inserting
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
