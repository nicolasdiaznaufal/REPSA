"""
Consultas de análisis sobre la base pml.duckdb.
Cada función corresponde a una de las preguntas de negocio.
"""
import os
import duckdb

DB_PATH = os.path.join(os.path.dirname(__file__), ".", "db", "pml.duckdb")


def connect():
    return duckdb.connect(DB_PATH, read_only=True)


# 1. Precio promedio por hora del día, para uno o varios nodos
def precio_promedio_por_hora(con, nodos: list[str], anio: int | None = None):
    filtro_anio = f"AND anio = {anio}" if anio else ""
    q = f"""
        SELECT nodo, hora, ROUND(AVG(pml), 2) AS pml_promedio
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)})
        {filtro_anio}
        GROUP BY nodo, hora
        ORDER BY nodo, hora
    """
    return con.execute(q).df()


# 2. Comparación entre dos o más nodos (resumen estadístico)
def comparar_nodos(con, nodos: list[str]):
    q = f"""
        SELECT
            nodo,
            COUNT(*) AS horas_observadas,
            ROUND(AVG(pml), 2) AS pml_promedio,
            ROUND(MEDIAN(pml), 2) AS pml_mediana,
            ROUND(MIN(pml), 2) AS pml_min,
            ROUND(MAX(pml), 2) AS pml_max,
            ROUND(STDDEV(pml), 2) AS pml_desviacion,
            ROUND(AVG(congestion), 2) AS congestion_promedio
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)})
        GROUP BY nodo
        ORDER BY pml_promedio DESC
    """
    return con.execute(q).df()


# 3. Evolución del PML a través de los años (promedio anual simple)
def evolucion_anual(con, nodos: list[str]):
    q = f"""
        SELECT nodo, anio, ROUND(AVG(pml), 2) AS pml_promedio_anual
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)})
        GROUP BY nodo, anio
        ORDER BY nodo, anio
    """
    return con.execute(q).df()


# 3b. Evolución mensual (útil cuando todos los datos caen en el mismo año)
def evolucion_mensual(con, nodos: list[str]):
    q = f"""
        SELECT nodo, anio, mes,
            strftime(make_date(anio, mes, 1), '%Y-%m') AS periodo,
            ROUND(AVG(pml), 2) AS pml_promedio
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)})
        GROUP BY nodo, anio, mes
        ORDER BY nodo, anio, mes
    """
    return con.execute(q).df()


# 4. Spread solar (6h-18h) vs hora punta (18h-22h)
def spread_solar_punta(con, nodo: str, anio: int | None = None):
    filtro_anio = f"AND anio = {anio}" if anio else ""
    q = f"""
        WITH clasificado AS (
            SELECT *,
                CASE
                    WHEN hora >= 6 AND hora < 18 THEN 'solar'
                    WHEN hora >= 18 AND hora < 22 THEN 'punta'
                    ELSE 'otro'
                END AS periodo
            FROM pml_hourly
            WHERE nodo = '{nodo}' {filtro_anio}
        )
        SELECT periodo, ROUND(AVG(pml), 2) AS pml_promedio, COUNT(*) AS n_horas
        FROM clasificado
        WHERE periodo IN ('solar', 'punta')
        GROUP BY periodo
    """
    df = con.execute(q).df()
    if len(df) == 2:
        solar = df.loc[df.periodo == 'solar', 'pml_promedio'].values[0]
        punta = df.loc[df.periodo == 'punta', 'pml_promedio'].values[0]
        spread = round(punta - solar, 2)
        spread_pct = round((punta / solar - 1) * 100, 1) if solar else None
        print(f"Spread punta-solar en {nodo}: {spread} $/MWh ({spread_pct}% sobre el precio solar)")
    return df


# 5. Riesgo de canibalización solar
def canibalizacion_solar(con, nodos: list[str], anio: int | None = None):
    """% de horas con PML <= 0 en horas solares centrales (10h-16h) por nodo/año,
    y precio de captura solar (ponderado por perfil típico de generación FV)
    vs. precio promedio simple del mismo periodo."""
    filtro_anio = f"AND anio = {anio}" if anio else ""

    q_negativas = f"""
        SELECT nodo, anio,
            COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16) AS horas_solares_centrales,
            COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) AS horas_negativas,
            ROUND(100.0 * COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) /
                  NULLIF(COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16), 0), 2) AS pct_horas_negativas
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)}) {filtro_anio}
        GROUP BY nodo, anio
        ORDER BY nodo, anio
    """
    df_negativas = con.execute(q_negativas).df()

    q_captura = f"""
        WITH perfil_fv AS (
            -- Perfil típico de generación solar: campana entre 6h y 18h, pico al mediodía.
            -- Aproximación estándar en ausencia de datos reales de generación por nodo.
            SELECT hora,
                CASE WHEN hora >= 6 AND hora < 18
                     THEN SIN(PI() * (hora - 6) / 12.0)
                     ELSE 0 END AS peso
            FROM (SELECT DISTINCT hora FROM pml_hourly)
        ),
        promedio_por_hora AS (
            SELECT nodo, hora, AVG(pml) AS pml_promedio
            FROM pml_hourly
            WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)}) {filtro_anio}
            GROUP BY nodo, hora
        )
        SELECT
            p.nodo,
            ROUND(SUM(p.pml_promedio * f.peso) / NULLIF(SUM(f.peso), 0), 2) AS precio_captura_solar,
            ROUND(AVG(p.pml_promedio) FILTER (WHERE p.hora >= 6 AND p.hora < 18), 2) AS precio_promedio_simple_solar
        FROM promedio_por_hora p
        JOIN perfil_fv f ON p.hora = f.hora
        GROUP BY p.nodo
    """
    df_captura = con.execute(q_captura).df()
    df_captura["captura_vs_simple_pct"] = (
        (df_captura["precio_captura_solar"] / df_captura["precio_promedio_simple_solar"] - 1) * 100
    ).round(1)

    return df_negativas, df_captura


# 6. Curva de duración de precios
def curva_duracion_precios(con, nodo: str, anio: int | None = None):
    """PML de cada hora del periodo, ordenado de mayor a menor, con percentil asociado."""
    filtro_anio = f"AND anio = {anio}" if anio else ""
    q = f"""
        SELECT nodo, fecha, hora, pml,
            ROW_NUMBER() OVER (ORDER BY pml DESC) AS orden,
            ROUND(PERCENT_RANK() OVER (ORDER BY pml DESC) * 100, 2) AS percentil
        FROM pml_hourly
        WHERE nodo = '{nodo}' {filtro_anio}
        ORDER BY pml DESC
    """
    return con.execute(q).df()


# 7. Descomposición del PML en Energía + Pérdidas + Congestión
def descomposicion_pml(con, nodos: list[str], anio: int | None = None):
    filtro_anio = f"AND anio = {anio}" if anio else ""
    q = f"""
        SELECT nodo,
            ROUND(AVG(energia), 2) AS energia_promedio,
            ROUND(AVG(perdidas), 2) AS perdidas_promedio,
            ROUND(AVG(congestion), 2) AS congestion_promedio,
            ROUND(AVG(pml), 2) AS pml_promedio
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos)}) {filtro_anio}
        GROUP BY nodo
        ORDER BY pml_promedio DESC
    """
    return con.execute(q).df()


if __name__ == "__main__":
    con = connect()
    nodos_muestra = con.execute(
        "SELECT DISTINCT nodo FROM pml_hourly ORDER BY nodo LIMIT 2"
    ).fetchall()
    nodos_muestra = [n[0] for n in nodos_muestra]
    print(f"Nodos de muestra: {nodos_muestra}\n")

    print("=== 1. Precio promedio por hora del día ===")
    print(precio_promedio_por_hora(con, nodos_muestra).head(10))

    print("\n=== 2. Comparación entre nodos ===")
    print(comparar_nodos(con, nodos_muestra))

    print("\n=== 3. Evolución anual ===")
    print(evolucion_anual(con, nodos_muestra))

    print("\n=== 4. Spread solar vs punta ===")
    spread_solar_punta(con, nodos_muestra[0])

    print("\n=== 5. Canibalización solar ===")
    df_neg, df_cap = canibalizacion_solar(con, nodos_muestra)
    print(df_neg)
    print(df_cap)

    print("\n=== 6. Curva de duración de precios (primeras 5 filas) ===")
    print(curva_duracion_precios(con, nodos_muestra[0]).head())

    print("\n=== 7. Descomposición del PML ===")
    print(descomposicion_pml(con, nodos_muestra))

    con.close()