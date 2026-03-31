"""
pipeline/sources/senatran.py
Ingestor para dados de frota de veículos elétricos — SENATRAN/DENATRAN.

O SENATRAN disponibiliza arquivos Excel/CSV mensais em:
  https://www.gov.br/senatran/pt-br/assuntos/estatistica/frota-de-veiculos

Este ingestor lê o CSV exportado e filtra veículos elétricos (EV/PHEV/HEV).
Tipos de combustível relevantes no SENATRAN:
  ELÉTRICO/FONTE EXTERNA (EV), HÍBRIDO (HEV), GÁS/ELÉTRICO (PHEV)
"""

import csv
import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from models.staging import StgVehicleFleet
from pipeline.base_ingester import BaseIngester

settings = get_settings()

# Categorias de combustível do SENATRAN que nos interessam
EV_FUEL_TYPES = {
    "ELÉTRICO/FONTE EXTERNA",
    "ELETRICO/FONTE EXTERNA",
    "ELÉTRICO",
    "ELETRICO",
}
PHEV_FUEL_TYPES = {
    "GÁS/ELÉTRICO",
    "GAS/ELETRICO",
    "HÍBRIDO ELÉTRICO",
    "HIBRIDO ELETRICO",
    "PLUG-IN HÍBRIDO",
    "PLUG-IN HIBRIDO",
}
HEV_FUEL_TYPES = {
    "HÍBRIDO",
    "HIBRIDO",
}


def _normalize_fuel(fuel: str) -> str:
    return fuel.strip().upper()


def _parse_reference_month(month_str: str) -> date:
    """
    Aceita formatos:
      - "2024-01" → date(2024, 1, 1)
      - "01/2024" → date(2024, 1, 1)
      - "2024-01-01" → date(2024, 1, 1)
    """
    month_str = month_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%m/%Y"):
        try:
            return datetime.strptime(month_str, fmt).date().replace(day=1)
        except ValueError:
            continue
    raise ValueError(f"Formato de mês não reconhecido: '{month_str}'")


class SenatranIngester(BaseIngester):
    source_name = "senatran"

    def fetch_raw(
        self,
        csv_path: str | Path,
        reference_month: str,
        encoding: str = "latin-1",
        delimiter: str = ";",
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Lê CSV do SENATRAN.

        O CSV do SENATRAN tem colunas variáveis por ano.
        Colunas esperadas (mínimas):
          MUNICIPIO | CODIGO_MUNICIPIO_IBGE | COMBUSTIVEL | TOTAL
        ou variações como:
          MUNICÍPIO | CÓDIGO IBGE | TIPO COMBUSTÍVEL | QUANTIDADE

        Args:
          csv_path        → caminho para o arquivo .csv do SENATRAN
          reference_month → "YYYY-MM" ou "MM/YYYY"
          encoding        → geralmente "latin-1" nos arquivos do governo
          delimiter       → ";" padrão nos CSVs do SENATRAN
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV SENATRAN não encontrado: {path}")

        ref_month = _parse_reference_month(reference_month)
        records: list[dict[str, Any]] = []

        with open(path, newline="", encoding=encoding, errors="replace") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            for row in reader:
                # Normaliza chaves (remove espaços, acentos problemáticos)
                normalized = {k.strip(): v.strip() for k, v in row.items() if k}
                fuel = _normalize_fuel(
                    normalized.get("COMBUSTIVEL")
                    or normalized.get("TIPO COMBUSTIVEL")
                    or normalized.get("TIPO_COMBUSTIVEL")
                    or ""
                )

                # Filtra apenas veículos eletrificados
                if fuel not in (EV_FUEL_TYPES | PHEV_FUEL_TYPES | HEV_FUEL_TYPES):
                    continue

                ibge_code = (
                    normalized.get("CODIGO_MUNICIPIO_IBGE")
                    or normalized.get("CÓDIGO IBGE")
                    or normalized.get("CODIGO IBGE")
                    or ""
                ).strip()

                if not ibge_code:
                    continue

                records.append({
                    "_reference_month": ref_month.isoformat(),
                    "_fuel_normalized": fuel,
                    "ibge_code": ibge_code[:7],  # garante 7 dígitos
                    "municipality_name": (
                        normalized.get("MUNICIPIO")
                        or normalized.get("MUNICÍPIO")
                        or normalized.get("NOME_MUNICIPIO")
                        or ""
                    ),
                    "state_code": (
                        normalized.get("UF")
                        or normalized.get("ESTADO")
                        or ""
                    ).upper()[:2],
                    "total": int(
                        (
                            normalized.get("TOTAL")
                            or normalized.get("QUANTIDADE")
                            or "0"
                        ).replace(".", "").replace(",", "") or "0"
                    ),
                    "_raw": normalized,
                })

        self.logger.info(
            "SENATRAN: %d registros de EVs lidos de %s (ref: %s)",
            len(records), path, ref_month,
        )
        return records

    def parse(self, raw_records: list[dict[str, Any]]) -> list[StgVehicleFleet]:
        """
        Agrega por (ibge_code, reference_month) para ter 1 linha por município/mês
        com totais de EV, PHEV, HEV separados.
        """
        from collections import defaultdict

        # Agrega {(ibge_code, month) -> {ev, phev, hev, total}}
        aggregated: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"ev": 0, "phev": 0, "hev": 0, "name": "", "state": "", "raws": []}
        )

        for item in raw_records:
            key = (item["ibge_code"], item["_reference_month"])
            fuel = item["_fuel_normalized"]
            qty = item.get("total", 0)
            agg = aggregated[key]

            if fuel in EV_FUEL_TYPES:
                agg["ev"] += qty
            elif fuel in PHEV_FUEL_TYPES:
                agg["phev"] += qty
            elif fuel in HEV_FUEL_TYPES:
                agg["hev"] += qty

            agg["name"] = agg["name"] or item.get("municipality_name", "")
            agg["state"] = agg["state"] or item.get("state_code", "")
            agg["raws"].append(item["_raw"])

        records: list[StgVehicleFleet] = []
        for (ibge_code, month_str), agg in aggregated.items():
            ref_month = _parse_reference_month(month_str)
            total_ev = agg["ev"]
            total_phev = agg["phev"]
            total_hev = agg["hev"]

            records.append(
                StgVehicleFleet(
                    source=self.source_name,
                    reference_month=datetime(
                        ref_month.year, ref_month.month, 1,
                        tzinfo=None,
                    ),
                    municipality_code=ibge_code,
                    raw_json={
                        "municipality": agg["name"],
                        "state": agg["state"],
                        "ev": total_ev,
                        "phev": total_phev,
                        "hev": total_hev,
                        "rows": agg["raws"],
                    },
                    total_ev=total_ev,
                    total_phev=total_phev,
                    total_vehicles=None,  # requer cruzamento com total geral
                )
            )

        self.logger.debug(
            "SENATRAN parsed: %d municípios com EVs agregados", len(records)
        )
        return records

    def upsert(
        self,
        session: Session,
        records: list[StgVehicleFleet],
    ) -> tuple[int, int]:
        if not records:
            return 0, 0

        rows = [
            {
                "source": r.source,
                "reference_month": r.reference_month,
                "municipality_code": r.municipality_code,
                "raw_json": r.raw_json,
                "total_ev": r.total_ev,
                "total_phev": r.total_phev,
                "total_vehicles": r.total_vehicles,
                "is_processed": False,
            }
            for r in records
        ]

        stmt = pg_insert(StgVehicleFleet).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stg_fleet_source_mun_month",
            set_={
                "raw_json": stmt.excluded.raw_json,
                "total_ev": stmt.excluded.total_ev,
                "total_phev": stmt.excluded.total_phev,
                "total_vehicles": stmt.excluded.total_vehicles,
                "is_processed": False,
                "ingested_at": stmt.excluded.ingested_at,
            },
        )

        result = session.execute(stmt)
        self.logger.info("SENATRAN upsert: %d linhas afetadas", result.rowcount)
        return result.rowcount, 0
