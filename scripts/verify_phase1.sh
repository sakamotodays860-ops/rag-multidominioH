# ---------------------------------------------------------------------------
# Verificacion de la FASE 1 (infraestructura base).
#
# Ejecuta unicamente comprobaciones NO destructivas:
#   - el backend responde en GET / y GET /health
#   - PostgreSQL acepta conexiones
#   - la extension pgvector esta disponible
#
# Uso:
#   ./scripts/verify_phase1.sh
# ---------------------------------------------------------------------------
set -u

BASE_URL="${BASE_URL:-http://localhost:8000}"
POSTGRES_USER="${POSTGRES_USER:-rag_user}"
POSTGRES_DB="${POSTGRES_DB:-rag_multidominio}"

echo "== 1. Estado del backend en GET / =="
curl -fsS "${BASE_URL}/" && echo

echo
echo "== 2. Healthcheck del backend en GET /health =="
curl -fsS "${BASE_URL}/health" && echo

echo
echo "== 3. PostgreSQL acepta conexiones =="
docker compose exec -T postgres pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

echo
echo "== 4. pgvector disponible en PostgreSQL =="
docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';"

echo
echo "== Verificacion de la FASE 1 completada =="