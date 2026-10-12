#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Aplicacion de migraciones SQL.
#
# POR QUE ESTE SCRIPT Y NO `docker/postgres/init/`
#   Los ficheros de `docker/postgres/init/` solo se ejecutan cuando Docker crea
#   un volumen NUEVO. El volumen `rag_multidominio_pgdata` ya existe, asi que
#   anadir alli un script NO aplicaria nada (y hay que evitar `docker compose
#   down -v`, que lo borraria). Este runner aplica las migraciones contra el
#   contenedor en marcha, sin tocar el volumen.
#
# Es idempotente: cada version se registra en la tabla `schema_migrations` y no
# se vuelve a aplicar. Volver a ejecutarlo no produce cambios.
#
# Uso:
#   ./scripts/migrate.sh            # aplica las migraciones pendientes
#   ./scripts/migrate.sh --status   # muestra el estado sin aplicar nada
#
# Variables de entorno:
#   DOCKER_CMD      binario de docker (por defecto: docker)
#   POSTGRES_USER   usuario de la base de datos
#   POSTGRES_DB     nombre de la base de datos
#   MIGRATIONS_DIR  directorio con los .sql (por defecto: <repo>/migrations)
# ---------------------------------------------------------------------------
set -u

DOCKER_CMD="${DOCKER_CMD:-docker}"
POSTGRES_USER="${POSTGRES_USER:-rag_user}"
POSTGRES_DB="${POSTGRES_DB:-rag_multidominio}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${REPO_ROOT}/migrations}"

psql_cmd() {
    # ON_ERROR_STOP hace que un error de SQL aborte el script en lugar de
    # continuar y dejar el esquema a medias.
    $DOCKER_CMD compose exec -T postgres \
        psql -v ON_ERROR_STOP=1 -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" "$@"
}

if [ ! -d "${MIGRATIONS_DIR}" ]; then
    echo "ERROR: no existe el directorio de migraciones: ${MIGRATIONS_DIR}" >&2
    exit 1
fi

if ! $DOCKER_CMD compose exec -T postgres pg_isready -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > /dev/null 2>&1; then
    echo "ERROR: PostgreSQL no responde. Levanta los servicios con:" >&2
    echo "       ${DOCKER_CMD} compose up -d" >&2
    exit 1
fi

# Tabla de control de versiones.
psql_cmd -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);" || exit 1

MOSTRAR_ESTADO=0
if [ "${1:-}" = "--status" ]; then
    MOSTRAR_ESTADO=1
fi

echo "== Migraciones registradas =="
psql_cmd -c "SELECT version, applied_at FROM schema_migrations ORDER BY version;"

if [ "${MOSTRAR_ESTADO}" -eq 1 ]; then
    echo
    echo "== Ficheros disponibles en ${MIGRATIONS_DIR} =="
    for file in "${MIGRATIONS_DIR}"/*.sql; do
        [ -e "${file}" ] || continue
        version="$(basename "${file}" .sql)"
        aplicada="$(psql_cmd -tAc "SELECT 1 FROM schema_migrations WHERE version = '${version}';")"
        if [ "${aplicada}" = "1" ]; then
            echo "  [aplicada]  ${version}"
        else
            echo "  [pendiente] ${version}"
        fi
    done
    exit 0
fi

echo
echo "== Aplicando migraciones pendientes =="
for file in "${MIGRATIONS_DIR}"/*.sql; do
    [ -e "${file}" ] || continue
    version="$(basename "${file}" .sql)"

    aplicada="$(psql_cmd -tAc "SELECT 1 FROM schema_migrations WHERE version = '${version}';")"
    if [ "${aplicada}" = "1" ]; then
        echo "  [omitida]   ${version} (ya aplicada)"
        continue
    fi

    echo "  [aplicando] ${version}"
    # Migracion + registro en la MISMA transaccion: si el SQL falla, la version
    # no queda registrada y podra reintentarse.
    {
        echo "BEGIN;"
        cat "${file}"
        # Salto de linea explicito: si el .sql no termina en '\n', el INSERT
        # se pegaria a la ultima linea y PostgreSQL lo tomaria como parte de
        # ella (por ejemplo, dentro de un comentario) sin llegar a ejecutarse.
        printf '\n'
        echo "INSERT INTO schema_migrations (version) VALUES ('${version}');"
        echo "COMMIT;"
    } | psql_cmd -f - || {
        echo "ERROR: fallo la migracion ${version}" >&2
        exit 1
    }
done

echo
echo "== Migraciones aplicadas =="
psql_cmd -c "SELECT version, applied_at FROM schema_migrations ORDER BY version;"