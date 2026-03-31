"""
pipeline/sources/energy.py
Ingestor para leituras de carga da rede elétrica.
Suporta CSVs do ONS e formato genérico de concessionárias.

ONS (Operador Nacional do Sistema Elétrico):
  Dados históricos de carga em:
  https://www.ons.org.br/Paginas/resultados-da-operacao/historico-da-operacao/
  Arquivos: CARGA_ENERGIA_XXXXX.csv

Concessionárias: formato customizável via mapeamento de colunas.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from models.staging import StgEnergyReading
from pipeline.base_ingester import BaseIngester

settings = get_settings()


# Mapeamento padrão de colunas ONS
ONS_COLUMN_MAP = {
    "din_instante": "timestamp",
    "nom_subsistema": "region_code",
    "val_cargaenergiamwmed": "load_mw",
}


def _parse_timestamp(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class EnergyReadingIngester(BaseIngester):
    source_name = "ons"

    def __init__(self, source_name: str = "ons") -> None:
        self.source_name = source_name
        super().__init__()

    def fetch_raw(
        self,
        csv_path: str | Path,
        column_map: dict[str, str] | None = None,
        encoding: str = "utf-8",
        delimiter: str = ";",
        region_code_override: str | None = None,
        municipality_ibge_code: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Lê CSV de carga energética.

        Args:
          csv_path              → arquivo CSV do ONS ou concessionária
          column_map            → {coluna_csv: campo_interno}
                                  campos internos: timestamp, region_code, load_mw,
                                  capacity_mw, voltage_kv, frequency_hz
          region_code_override  → usa este código para toda a leitura
          municipality_ibge_code→ associa leituras a um município
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV de energia não encontrado: {path}")

        col_map = column_map or ONS_COLUMN_MAP
        records: list[dict[str, Any]] = []

        with open(path, newline="", encoding=encoding, errors="replace") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            for row in reader:
                normalized = {k.strip().lower(): v.strip() for k, v in row.items() if k}

                # Aplica mapeamento de colunas
                mapped: dict[str, Any] = {"_raw": dict(row)}
                for csv_col, internal_key in col_map.items():
                    if csv_col.lower() in normalized:
                        mapped[internal_key] = normalized[csv_col.lower()]

                mapped["_region_code_override"] = region_code_override
                mapped["_municipality_ibge"] = municipality_ibge_code
                records.append(mapped)

        self.logger.info(
            "%s: %d leituras lidas de %s", self.source_name, len(records), path
        )
        return records

    def parse(self, raw_records: list[dict[str, Any]]) -> list[StgEnergyReading]:
        records: list[StgEnergyReading] = []

        for item in raw_records:
            try:
                ts = _parse_timestamp(item.get("timestamp", ""))
                if not ts:
                    self.logger.debug("Ignorando linha sem timestamp válido")
                    continue

                region = (
                    item.get("_region_code_override")
                    or item.get("region_code", "")
                ).strip()

                if not region:
                    self.logger.debug("Ignorando linha sem region_code")
                    continue

                load_raw = item.get("load_mw")
                load_mw = float(str(load_raw).replace(",", ".")) if load_raw else None

                cap_raw = item.get("capacity_mw")
                capacity_mw = float(str(cap_raw).replace(",", ".")) if cap_raw else None

                volt_raw = item.get("voltage_kv")
                voltage_kv = float(str(volt_raw).replace(",", ".")) if volt_raw else None

                freq_raw = item.get("frequency_hz")
                frequency_hz = float(str(freq_raw).replace(",", ".")) if freq_raw else None

                records.append(
                    StgEnergyReading(
                        source=self.source_name,
                        region_code=region,
                        reading_at=ts,
                        raw_json=item.get("_raw", {}),
                        load_mw=load_mw,
                        available_capacity_mw=capacity_mw,
                    )
                )
            except Exception as exc:
                self.logger.warning("Erro ao parsear leitura de energia: %s", exc)

        self.logger.debug(
            "%s parsed: %d leituras válidas de %d",
            self.source_name, len(records), len(raw_records),
        )
        return records

    def upsert(
        self,
        session: Session,
        records: list[StgEnergyReading],
    ) -> tuple[int, int]:
        if not records:
            return 0, 0

        batch_size = settings.pipeline_batch_size
        total = 0

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            rows = [
                {
                    "source": r.source,
                    "region_code": r.region_code,
                    "reading_at": r.reading_at,
                    "raw_json": r.raw_json,
                    "load_mw": r.load_mw,
                    "available_capacity_mw": r.available_capacity_mw,
                    "is_processed": False,
                }
                for r in batch
            ]

            stmt = pg_insert(StgEnergyReading).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stg_energy_source_region_ts",
                set_={
                    "raw_json": stmt.excluded.raw_json,
                    "load_mw": stmt.excluded.load_mw,
                    "available_capacity_mw": stmt.excluded.available_capacity_mw,
                    "is_processed": False,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )

            result = session.execute(stmt)
            total += result.rowcount

        self.logger.info("%s upsert: %d linhas afetadas", self.source_name, total)
        return total, 0
