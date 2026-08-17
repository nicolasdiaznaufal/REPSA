"""
Interfaz de análisis histórico de PML (CENACE).

Uso local:
    streamlit run app.py

Despliegue en Streamlit Community Cloud:
    La base de datos (db/pml.duckdb) NO vive en este repositorio de git —
    se descarga automáticamente al arrancar desde un GitHub Release.
    Ver README.md > "Despliegue" para la configuración completa.
"""
import os
import io
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import requests
import streamlit as st

# --- Configuración de descarga de la base de datos ---
# Ajusta estos tres valores a tu repositorio y release reales.
GITHUB_REPO = "nicolasdiaznaufal/REPSA"       # formato: "usuario/nombre-repo"
RELEASE_TAG = "datos-actuales"             # el tag del release que contiene el .duckdb
ASSET_NAME = "pml.duckdb"                  # nombre del archivo adjunto en el release

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "pml.duckdb")

st.set_page_config(page_title="PML Histórico", layout="wide")


def asegurar_base_datos():
    """Descarga pml.duckdb desde un GitHub Release si no existe localmente.

    Funciona con repos públicos y privados:
    - Público: descarga directa, sin autenticación.
    - Privado: requiere un token en st.secrets['github_token'] (Personal
      Access Token, permiso mínimo 'Contents: Read-only' sobre el repo).
      En Streamlit Community Cloud esto se configura en
      App settings > Secrets, no se sube a git.
    """
    if os.path.exists(DB_PATH):
        return  # ya está descargada (uso local, o ya se descargó en esta sesión del contenedor)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    token = st.secrets.get("github_token", None) if hasattr(st, "secrets") else None

    with st.spinner("Descargando base de datos más reciente..."):
        if token:
            # Repo privado: hay que resolver el asset por la API y pedirlo con
            # Accept: application/octet-stream para obtener el binario, no el JSON.
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{RELEASE_TAG}"
            headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
            r = requests.get(api_url, headers=headers, timeout=30)
            r.raise_for_status()
            release = r.json()
            asset = next((a for a in release["assets"] if a["name"] == ASSET_NAME), None)
            if asset is None:
                st.error(f"No se encontró el archivo '{ASSET_NAME}' en el release '{RELEASE_TAG}'.")
                st.stop()

            headers_download = {"Authorization": f"token {token}", "Accept": "application/octet-stream"}
            r2 = requests.get(asset["url"], headers=headers_download, timeout=120, stream=True)
            r2.raise_for_status()
            contenido = r2.content
        else:
            # Repo público: URL directa de descarga del release
            url = f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/{ASSET_NAME}"
            r = requests.get(url, timeout=120, stream=True)
            r.raise_for_status()
            contenido = r.content

        with open(DB_PATH, "wb") as f:
            f.write(contenido)


asegurar_base_datos()


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

st.caption(f"Base de datos: {len(nodos_disponibles):,} nodos · datos del {fecha_min} al {fecha_max}")

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

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "Precio promedio por hora",
    "Comparación de nodos",
    "Evolución temporal",
    "Spread solar vs punta",
    "Mapa de México",
    "Canibalización solar",
    "Curva de duración",
    "Descomposición PML",
    "Radiografía nacional",
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
                map_provider="carto", map_style="light",
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

# --- 9. Radiografía nacional (hallazgos clave a nivel sistema) ---
with tab9:
    st.subheader("Radiografía del mercado: hallazgos clave a nivel nacional")
    st.caption(
        "A diferencia de las demás pestañas, esta mira el sistema completo (todos los nodos), "
        "no solo los seleccionados en el panel izquierdo — sirve para detectar eventos y patrones "
        "de mercado antes de profundizar en un nodo o región específica. Respeta el filtro de año."
    )

    filtro_anio_solo = f"WHERE anio = {anio_filtro}" if anio_filtro else ""
    filtro_anio_p = f"WHERE p.anio = {anio_filtro}" if anio_filtro else ""

    # KPIs por año
    # KPIs por año (siempre todos los años, sin importar el filtro de la barra lateral)
    kpi_df = con.execute("""
        SELECT anio,
            ROUND(AVG(pml),1) AS "PML promedio",
            ROUND(MEDIAN(pml),1) AS "PML mediana",
            ROUND(MIN(pml),1) AS "Mínimo",
            ROUND(MAX(pml),1) AS "Máximo"
        FROM pml_hourly
        GROUP BY anio
        ORDER BY anio
    """).df()

    st.dataframe(kpi_df.set_index("anio"), use_container_width=True)

    fig_kpi = go.Figure()
    fig_kpi.add_bar(name="Promedio", x=kpi_df["anio"],   y=kpi_df["PML promedio"], marker_color="#0a3758")
    fig_kpi.add_bar(name="Mediana", x=kpi_df["anio"], y=kpi_df["PML mediana"], marker_color="#83450f")
    fig_kpi.update_layout(
    barmode="group",
    xaxis_title="Año",
    yaxis_title="$/MWh",
    xaxis=dict(type="category"),  # evita que trate 2025/2026 como escala continua con huecos
    title="PML promedio vs. mediana por año",
    )
    st.plotly_chart(fig_kpi, use_container_width=True)

    # 9.0b Descomposición del PML promedio por año
    st.markdown("#### Descomposición del PML promedio por año")
    st.caption(
        "Mismo principio que la pestaña 'Descomposición PML', pero agregado por año en vez de por "
        "nodo — muestra si el precio promedio de cada año fue impulsado por energía, pérdidas o "
        "congestión."
    )
    df_desc_anual = con.execute("""
        SELECT anio,
            ROUND(AVG(energia), 1) AS energia,
            ROUND(AVG(perdidas), 1) AS perdidas,
            ROUND(AVG(congestion), 1) AS congestion,
            ROUND(AVG(pml), 1) AS pml_total
        FROM pml_hourly GROUP BY anio ORDER BY anio
    """).df()
    fig_desc_anual = go.Figure()
    fig_desc_anual.add_bar(name="Energía", x=df_desc_anual["anio"], y=df_desc_anual["energia"])
    fig_desc_anual.add_bar(name="Pérdidas", x=df_desc_anual["anio"], y=df_desc_anual["perdidas"])
    fig_desc_anual.add_bar(name="Congestión", x=df_desc_anual["anio"], y=df_desc_anual["congestion"])
    fig_desc_anual.update_layout(
        barmode="relative", yaxis_title="$/MWh", xaxis_title="Año",
        xaxis=dict(type="category"),
        title="Componentes promedio del PML por año",
    )
    st.plotly_chart(fig_desc_anual, use_container_width=True)
    st.dataframe(df_desc_anual, use_container_width=True)
    st.caption(
        "'relative' apila positivos y negativos correctamente — pérdidas o congestión pueden ser "
        "negativas (el año/sistema se benefició de ellas en promedio)."
    )

    # 9.1 Evolución mensual: media vs mediana
    st.markdown("#### Evolución mensual: media vs. mediana")
    st.caption(
        "Una brecha grande entre media y mediana es señal de que unos pocos días u horas extremas "
        "están inflando el promedio del mes — no confundir con una tendencia sostenida."
    )
    df_mensual_nac = con.execute(f"""
        SELECT strftime(make_date(anio, mes, 1), '%Y-%m') AS periodo,
            ROUND(AVG(pml), 1) AS media, ROUND(MEDIAN(pml), 1) AS mediana
        FROM pml_hourly {filtro_anio_solo}
        GROUP BY anio, mes ORDER BY anio, mes
    """).df()
    fig_mensual_nac = go.Figure()
    fig_mensual_nac.add_scatter(x=df_mensual_nac["periodo"], y=df_mensual_nac["media"],
                                 mode="lines+markers", name="Media")
    fig_mensual_nac.add_scatter(x=df_mensual_nac["periodo"], y=df_mensual_nac["mediana"],
                                 mode="lines+markers", name="Mediana")
    fig_mensual_nac.update_layout(yaxis_title="$/MWh", xaxis_title="Mes")
    st.plotly_chart(fig_mensual_nac, use_container_width=True)

    # 9.1b Comparación año contra año (requiere más de un año cargado)
    anios_todos_nac = con.execute("SELECT DISTINCT anio FROM pml_hourly ORDER BY anio").df()["anio"].tolist()
    if len(anios_todos_nac) > 1:
        st.markdown("#### Comparación año contra año")
        st.caption(
            "Mismo mes, distintos años, superpuestos — permite distinguir un patrón que se repite "
            "cada año (estacional) de un evento propio de un solo año."
        )
        df_yoy = con.execute("""
            SELECT anio, mes, ROUND(AVG(pml), 1) AS pml_promedio
            FROM pml_hourly GROUP BY anio, mes ORDER BY anio, mes
        """).df()
        fig_yoy = px.line(df_yoy, x="mes", y="pml_promedio", color="anio", markers=True,
                           labels={"mes": "Mes", "pml_promedio": "PML promedio ($/MWh)", "anio": "Año"})
        fig_yoy.update_xaxes(dtick=1)
        st.plotly_chart(fig_yoy, use_container_width=True)

        resumen_anual = con.execute("""
            SELECT anio, ROUND(AVG(pml), 1) AS media, ROUND(MAX(pml), 1) AS maximo,
                COUNT(*) FILTER (WHERE pml >= 19000) AS horas_en_cap
            FROM pml_hourly GROUP BY anio ORDER BY anio
        """).df()
        anio_pico = resumen_anual.loc[resumen_anual["media"].idxmax()]
        otros_anios_txt = ", ".join(
            f"\\${row.media:,.0f} en {int(row.anio)}"
            for row in resumen_anual.itertuples() if row.anio != anio_pico["anio"]
        )
        st.warning(
            f"**{int(anio_pico['anio'])} fue un año de crisis, no un año típico.** Promedio nacional de "
            f"\\${anio_pico['media']:,.0f}/MWh (vs. {otros_anios_txt}), con "
            f"{int(anio_pico['horas_en_cap']):,} horas al tope del precio regulatorio (≥\\$19,000/MWh) — "
            "prácticamente el resto de los años combinados no llegó a ese nivel. Revisando la "
            "pestaña de Descomposición PML para ese periodo: el componente de **energía** (no "
            "congestión) es el que se dispara, señal de escasez real de generación/reserva — "
            "consistente con la crisis de calor y sequía que vivió México ese año — no un cuello de "
            "botella de red. Al usar un promedio histórico como referencia de 'precio típico', vale la "
            "pena excluir o marcar por separado los años de crisis."
        )

        st.markdown("**Descomposición del PML por mes, año contra año**")
        st.caption(
            "Mismo desglose de arriba, mes a mes: permite ver en qué meses el componente de "
            "energía se dispara (como mayo-junio 2024) frente a fluctuaciones normales de "
            "pérdidas y congestión."
        )
        df_desc_mes_yoy = con.execute("""
            SELECT anio, mes,
                ROUND(AVG(pml), 1) AS "PML",
                ROUND(AVG(energia), 1) AS "Energía",
                ROUND(AVG(perdidas), 1) AS "Pérdidas",
                ROUND(AVG(congestion), 1) AS "Congestión"
            FROM pml_hourly GROUP BY anio, mes ORDER BY anio, mes
        """).df()
        df_desc_mes_long = df_desc_mes_yoy.melt(
            id_vars=["anio", "mes"], value_vars=["PML", "Energía", "Pérdidas", "Congestión"],
            var_name="componente", value_name="valor",
        )
        fig_desc_mes = px.line(
            df_desc_mes_long, x="mes", y="valor", color="anio", facet_col="componente", facet_col_wrap=2,
            markers=True, labels={"mes": "Mes", "valor": "$/MWh", "anio": "Año"},
        )
        fig_desc_mes.update_yaxes(matches=None, showticklabels=True)
        fig_desc_mes.update_xaxes(dtick=1)
        fig_desc_mes.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        st.plotly_chart(fig_desc_mes, use_container_width=True)

        st.markdown("**Enero: el tramo de precios altos se repite cada año**")
        df_ene_yoy = con.execute("""
            SELECT anio, EXTRACT(day FROM fecha) AS dia, ROUND(AVG(pml), 1) AS pml_promedio
            FROM pml_hourly WHERE mes = 1 GROUP BY anio, dia ORDER BY anio, dia
        """).df()
        fig_ene_yoy = px.line(df_ene_yoy, x="dia", y="pml_promedio", color="anio", markers=True,
                               labels={"dia": "Día de enero", "pml_promedio": "PML promedio ($/MWh)", "anio": "Año"})
        st.plotly_chart(fig_ene_yoy, use_container_width=True)
        st.caption(
            "Cada enero cargado hasta ahora muestra un tramo de varios días con precios muy por encima "
            "del resto del mes — año con año, aunque en fechas distintas — probablemente ligado a la "
            "temporada de frío / restricción de gas natural. No es exclusivo de 2026: vale la pena "
            "presupuestar este riesgo cada invierno, no tratarlo como un evento de una sola vez."
        )

        st.markdown("**Perfil horario promedio por año**")
        df_hora_yoy = con.execute("""
            SELECT anio, hora, ROUND(AVG(pml), 1) AS pml_promedio
            FROM pml_hourly GROUP BY anio, hora ORDER BY anio, hora
        """).df()
        fig_hora_yoy = px.line(df_hora_yoy, x="hora", y="pml_promedio", color="anio", markers=True,
                                labels={"hora": "Hora del día", "pml_promedio": "PML promedio ($/MWh)", "anio": "Año"})
        fig_hora_yoy.update_xaxes(dtick=1)
        st.plotly_chart(fig_hora_yoy, use_container_width=True)
        st.caption(
            "La forma de esta curva —cuándo cae el precio por sobreoferta solar y cuándo sube en la "
            "punta de la tarde/noche— es la 'curva de pato' del sistema a nivel nacional. Compararla "
            "año con año muestra si se está profundizando (más solar instalado) o aplanando (más "
            "demanda o almacenamiento absorbiendo esas horas)."
        )

    # 9.2 Días con precio anómalo
    st.markdown("#### Días con precio anómalo")
    st.caption(
        "Un día se marca como anómalo si su PML promedio nacional supera 2x la mediana diaria del "
        "periodo cargado — típicamente eventos de restricción de oferta a nivel sistema, no de un "
        "nodo aislado. Considera excluirlos si buscas un precio 'típico'."
    )
    df_diario_nac = con.execute(f"""
        SELECT fecha, ROUND(AVG(pml), 1) AS pml_promedio,
            ROUND(100.0 * COUNT(*) FILTER (WHERE pml <= 0) / COUNT(*), 2) AS pct_horas_negativas
        FROM pml_hourly {filtro_anio_solo}
        GROUP BY fecha ORDER BY fecha
    """).df()
    if not df_diario_nac.empty:
        umbral = df_diario_nac["pml_promedio"].median() * 2
        df_diario_nac["evento"] = df_diario_nac["pml_promedio"] > umbral
        fig_diario_nac = px.bar(
            df_diario_nac, x="fecha", y="pml_promedio", color="evento",
            color_discrete_map={True: "#eb6834", False: "#2a78d6"},
            labels={"pml_promedio": "PML promedio nacional ($/MWh)", "fecha": "Fecha",
                    "evento": "Anómalo (> 2x mediana diaria)"},
        )
        st.plotly_chart(fig_diario_nac, use_container_width=True)
        dias_evento = df_diario_nac[df_diario_nac["evento"]]
        if not dias_evento.empty:
            st.info(
                f"{len(dias_evento)} día(s) marcados como anómalos, entre "
                f"{dias_evento['fecha'].min().date()} y {dias_evento['fecha'].max().date()}."
            )

    # 9.3 Canibalización solar: tendencia nacional
    st.markdown("#### Canibalización solar: tendencia nacional")
    st.caption(
        "% de horas con PML ≤ 0 en la ventana solar central (10h-16h), agregado a nivel nacional, "
        "mes a mes. Una caída fuerte en verano suele ser estacional (demanda de aire acondicionado "
        "absorbiendo la generación solar), no necesariamente una mejora estructural del mercado."
    )
    df_canib_nac = con.execute(f"""
        SELECT strftime(make_date(anio, mes, 1), '%Y-%m') AS periodo,
            ROUND(100.0 * COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) /
                  NULLIF(COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16), 0), 2) AS pct_negativas_solar
        FROM pml_hourly {filtro_anio_solo}
        GROUP BY anio, mes ORDER BY anio, mes
    """).df()
    fig_canib_nac = px.area(df_canib_nac, x="periodo", y="pct_negativas_solar",
                             labels={"periodo": "Mes", "pct_negativas_solar": "% horas ≤ $0 (10h-16h)"})
    st.plotly_chart(fig_canib_nac, use_container_width=True)

    if len(anios_todos_nac) > 1:
        st.markdown("**Por mes, año contra año: ¿la canibalización empeora con el tiempo?**")
        df_canib_yoy = con.execute("""
            SELECT anio, mes,
                ROUND(100.0 * COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16 AND pml <= 0) /
                      NULLIF(COUNT(*) FILTER (WHERE hora >= 10 AND hora < 16), 0), 2) AS pct_negativas_solar
            FROM pml_hourly GROUP BY anio, mes ORDER BY anio, mes
        """).df()
        fig_canib_yoy = px.line(df_canib_yoy, x="mes", y="pct_negativas_solar", color="anio", markers=True,
                                 labels={"mes": "Mes", "pct_negativas_solar": "% horas ≤ $0 (10h-16h)", "anio": "Año"})
        fig_canib_yoy.update_xaxes(dtick=1)
        st.plotly_chart(fig_canib_yoy, use_container_width=True)

        df_canib_pivot = df_canib_yoy.pivot(index="mes", columns="anio", values="pct_negativas_solar")
        anios_cols = sorted(df_canib_pivot.columns.tolist())
        meses_invierno = [m for m in [11, 12, 1, 2, 3] if m in df_canib_pivot.index]
        meses_verano = [m for m in [6, 7, 8] if m in df_canib_pivot.index]
        invierno_sube = len(anios_cols) > 1 and (
            df_canib_pivot.loc[meses_invierno, anios_cols[-1]].mean()
            > df_canib_pivot.loc[meses_invierno, anios_cols[0]].mean()
        ) if meses_invierno else False
        st.info(
            "En los meses de **verano (jun-ago)** la canibalización se mantiene cerca de 0% en todos "
            "los años — la demanda de aire acondicionado absorbe la generación solar de forma consistente. "
            "En los meses de **invierno/primavera seca (nov-mar)**, en cambio, el % de horas en precio "
            "≤ \\$0 " + ("tiende a subir año con año" if invierno_sube else "varía más entre años") +
            " conforme se instala más capacidad solar — esa es la ventana de riesgo real para un proyecto "
            "nuevo, no el promedio anual."
        )

    # 9.4 Geografía de precios
    tabla_ubic_existe_nac = con.execute("""
        SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'nodos_ubicacion'
    """).fetchone()[0]

    df_estados_nac = pd.DataFrame()
    if tabla_ubic_existe_nac:
        st.markdown("#### Geografía de precios: entidades más caras y más baratas")
        df_estados_nac = con.execute(f"""
            SELECT u.entidad_federativa AS entidad, COUNT(DISTINCT p.nodo) AS n_nodos,
                ROUND(AVG(p.pml), 1) AS pml_promedio, ROUND(AVG(p.energia), 1) AS energia,
                ROUND(AVG(p.perdidas), 1) AS perdidas, ROUND(AVG(p.congestion), 1) AS congestion
            FROM pml_hourly p JOIN nodos_ubicacion u ON p.nodo = u.nodo
            {filtro_anio_p}
            GROUP BY 1 ORDER BY pml_promedio DESC
        """).df()
        n_extremos = st.slider("Entidades a mostrar por extremo", 3, 15, 8, key="n_extremos_nac")
        df_top_bottom = pd.concat([df_estados_nac.head(n_extremos), df_estados_nac.tail(n_extremos)])
        fig_estados_nac = px.bar(
            df_top_bottom.sort_values("pml_promedio"), x="pml_promedio", y="entidad", orientation="h",
            color="pml_promedio", color_continuous_scale="Blues",
            labels={"pml_promedio": "PML promedio ($/MWh)", "entidad": ""},
        )
        st.plotly_chart(fig_estados_nac, use_container_width=True)
        st.caption(
            "La energía es casi idéntica en todo el país — lo que cambia es la congestión: positiva "
            "y alta en sistemas con interconexión débil (p. ej. península de Yucatán), negativa en "
            "zonas con sobreoferta renovable que la red local no puede evacuar (p. ej. Sonora, Chihuahua)."
        )
        with st.expander("Ver todas las entidades"):
            st.dataframe(df_estados_nac, use_container_width=True)
    else:
        st.info("Corre geolocalizar_nodos.py para habilitar el desglose por entidad.")

    # 9.5 Oportunidad de almacenamiento: interconectado vs. sistemas aislados
    df_spread_interconectado = pd.DataFrame()
    df_spread_aislado = pd.DataFrame()
    if tabla_ubic_existe_nac:
        st.markdown("#### Oportunidad de almacenamiento: sistema interconectado vs. sistemas aislados")
        st.caption(
            "El spread solar-punta más grande del país suele estar en la península de Yucatán, pero "
            "ahí es congestión estructural por interconexión débil, no la curva de pato clásica. "
            "Separar ambos grupos evita confundir un spread que podría comprimirse con una obra de "
            "transmisión, con uno que crece con más solar en el sistema."
        )
        estados_aislados = ["QUINTANA ROO", "YUCATAN", "CAMPECHE", "TABASCO", "CHIAPAS"]
        lista_aislados_sql = ",".join(f"'{e}'" for e in estados_aislados)

        def _top_spread_nac(filtro_estados):
            filtro_anio_extra = f" AND anio = {anio_filtro}" if anio_filtro else ""
            q = f"""
                WITH clasificado AS (
                    SELECT p.*, u.nombre, u.entidad_federativa, u.municipio,
                        CASE WHEN hora >= 6 AND hora < 18 THEN 'solar'
                             WHEN hora >= 18 AND hora < 22 THEN 'punta' ELSE 'otro' END AS periodo
                    FROM pml_hourly p LEFT JOIN nodos_ubicacion u ON p.nodo = u.nodo
                    WHERE {filtro_estados} {filtro_anio_extra}
                ),
                agg AS (
                    SELECT nodo, nombre, entidad_federativa, municipio, periodo,
                        AVG(pml) AS pml_prom, COUNT(*) AS n
                    FROM clasificado WHERE periodo IN ('solar', 'punta') GROUP BY 1, 2, 3, 4, 5
                ),
                piv AS (
                    SELECT nodo, ANY_VALUE(nombre) AS nombre, ANY_VALUE(entidad_federativa) AS ent,
                        ANY_VALUE(municipio) AS mun,
                        MAX(CASE WHEN periodo = 'solar' THEN pml_prom END) AS solar,
                        MAX(CASE WHEN periodo = 'punta' THEN pml_prom END) AS punta,
                        SUM(n) AS n_obs
                    FROM agg GROUP BY nodo
                )
                SELECT nodo, nombre, ent AS entidad, mun AS municipio,
                    ROUND(solar, 1) AS pml_solar, ROUND(punta, 1) AS pml_punta,
                    ROUND(punta - solar, 1) AS spread
                FROM piv WHERE n_obs > 500
                ORDER BY spread DESC LIMIT 10
            """
            return con.execute(q).df()

        df_spread_interconectado = _top_spread_nac(f"u.entidad_federativa NOT IN ({lista_aislados_sql})")
        df_spread_aislado = _top_spread_nac(f"u.entidad_federativa IN ({lista_aislados_sql})")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Sistema interconectado (excluye península)**")
            st.dataframe(df_spread_interconectado, use_container_width=True)
        with col2:
            st.markdown("**Sistemas aislados (península de Yucatán)**")
            st.dataframe(df_spread_aislado, use_container_width=True)

    # 9.6 Curva de duración normalizada: tres arquetipos de nodo
    st.markdown("#### Curva de duración normalizada: tres arquetipos de nodo")
    st.caption(
        "Eje Y = PML de cada hora dividido entre el promedio propio del nodo (1.0x = su propio "
        "promedio). Permite comparar la forma de la volatilidad de nodos con niveles de precio muy "
        "distintos en el mismo eje."
    )
    nodos_arquetipo = {}
    nodo_barato = con.execute(f"""
        SELECT nodo FROM pml_hourly {filtro_anio_solo}
        GROUP BY nodo HAVING COUNT(*) > 1000
        ORDER BY AVG(pml) ASC LIMIT 1
    """).fetchone()
    if nodo_barato:
        nodos_arquetipo["Más barato (sobreoferta)"] = nodo_barato[0]
    if not df_spread_interconectado.empty:
        nodos_arquetipo["Mejor spread interconectado (duck-curve)"] = df_spread_interconectado.iloc[0]["nodo"]
    if not df_spread_aislado.empty:
        nodos_arquetipo["Mejor spread aislado (congestión)"] = df_spread_aislado.iloc[0]["nodo"]

    if nodos_arquetipo:
        fig_dur_nac = go.Figure()
        for etiqueta, nodo in nodos_arquetipo.items():
            filtro_anio_dur = f"AND anio = {anio_filtro}" if anio_filtro else ""
            df_nodo_dur = con.execute(f"""
                SELECT pml, ROUND(PERCENT_RANK() OVER (ORDER BY pml DESC) * 100, 1) AS percentil
                FROM pml_hourly WHERE nodo = '{nodo}' {filtro_anio_dur}
                ORDER BY pml DESC
            """).df()
            media_nodo = df_nodo_dur["pml"].mean()
            if media_nodo:
                fig_dur_nac.add_scatter(x=df_nodo_dur["percentil"], y=df_nodo_dur["pml"] / media_nodo,
                                         mode="lines", name=f"{etiqueta} ({nodo})")
        fig_dur_nac.update_layout(xaxis_title="Percentil de horas (0% = hora más cara)",
                                   yaxis_title="PML / promedio del nodo")
        st.plotly_chart(fig_dur_nac, use_container_width=True)
    else:
        st.info("No hay suficientes datos para construir la comparación de arquetipos.")
