#!/usr/bin/env bash
#
# Publica (o actualiza) la base de datos pml.duckdb como GitHub Release.
# La app la descarga sola desde ahí — este script es lo único que necesitas
# correr cada vez que actualices los datos (después de ingest.py / geolocalizar_nodos.py).
#
# Requiere: GitHub CLI instalado y autenticado (`gh auth login`, una sola vez).
# Instalar gh: https://cli.github.com/
#
# Uso:
#   ./publicar_base_datos.sh
#
set -e

REPO="tu-usuario/pml-repsa"       # <-- ajusta a tu repo real
TAG="datos-actuales"
ARCHIVO="db/pml.duckdb"

if [ ! -f "$ARCHIVO" ]; then
    echo "No se encontró $ARCHIVO. Corre ingest.py primero."
    exit 1
fi

echo "Publicando $ARCHIVO en $REPO (release: $TAG)..."

# Si el release ya existe, lo borramos y recreamos (más simple y confiable
# que actualizar un asset existente uno por uno).
if gh release view "$TAG" --repo "$REPO" &>/dev/null; then
    echo "Release existente encontrado, reemplazando..."
    gh release delete "$TAG" --repo "$REPO" --yes --cleanup-tag
fi

gh release create "$TAG" "$ARCHIVO" \
    --repo "$REPO" \
    --title "Datos PML - actualización $(date +%Y-%m-%d)" \
    --notes "Base de datos actualizada automáticamente. $(du -h "$ARCHIVO" | cut -f1) de datos."

echo ""
echo "Listo. La próxima vez que alguien abra la app (o se reinicie el contenedor"
echo "de Streamlit Cloud), va a descargar esta versión nueva automáticamente."
echo ""
echo "Nota: si la app ya está corriendo con la base vieja en memoria, no se"
echo "entera sola del cambio hasta que el contenedor se reinicie. En Streamlit"
echo "Community Cloud puedes forzar esto desde el menú de la app > 'Reboot app'."
