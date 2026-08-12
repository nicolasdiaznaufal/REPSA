# PML Analysis Project

## Estructura
- `scripts/ingest.py` — carga archivos CSV de CENACE a la base DuckDB (idempotente)
- `scripts/analysis.py` — las 4 consultas de negocio, reutilizables como funciones
- `db/pml.duckdb` — base de datos (ya contiene julio 2026 completo, ambos archivos cargados)

## Uso
Requiere: `pip install duckdb pandas`

Cargar nuevos archivos (cuando descargues más meses de CENACE):
    cd scripts
    python ingest.py /ruta/a/nuevos_archivos/*.csv

Correr el demo de análisis:
    cd scripts
    python analysis.py

O importar las funciones en tu propio script/notebook:
    from analysis import connect, precio_promedio_por_hora, comparar_nodos, evolucion_anual, spread_solar_punta
    con = connect()
    df = comparar_nodos(con, ['01AAN-85', '01AAP-85'])

## Interfaz (Streamlit)
Requiere además: `pip install streamlit plotly`

Para uso personal:
    cd scripts
    streamlit run app.py

Para compartir con colegas en la oficina (correr en un servidor/VM interno accesible en la red):
    streamlit run app.py --server.address 0.0.0.0 --server.port 8501

Luego cada colega entra desde su navegador a: http://<ip-del-servidor>:8501

La app tiene 4 pestañas, una por cada pregunta de negocio:
1. Precio promedio por hora del día (uno o varios nodos)
2. Comparación estadística entre nodos
3. Evolución anual del PML
4. Spread solar (6h-18h) vs punta (18h-22h)

Todo se filtra desde el panel izquierdo (selección de nodos y año).

## Mapa geográfico (nueva pestaña "Mapa de México")

Los nodos se geolocalizan a nivel de **municipio** (cabecera municipal), cruzando:
- El catálogo oficial de nodos de CENACE (Estado + Municipio, claves INEGI)
- Centroides de cabeceras municipales de INEGI (`municipios_centroides.csv`, ya incluido)

CENACE no publica coordenadas exactas de subestación — municipio es la granularidad
más fina disponible públicamente. Cobertura actual: 99.7% de los nodos.

### Actualizar la geolocalización (cuando cambie el catálogo de CENACE)
Descarga la versión más reciente desde:
https://www.cenace.gob.mx/Paginas/SIM/NodosP.aspx

Luego:
    cd scripts
    python geolocalizar_nodos.py "Catálogo NodosP Sistema Eléctrico Nacional (v...).xlsx"

Esto regenera la tabla `nodos_ubicacion` dentro de `pml.duckdb`. Solo hace falta
correrlo de nuevo cuando CENACE actualice el catálogo (no cada vez que cargas
datos de PML nuevos).

## Análisis adicionales (agosto 2026)

Tres pestañas nuevas, todas usando los mismos datos que ya cargas (sin fuentes nuevas):

1. **Canibalización solar**: % de horas con PML ≤ 0 en horas solares centrales (10h-16h)
   por nodo/año, y precio de captura solar (ponderado por un perfil típico de generación
   FV) vs. el promedio simple del mismo periodo. Si el precio de captura es menor,
   es señal de canibalización.
2. **Curva de duración de precios**: todas las horas del periodo ordenadas de mayor a
   menor precio — muestra la volatilidad completa, no solo el promedio.
3. **Descomposición del PML**: Energía + Pérdidas + Congestión por nodo, para argumentar
   si un nodo es caro por congestión (temporal, ligado a la red) o por energía
   (estructural, ligado al mix de generación regional).

Nota sobre el precio de captura: usa un perfil sintético de generación solar (campana
6h-18h, pico al mediodía) como aproximación, no la curva de generación real de un
proyecto específico — es la práctica estándar cuando no se tienen datos de generación
por nodo.
