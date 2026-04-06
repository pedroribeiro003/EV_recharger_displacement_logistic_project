from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.config import settings
from core.logging import get_logger
from db.models.ibge import IbgeMunicipality
from db.models.ipea import IpeaSeries, IpeaValue

logger = get_logger(__name__)

# Relevant IPEA series codes
SERIES: dict[str, str] = {
    "PNAD_RDMHABMAR": "Rendimento médio habitual — PNAD Contínua",
    "PIB_MUNGDP": "PIB Municipal",
    "CENSO_IDHM": "Índice de Desenvolvimento Humano Municipal (IDHM)",
}

_MUN_FIELD_MAP = {
    "PNAD_RDMHABMAR": None,          # no municipality-level data
    "PIB_MUNGDP": "gdp_per_capita",
    "CENSO_IDHM": "idhm",
}


class IpeaIngester:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = httpx.Client(
            base_url=settings.ipea_base_url,
            headers={"Accept": "application/json"},
            timeout=120,
        )

    def _get_or_create_series(self, code: str, name: str) -> IpeaSeries:
        series = self.session.scalars(
            select(IpeaSeries).where(IpeaSeries.code == code)
        ).first()
        if series:
            return series
        series = IpeaSeries(code=code, name=name, source="IPEA")
        self.session.add(series)
        self.session.flush()
        return series

    def fetch_series_values(self, serie_code: str) -> list[dict]:
        """Fetch all values via IPEA OData endpoint."""
        url = f"/ValoresSerie(SERCODIGO='{serie_code}')"
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("value") or data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("IPEA fetch failed for %s: %s", serie_code, exc)
            return []

    @staticmethod
    def _parse_value(raw: dict) -> tuple[int | None, datetime | None, float | None]:
        """Return (municipality_id, reference_date, numeric_value)."""
        mun_id = raw.get("TERCODIGO") or raw.get("NIVNOME")
        try:
            mun_id = int(mun_id) if mun_id else None
        except (ValueError, TypeError):
            mun_id = None

        ref_date = None
        date_str = raw.get("VALDATA") or raw.get("ANINI")
        if date_str:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y"):
                try:
                    ref_date = datetime.strptime(str(date_str)[:10], fmt[:10]).replace(
                        tzinfo=timezone.utc
                    )
                    break
                except ValueError:
                    continue

        value = None
        try:
            value = float(raw.get("VALVALOR") or raw.get("value") or 0) or None
        except (ValueError, TypeError):
            pass

        return mun_id, ref_date, value

    def run(self) -> None:
        for code, name in SERIES.items():
            logger.info("IPEA: processing series %s", code)
            series = self._get_or_create_series(code, name)
            raw_values = self.fetch_series_values(code)

            rows = []
            for raw in raw_values:
                mun_id, ref_date, value = self._parse_value(raw)
                rows.append(
                    {
                        "series_id": series.id,
                        "municipality_id": mun_id,
                        "reference_date": ref_date,
                        "value": value,
                    }
                )

            logger.info("IPEA: %d raw values fetched for %s, %d rows built",
                        len(raw_values), code, len(rows))

            # Bulk insert (append-only)
            if rows:
                self.session.execute(
                    pg_insert(IpeaValue).values(rows)
                    .on_conflict_do_nothing(),
                )
                self.session.commit()
                logger.info("IPEA: inserted %d values for %s", len(rows), code)
            else:
                logger.warning("IPEA: no rows to insert for %s", code)

            # Patch ibge_municipalities with most recent value if applicable
            mun_field = _MUN_FIELD_MAP.get(code)
            if mun_field:
                self._patch_municipalities(series, mun_field)

        logger.info("IPEA ingestion complete")

    def _patch_municipalities(self, series: IpeaSeries, field: str) -> None:
        """Write the most recent value per municipality onto ibge_municipalities."""
        from sqlalchemy import func

        latest = (
            select(
                IpeaValue.municipality_id,
                func.max(IpeaValue.reference_date).label("max_date"),
            )
            .where(IpeaValue.series_id == series.id)
            .where(IpeaValue.municipality_id.isnot(None))
            .group_by(IpeaValue.municipality_id)
            .subquery()
        )
        value_sub = (
            select(IpeaValue.municipality_id, IpeaValue.value)
            .join(
                latest,
                (IpeaValue.municipality_id == latest.c.municipality_id)
                & (IpeaValue.reference_date == latest.c.max_date),
            )
            .where(IpeaValue.series_id == series.id)
            .subquery()
        )

        self.session.execute(
            update(IbgeMunicipality)
            .where(IbgeMunicipality.id == value_sub.c.municipality_id)
            .values({field: value_sub.c.value})
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
        logger.info("IPEA: patched municipalities.%s", field)
