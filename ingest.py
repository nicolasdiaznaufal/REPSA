"""
Ingesta de archivos CENACE (Precios Marginales Locales - MDA) hacia DuckDB.

Uso:
    python ingest.py archivo1.csv archivo2.csv ...
    python ingest.py /ruta/a/carpeta_con_csvs/

Es idempotente: si un (fecha, hora, nodo) ya existe en la base, no se duplica.
Puedes correrlo cada vez que descargues nuevos archivos de CENACE.
"""

import sys
import glob
import os
import duckdb
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), ".", "db", "pml.duckdb")
CENACE_HEADER_ROWS = 8  # líneas de metadata antes del encabezado real


def parse_cenace_csv(path: str) -> pd.DataFrame:
    """Lee un CSV de CENACE (formato 'PreciosMargLocales_SIN_MDA_Mes_...') y
    devuelve un DataFrame limpio y tipado."""
    df = pd.read_csv(
        path,
        skiprows=CENACE_HEADER_ROWS,
        header=None,
        names=["fecha", "hora", "nodo", "pml", "energia", "perdidas", "congestion"],
        engine="python",
        on_bad_lines="warn",
    )
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["hora"] = df["hora"].astype("Int64")  # CENACE usa horas 1-24
    df["archivo_origen"] = os.path.basename(path)

    before = len(df)
    df = df.dropna(subset=["fecha", "hora", "nodo", "pml"])
    dropped = before - len(df)
    if dropped:
        print(f"  [!] {dropped} filas descartadas por datos incompletos en {os.path.basename(path)}")

    return df


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pml_hourly (
            fecha DATE,
            hora INTEGER,
            nodo VARCHAR,
            pml DOUBLE,
            energia DOUBLE,
            perdidas DOUBLE,
            congestion DOUBLE,
            archivo_origen VARCHAR,
            anio INTEGER,
            mes INTEGER
        )
    """)
    con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pml_key
        ON pml_hourly (fecha, hora, nodo)
    """)
    return con


def ingest_file(con, path: str):
    print(f"Procesando {os.path.basename(path)} ...")
    df = parse_cenace_csv(path)
    df["anio"] = df["fecha"].dt.year
    df["mes"] = df["fecha"].dt.month

    con.register("staging_df", df)
    # INSERT ... ON CONFLICT DO NOTHING -> idempotente ante recargas
    con.execute("""
        INSERT INTO pml_hourly
        SELECT fecha, hora, nodo, pml, energia, perdidas, congestion, archivo_origen, anio, mes
        FROM staging_df
        ON CONFLICT (fecha, hora, nodo) DO NOTHING
    """)
    con.unregister("staging_df")
    print(f"  -> {len(df):,} filas leídas, {df['nodo'].nunique():,} nodos, "
          f"rango {df['fecha'].min().date()} a {df['fecha'].max().date()}")


def main(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "*.csv"))))
        else:
            files.append(p)

    if not files:
        print("No se encontraron archivos CSV.")
        return

    con = get_connection()
    for f in files:
        ingest_file(con, f)

    total = con.execute("SELECT COUNT(*) FROM pml_hourly").fetchone()[0]
    nodos = con.execute("SELECT COUNT(DISTINCT nodo) FROM pml_hourly").fetchone()[0]
    rango = con.execute("SELECT MIN(fecha), MAX(fecha) FROM pml_hourly").fetchone()
    print(f"\n=== Base de datos actualizada: {DB_PATH} ===")
    print(f"Total filas: {total:,} | Nodos distintos: {nodos:,} | Rango: {rango[0]} a {rango[1]}")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
