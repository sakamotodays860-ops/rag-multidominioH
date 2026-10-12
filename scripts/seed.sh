#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Carga de los datos de prueba de aislamiento entre colecciones.
#
# Los scripts son idempotentes (no insertan si la coleccion ya tiene filas),
# asi que este script se puede ejecutar varias veces sin duplicar nada.
#
# Uso:
#   ./scripts/seed.sh
#
# Variables de entorno:
#   DOCKER_CMD      binario de docker (por defecto: docker)
#   POSTGRES_USER   usuario de la base de datos
#   POSTGRES_DB     nombre de la base de datos
#   SEEDS_DIR       directorio con los .sql (por defecto: <repo>/seeds)
# ---------------------------------------------------------------------------
set -u

DOCKER_CMD="${DOCKER_CMD:-docker}"
POSTGRES_USER="${POSTGRES_USER:-rag_user}"
POSTGRES_DB="${POSTGRES_DB:-rag_multidominio}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEEDS_DIR="${SEEDS_DIR:-${REPO_ROOT}/seeds}"

if [ ! -d "${SEEDS_DIR}" ]; then
    echo "ERROR: no existe el directorio de seeds: ${SEEDS_DIR}" >&2
    exit 1
fi

if ! $DOCKER_CMD compose exec -T postgres pg_isready -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > /dev/null 2>&1; then
    echo "ERROR: PostgreSQL no responde. Levanta los servicios con:" >&2
    echo "       ${DOCKER_CMD} compose up -d" >&2
    exit 1
fi

echo "== Cargando seeds desde ${SEEDS_DIR} =="
for file in "${SEEDS_DIR}"/*.sql; do
    [ -e "${file}" ] || continue
    echo "  [aplicando] $(basename "${file}")"
    $DOCKER_CMD compose exec -T postgres \
        psql -v ON_ERROR_STOP=1 -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
        -f - < "${file}" || {
            echo "ERROR: fallo el seed $(basename "${file}")" >&2
            exit 1
        }
done

echo
echo "== Resumen de colecciones =="
$DOCKER_CMD compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    -c "SELECT coleccion_id, count(*) AS fragmentos FROM normativa_bancaria GROUP BY coleccion_id ORDER BY coleccion_id;"