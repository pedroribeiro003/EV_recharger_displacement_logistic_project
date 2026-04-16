"""
export_parquet.py — Exporta todas as tabelas do banco para .parquet e zippa.

Uso no servidor:
    cd /opt/ev-demand && source venv/bin/activate
    python scripts/export_parquet.py

Depois transfira para sua máquina:
    scp user@servidor:/tmp/ev_export.zip ~/Desktop/ev_export.zip
"""

from __future__ import annotations

import os
import sys
import zipfile
import tempfile
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, ".")
from db.engine import engine  # noqa: E402

TABLES = [
    "stations",
    "connectors",
    "status_snapshots",
    "ibge_states",
    "ibge_municipalities",
    "anp_gas_stations",
    "anp_fuel_prices",
    "aneel_distributors",
    "aneel_tariffs",
    "aneel_outages",
    "senatran_fleet",
    "ipea_series",
    "ipea_values",
    "osm_pois",
    "station_poi_distances",
]

OUT_ZIP = Path("/tmp/ev_export.zip")


def export_table(table: str, conn) -> pd.DataFrame:
    df = pd.read_sql(text(f"SELECT * FROM {table}"), conn)
    # Colunas geometry (PostGIS) não são serializáveis — converte para WKT
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(1)
            if not sample.empty and hasattr(sample.iloc[0], "__geo_interface__"):
                df[col] = df[col].apply(lambda g: g.wkt if g is not None else None)
    return df


def main() -> None:
    print(f"Exportando {len(TABLES)} tabelas para {OUT_ZIP} ...")

    with engine.connect() as conn, zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in TABLES:
            try:
                df = export_table(table, conn)
                rows = len(df)
                with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
                    tmp_path = tmp.name
                df.to_parquet(tmp_path, index=False)
                zf.write(tmp_path, arcname=f"{table}.parquet")
                os.unlink(tmp_path)
                print(f"  ✓ {table:35s} {rows:>8,} linhas")
            except Exception as exc:
                print(f"  ✗ {table}: {exc}")

    size_mb = OUT_ZIP.stat().st_size / 1_048_576
    print(f"\nArquivo gerado: {OUT_ZIP}  ({size_mb:.1f} MB)")
    print(f"\nPara baixar:\n  scp user@servidor:{OUT_ZIP} ~/Desktop/ev_export.zip")


if __name__ == "__main__":
    main()
