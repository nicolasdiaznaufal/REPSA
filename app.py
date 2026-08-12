"""
Interfaz de análisis histórico de PML (CENACE).

Uso:
    streamlit run app.py

Para compartir con colegas en la oficina, correr en un servidor/VM interno:
    streamlit run app.py --server.address 0.0.0.0 --server.port 8501
Luego cada quien entra desde su navegador a http://<ip-del-servidor>:8501
"""
import os
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(__file__), ".", "db", "pml.duckdb")

st.set_page_config(page_title="PML Histórico", layout="wide")


@st.cache_resource
def get_connection():
    return duckdb.connect(DB_PATH, read_only=True)


@st.cache_data(ttl=600)
def cargar_nodos():
    con = get_connection()
    return con.execute("SELECT DISTINCT nodo FROM pml_hourly ORDER BY nodo").df()["nodo"].tolist()


@st.cache_data(ttl=600)
def cargar_anios():
    con = get_connection()
    return sorted(con.execute("SELECT DISTINCT anio FROM pml_hourly ORDER BY anio").df()["anio"].tolist())


@st.cache_data(ttl=600)
def rango_fechas():
    con = get_connection()
    r = con.execute("SELECT MIN(fecha), MAX(fecha) FROM pml_hourly").fetchone()
    return r[0], r[1]


st.title("⚡ Análisis Histórico de Precios Marginales Locales (PML)")

con = get_connection()
nodos_disponibles = cargar_nodos()
anios_disponibles = cargar_anios()
fecha_min, fecha_max = rango_fechas()

st.caption(f"Base de datos: {len(nodos_disponibles):,} nodos ; datos del {fecha_min} al {fecha_max}")

st.sidebar.header("Filtros")
nodos_sel = st.sidebar.multiselect(
    "Nodo(s)", nodos_disponibles,
    default=nodos_disponibles[:2] if len(nodos_disponibles) >= 2 else nodos_disponibles,
)
anio_sel = st.sidebar.selectbox("Año (opcional, para vistas por hora)", ["Todos"] + anios_disponibles)
anio_filtro = None if anio_sel == "Todos" else anio_sel

if not nodos_sel:
    st.warning("Selecciona al menos un nodo en el panel izquierdo.")
    st.stop()

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Precio promedio por hora",
    "Comparación de nodos",
    "Evolución temporal",
    "Spread solar vs punta",
    "Mapa de México",
    "Canibalización solar",
    "Curva de duración",
    "Descomposición PML",
])

# --- 1. Precio promedio por hora del día ---
with tab1:
    st.subheader("Precio promedio por hora del día")
    filtro_anio = f"AND anio = {anio_filtro}" if anio_filtro else ""
    q = f"""
        SELECT nodo, hora, ROUND(AVG(pml), 2) AS pml_promedio
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)}) {filtro_anio}
        GROUP BY nodo, hora
        ORDER BY nodo, hora
    """
    df = con.execute(q).df()
    fig = px.line(df, x="hora", y="pml_promedio", color="nodo", markers=True,
                   labels={"hora": "Hora del día", "pml_promedio": "PML promedio ($/MWh)"})
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True)

# --- 2. Comparación entre nodos ---
with tab2:
    st.subheader("Comparación estadística entre nodos")
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
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)})
        GROUP BY nodo
        ORDER BY pml_promedio DESC
    """
    df = con.execute(q).df()
    fig = px.bar(df, x="nodo", y="pml_promedio", color="nodo",
                  labels={"pml_promedio": "PML promedio ($/MWh)"})
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True)
    st.caption("Congestión promedio positiva = el nodo suele estar 'importando' costo por restricciones de red; "
               "negativa = suele beneficiarse de ellas.")

# --- 3. Evolución temporal (año o mes) ---
with tab3:
    st.subheader("Evolución del PML a través del tiempo")

    granularidad = st.radio(
        "Ver por:", ["Mes", "Año"], horizontal=True,
        help="Usa 'Mes' para comparar dentro del mismo año (ej. enero vs. julio). "
             "Usa 'Año' cuando ya tengas varios años cargados.",
    )

    if granularidad == "Mes":
        q = f"""
            SELECT nodo, anio, mes,
                strftime(make_date(anio, mes, 1), '%Y-%m') AS periodo,
                ROUND(AVG(pml), 2) AS pml_promedio
            FROM pml_hourly
            WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)})
            GROUP BY nodo, anio, mes
            ORDER BY nodo, anio, mes
        """
        df = con.execute(q).df()
        fig = px.line(df, x="periodo", y="pml_promedio", color="nodo", markers=True,
                       labels={"periodo": "Mes", "pml_promedio": "PML promedio ($/MWh)"})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df, use_container_width=True)
        if df["periodo"].nunique() <= 1:
            st.info("Solo hay un mes cargado todavía. Corre ingest.py con más archivos para ver la comparación mensual.")
    else:
        q = f"""
            SELECT nodo, anio, ROUND(AVG(pml), 2) AS pml_promedio_anual
            FROM pml_hourly
            WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)})
            GROUP BY nodo, anio
            ORDER BY nodo, anio
        """
        df = con.execute(q).df()
        fig = px.line(df, x="anio", y="pml_promedio_anual", color="nodo", markers=True,
                       labels={"anio": "Año", "pml_promedio_anual": "PML promedio anual ($/MWh)"})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df, use_container_width=True)
        if len(anios_disponibles) <= 1:
            st.info("Solo hay un año cargado todavía. Esta vista se vuelve útil conforme cargues datos de más años.")

# --- 4. Spread solar vs punta ---
with tab4:
    st.subheader("Spread horario: solar (6h-18h) vs punta (18h-22h)")
    resultados = []
    for nodo in nodos_sel:
        filtro_anio = f"AND anio = {anio_filtro}" if anio_filtro else ""
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
            SELECT periodo, ROUND(AVG(pml), 2) AS pml_promedio
            FROM clasificado
            WHERE periodo IN ('solar', 'punta')
            GROUP BY periodo
        """
        df_nodo = con.execute(q).df()
        df_nodo["nodo"] = nodo
        resultados.append(df_nodo)

    df = pd.concat(resultados, ignore_index=True)
    fig = px.bar(df, x="nodo", y="pml_promedio", color="periodo", barmode="group",
                  labels={"pml_promedio": "PML promedio ($/MWh)", "periodo": "Periodo"})
    st.plotly_chart(fig, use_container_width=True)

    resumen = df.pivot(index="nodo", columns="periodo", values="pml_promedio").reset_index()
    if "solar" in resumen.columns and "punta" in resumen.columns:
        resumen["spread ($/MWh)"] = (resumen["punta"] - resumen["solar"]).round(2)
        resumen["spread (%)"] = ((resumen["punta"] / resumen["solar"] - 1) * 100).round(1)
    st.dataframe(resumen, use_container_width=True)
    st.caption("Un spread alto sugiere valor para almacenamiento/despacho en hora punta en ese nodo.")

# --- 5. Mapa de México con columnas 3D ---
with tab5:
    st.subheader("Mapa de nodos: métrica seleccionada, columnas 3D")

    tabla_existe = con.execute("""
        SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'nodos_ubicacion'
    """).fetchone()[0]

    if not tabla_existe:
        st.warning(
            "Aún no existe la tabla `nodos_ubicacion`. Corre `python geolocalizar_nodos.py "
            "\"<catálogo de nodos CENACE>.xlsx\"` una vez para generarla."
        )
    else:
        metrica = st.radio(
            "Métrica a mostrar",
            ["Precio promedio (PML)", "Spread solar-punta", "Congestión promedio"],
            horizontal=True,
        )
        estado_filtro = st.selectbox(
            "Filtrar por estado (opcional)",
            ["Todos"] + sorted(con.execute(
                "SELECT DISTINCT entidad_federativa FROM nodos_ubicacion ORDER BY 1"
            ).df()["entidad_federativa"].tolist())
        )
        filtro_sql = f"AND u.entidad_federativa = '{estado_filtro}'" if estado_filtro != "Todos" else ""

        if metrica == "Precio promedio (PML)":
            q_mapa = f"""
                SELECT u.nodo, u.nombre, u.entidad_federativa, u.municipio, u.lat, u.lon,
                       ROUND(AVG(p.pml), 2) AS valor
                FROM pml_hourly p
                JOIN nodos_ubicacion u ON p.nodo = u.nodo
                WHERE 1=1 {filtro_sql}
                GROUP BY u.nodo, u.nombre, u.entidad_federativa, u.municipio, u.lat, u.lon
            """
            etiqueta_valor = "PML promedio ($/MWh)"
        elif metrica == "Congestión promedio":
            q_mapa = f"""
                SELECT u.nodo, u.nombre, u.entidad_federativa, u.municipio, u.lat, u.lon,
                       ROUND(AVG(p.congestion), 2) AS valor
                FROM pml_hourly p
                JOIN nodos_ubicacion u ON p.nodo = u.nodo
                WHERE 1=1 {filtro_sql}
                GROUP BY u.nodo, u.nombre, u.entidad_federativa, u.municipio, u.lat, u.lon
            """
            etiqueta_valor = "Congestión promedio ($/MWh)"
        else:  # Spread solar-punta
            q_mapa = f"""
                WITH clasificado AS (
                    SELECT p.*, u.nombre, u.entidad_federativa, u.municipio, u.lat, u.lon,
                        CASE WHEN hora >= 6 AND hora < 18 THEN 'solar'
                             WHEN hora >= 18 AND hora < 22 THEN 'punta'
                             ELSE 'otro' END AS periodo
                    FROM pml_hourly p
                    JOIN nodos_ubicacion u ON p.nodo = u.nodo
                    WHERE 1=1 {filtro_sql}
                ),
                agregado AS (
                    SELECT nodo, nombre, entidad_federativa, municipio, lat, lon, periodo,
                           AVG(pml) AS pml_promedio
                    FROM clasificado
                    WHERE periodo IN ('solar', 'punta')
                    GROUP BY nodo, nombre, entidad_federativa, municipio, lat, lon, periodo
                )
                SELECT nodo, nombre, entidad_federativa, municipio, lat, lon,
                       ROUND(MAX(CASE WHEN periodo='punta' THEN pml_promedio END) -
                             MAX(CASE WHEN periodo='solar' THEN pml_promedio END), 2) AS valor
                FROM agregado
                GROUP BY nodo, nombre, entidad_federativa, municipio, lat, lon
            """
            etiqueta_valor = "Spread punta - solar ($/MWh)"

        df_mapa = con.execute(q_mapa).df().dropna(subset=["valor"])

        if df_mapa.empty:
            st.info("No hay datos para mostrar con este filtro.")
        else:
            # Normalizar altura de columnas (pydeck espera magnitudes razonables, no $/MWh directo)
            valor_min, valor_max = df_mapa["valor"].min(), df_mapa["valor"].max()
            rango = max(valor_max - valor_min, 1e-6)
            df_mapa["altura"] = ((df_mapa["valor"] - valor_min) / rango) * 80000 + 5000

            # Color: rojo = valor alto, azul = valor bajo
            df_mapa["color_r"] = (((df_mapa["valor"] - valor_min) / rango) * 255).astype(int)
            df_mapa["color_b"] = (255 - ((df_mapa["valor"] - valor_min) / rango) * 255).astype(int)

            capa = pdk.Layer(
                "ColumnLayer",
                data=df_mapa,
                get_position=["lon", "lat"],
                get_elevation="altura",
                elevation_scale=1,
                radius=8000,
                get_fill_color=["color_r", "50", "color_b", 180],
                pickable=True,
                auto_highlight=True,
            )

            vista = pdk.ViewState(
                longitude=-102.5, latitude=23.6, zoom=4.3, pitch=45,
            )

            tooltip = {
                "html": "<b>{nombre}</b> ({nodo})<br/>{municipio}, {entidad_federativa}<br/>"
                        f"{etiqueta_valor}: " + "{valor}",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            }

            st.pydeck_chart(pdk.Deck(
                layers=[capa], initial_view_state=vista, tooltip=tooltip,
                map_style="mapbox://styles/mapbox/light-v9",
            ))

            st.caption(
                f"{len(df_mapa)} nodos mostrados. Altura y color de columna = {etiqueta_valor}. "
                "Ubicación a nivel de cabecera municipal (CENACE no publica coordenadas exactas de subestación)."
            )
            with st.expander("Ver tabla de datos"):
                st.dataframe(
                    df_mapa[["nodo", "nombre", "entidad_federativa", "municipio", "valor"]]
                    .sort_values("valor", ascending=False),
                    use_container_width=True
                )

# --- 6. Riesgo de canibalización solar ---
with tab6:
    st.subheader("Riesgo de canibalización solar")
    st.caption(
        "El tema más relevante del mercado mexicano en 2026: mide qué tan seguido el "
        "precio se desploma justo cuando hay más generación solar, y compara el precio "
        "que 'capturaría' un proyecto solar contra el promedio simple del mismo periodo."
    )

    filtro_anio_canib = f"AND anio = {anio_filtro}" if anio_filtro else ""

    q_negativas = f"""
        SELECT nodo, anio,
            COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16) AS horas_solares_centrales,
            COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) AS horas_negativas,
            ROUND(100.0 * COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) /
                  NULLIF(COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16), 0), 2) AS pct_horas_negativas
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)}) {filtro_anio_canib}
        GROUP BY nodo, anio
        ORDER BY nodo, anio
    """
    df_negativas = con.execute(q_negativas).df()

    q_captura = f"""
        WITH perfil_fv AS (
            SELECT hora,
                CASE WHEN hora >= 6 AND hora < 18
                     THEN SIN(PI() * (hora - 6) / 12.0)
                     ELSE 0 END AS peso
            FROM (SELECT DISTINCT hora FROM pml_hourly)
        ),
        promedio_por_hora AS (
            SELECT nodo, hora, AVG(pml) AS pml_promedio
            FROM pml_hourly
            WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)}) {filtro_anio_canib}
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
    if not df_captura.empty:
        df_captura["captura_vs_simple_pct"] = (
            (df_captura["precio_captura_solar"] / df_captura["precio_promedio_simple_solar"] - 1) * 100
        ).round(1)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**% de horas con PML ≤ 0 (10h-16h)**")
        st.dataframe(df_negativas, use_container_width=True)
        fig_neg = px.bar(df_negativas, x="nodo", y="pct_horas_negativas", color="nodo",
                          labels={"pct_horas_negativas": "% horas con PML ≤ 0"})
        st.plotly_chart(fig_neg, use_container_width=True)

    with col2:
        st.markdown("**Precio de captura solar vs. promedio simple**")
        st.dataframe(df_captura, use_container_width=True)
        if not df_captura.empty:
            fig_cap = go.Figure()
            fig_cap.add_bar(name="Precio de captura", x=df_captura["nodo"], y=df_captura["precio_captura_solar"])
            fig_cap.add_bar(name="Promedio simple", x=df_captura["nodo"], y=df_captura["precio_promedio_simple_solar"])
            fig_cap.update_layout(barmode="group", yaxis_title="$/MWh")
            st.plotly_chart(fig_cap, use_container_width=True)

    st.caption(
        "El precio de captura pondera cada hora por un perfil típico de generación FV "
        "(campana entre 6h y 18h, pico al mediodía) — es una aproximación estándar, no "
        "la curva de generación real del proyecto. Si el precio de captura es menor al "
        "promedio simple, es señal de canibalización: el proyecto generaría más justo "
        "cuando el precio está más deprimido."
    )

# --- 7. Curva de duración de precios ---
with tab7:
    st.subheader("Curva de duración de precios")
    st.caption(
        "Todas las horas del periodo ordenadas de mayor a menor precio. Muestra la "
        "volatilidad completa, no solo el promedio — cuántas horas son realmente caras "
        "vs. cuántas sostienen el promedio."
    )

    fig_curva = go.Figure()
    for nodo in nodos_sel:
        filtro_anio_curva = f"AND anio = {anio_filtro}" if anio_filtro else ""
        q_curva = f"""
            SELECT pml,
                ROUND(PERCENT_RANK() OVER (ORDER BY pml DESC) * 100, 2) AS percentil
            FROM pml_hourly
            WHERE nodo = '{nodo}' {filtro_anio_curva}
            ORDER BY pml DESC
        """
        df_curva = con.execute(q_curva).df()
        fig_curva.add_trace(go.Scatter(x=df_curva["percentil"], y=df_curva["pml"],
                                        mode="lines", name=nodo))

    fig_curva.update_layout(
        xaxis_title="Percentil de horas (0% = hora más cara)",
        yaxis_title="PML ($/MWh)",
    )
    st.plotly_chart(fig_curva, use_container_width=True)
    st.caption(
        "Una curva empinada al inicio indica que unas pocas horas extremas concentran "
        "gran parte del valor (típico de nodos con congestión puntual). Una curva plana "
        "indica precios más consistentes a lo largo del periodo."
    )

# --- 8. Descomposición del PML ---
with tab8:
    st.subheader("Descomposición del PML: Energía + Pérdidas + Congestión")
    st.caption(
        "CENACE publica los 3 componentes por separado. Permite argumentar si un nodo "
        "es caro por congestión (posiblemente temporal, ligado a la red) o por energía "
        "(más estructural, ligado al mix de generación de la región)."
    )

    filtro_anio_desc = f"AND anio = {anio_filtro}" if anio_filtro else ""
    q_desc = f"""
        SELECT nodo,
            ROUND(AVG(energia), 2) AS energia,
            ROUND(AVG(perdidas), 2) AS perdidas,
            ROUND(AVG(congestion), 2) AS congestion,
            ROUND(AVG(pml), 2) AS pml_total
        FROM pml_hourly
        WHERE nodo IN ({','.join(f"'{n}'" for n in nodos_sel)}) {filtro_anio_desc}
        GROUP BY nodo
        ORDER BY pml_total DESC
    """
    df_desc = con.execute(q_desc).df()

    fig_desc = go.Figure()
    fig_desc.add_bar(name="Energía", x=df_desc["nodo"], y=df_desc["energia"])
    fig_desc.add_bar(name="Pérdidas", x=df_desc["nodo"], y=df_desc["perdidas"])
    fig_desc.add_bar(name="Congestión", x=df_desc["nodo"], y=df_desc["congestion"])
    fig_desc.update_layout(barmode="relative", yaxis_title="$/MWh",
                            title="Componentes promedio del PML por nodo")
    st.plotly_chart(fig_desc, use_container_width=True)
    st.dataframe(df_desc, use_container_width=True)
    st.caption(
        "Nota: 'relative' apila positivos y negativos correctamente — la congestión "
        "puede ser negativa (el nodo se beneficia de la restricción de red)."
    )