#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Verificacion de la FASE 2 (aislamiento vectorial multi-dominio).
#
# Comprueba, en este orden:
#   1. PostgreSQL funciona
#   2. pgvector funciona
#   3. existe `coleccion_id` (VARCHAR(50) NOT NULL)
#   4. existe el indice B-tree sobre `coleccion_id`
#   5. existe la funcion `match_normativa_coleccion`
#   6. las consultas respetan el aislamiento entre colecciones
#   7. los registros de demostracion siguen intactos (4 y 4)
#   8. las pruebas automatizadas de pytest pasan
#
# TODAS las comprobaciones son de solo lectura: no insertan, no borran y no
# modifican la base de datos.
#
# Uso:
#   ./scripts/verify_phase2.sh
#
# Variables de entorno:
#   DOCKER_CMD      binario de docker (por defecto: docker)
#   POSTGRES_USER   usuario de la base de datos
#   POSTGRES_DB     nombre de la base de datos
#   BASE_URL        URL del backend
#   SALTAR_PYTEST   1 = no ejecutar pytest
# ---------------------------------------------------------------------------
set -u

DOCKER_CMD="${DOCKER_CMD:-docker}"
POSTGRES_USER="${POSTGRES_USER:-rag_user}"
POSTGRES_DB="${POSTGRES_DB:-rag_multidominio}"
BASE_URL="${BASE_URL:-http://localhost:8000}"

FALLOS=0

psql_q() {
    $DOCKER_CMD compose exec -T postgres \
        psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "$1" 2>&1 | tr -d '[:space:]'
}

comprobar() {
    # $1 = etiqueta, $2 = valor esperado, $3 = valor obtenido
    if [ "$2" = "$3" ]; then
        echo "  [OK]   $1"
    else
        echo "  [FAIL] $1"
        echo "         esperado : $2"
        echo "         obtenido : $3"
        FALLOS=$((FALLOS + 1))
    fi
}

if ! $DOCKER_CMD compose exec -T postgres pg_isready -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > /dev/null 2>&1; then
    echo "ERROR: PostgreSQL no responde. Levanta los servicios con:" >&2
    echo "       ${DOCKER_CMD} compose up -d" >&2
    exit 1
fi

echo "== 1. PostgreSQL funciona =="
comprobar "acepta conexiones" "1" "$(psql_q "SELECT 1")"
comprobar "version PostgreSQL 16" "PostgreSQL16" \
    "$(psql_q "SELECT replace(substring(version() from 'PostgreSQL [0-9]+'), ' ', '')")"

echo
echo "== 2. pgvector funciona =="
comprobar "extension 'vector' instalada" "0.8.7" "$(psql_q "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
# `<=>` es la distancia coseno, el operador que usa la RPC.
comprobar "distancia coseno de un vector consigo mismo es 0" "0" \
    "$(psql_q "SELECT (('[1,0,0]'::vector <=> '[1,0,0]'::vector))::text")"
comprobar "vectores ortogonales quedan separados" "distinta" \
    "$(psql_q "SELECT CASE WHEN ('[1,0,0]'::vector <=> '[0,1,0]'::vector) > 0
                           THEN 'distinta' ELSE 'igual' END")"

echo
echo "== 3. Existe coleccion_id =="
comprobar "VARCHAR(50) NOT NULL" "charactervarying50NO" \
    "$(psql_q "SELECT data_type || character_maximum_length || is_nullable
               FROM information_schema.columns
               WHERE table_name = 'normativa_bancaria' AND column_name = 'coleccion_id'")"

echo
echo "== 4. Existe el indice B-tree sobre coleccion_id =="
comprobar "indice idx_normativa_bancaria_coleccion_id (btree)" "1" \
    "$(psql_q "SELECT count(*) FROM pg_indexes
               WHERE tablename = 'normativa_bancaria'
                 AND indexname = 'idx_normativa_bancaria_coleccion_id'
                 AND indexdef LIKE '%USING btree%'
                 AND indexdef LIKE '%coleccion_id%'")"

echo
echo "== 5. Existe match_normativa_coleccion =="
comprobar "funcion declarada" "1" \
    "$(psql_q "SELECT count(*) FROM pg_proc WHERE proname = 'match_normativa_coleccion'")"
comprobar "firma con coleccion_id" "1" \
    "$(psql_q "SELECT count(*) FROM pg_proc
               WHERE proname = 'match_normativa_coleccion'
                 AND pg_get_function_identity_arguments(oid) =
                     'p_coleccion_id character varying, p_query_embedding vector, p_match_threshold double precision, p_match_count integer'")"

echo
echo "== 6. Las consultas respetan el aislamiento =="
# Umbral -1.0 y match_count 100: aceptarian las 8 filas de la base de datos,
# asi que cualquier fuga del filtro por coleccion_id saldria a la luz.
ISOLACION_SQL="
SELECT
  (SELECT count(*) FROM match_normativa_coleccion(
      'financiero_bolivia_2026',
      (SELECT array_agg(CASE WHEN s = 1 THEN 1.0 WHEN s = 2 THEN 0.0 ELSE 0.01 END
                        ORDER BY s)::vector
       FROM generate_series(1, 768) AS s),
      -1.0, 100)
   WHERE coleccion_id <> 'financiero_bolivia_2026')
  ||
  '|' ||
  (SELECT count(*) FROM match_normativa_coleccion(
      'tributario_bolivia_2026',
      (SELECT array_agg(CASE WHEN s = 1 THEN 0.0 WHEN s = 2 THEN 1.0 ELSE 0.01 END
                        ORDER BY s)::vector
       FROM generate_series(1, 768) AS s),
      -1.0, 100)
   WHERE coleccion_id <> 'tributario_bolivia_2026')
  ||
  '|' ||
  (SELECT count(*) FROM match_normativa_coleccion(
      'coleccion_inexistente',
      (SELECT array_agg(CASE WHEN s = 1 THEN 1.0 WHEN s = 2 THEN 0.0 ELSE 0.01 END
                        ORDER BY s)::vector
       FROM generate_series(1, 768) AS s),
      -1.0, 100))
  ||
  '|' ||
  (SELECT count(*) FROM match_normativa_coleccion(
      'financiero_bolivia_2026',
      (SELECT array_agg(CASE WHEN s = 1 THEN 1.0 WHEN s = 2 THEN 0.0 ELSE 0.01 END
                        ORDER BY s)::vector
       FROM generate_series(1, 768) AS s),
      -1.0, 100))
  ||
  '|' ||
  (SELECT count(*) FROM match_normativa_coleccion(
      'tributario_bolivia_2026',
      (SELECT array_agg(CASE WHEN s = 1 THEN 0.0 WHEN s = 2 THEN 1.0 ELSE 0.01 END
                        ORDER BY s)::vector
       FROM generate_series(1, 768) AS s),
      -1.0, 100));
"
ISOLACION="$($DOCKER_CMD compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "${ISOLACION_SQL}" 2>&1 | tr -d '[:space:]')"
comprobar "fugas=0|fugas=0|desconocida=0|financiero=4|tributario=4" "0|0|0|4|4" "${ISOLACION}"

echo
echo "== 7. Registros de demostracion intactos =="
conteos="$(${DOCKER_CMD} compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
    "SELECT coleccion_id || '=' || count(*) FROM normativa_bancaria GROUP BY coleccion_id ORDER BY coleccion_id" 2>&1 \
    | tr '\n' ' ' | tr -s ' ' | sed 's/^ *//;s/ *$//')"
comprobar "4 y 4, sin duplicados" "financiero_bolivia_2026=4 tributario_bolivia_2026=4" "${conteos}"
comprobar "total de filas" "8" "$(psql_q "SELECT count(*) FROM normativa_bancaria")"

echo
echo "== 8. Pruebas automatizadas (pytest) =="
if [ "${SALTAR_PYTEST:-0}" = "1" ]; then
    echo "  [SKIP] SALTAR_PYTEST=1"
else
    if ! $DOCKER_CMD compose exec -T backend python -c "import pytest" > /dev/null 2>&1; then
        echo "  Instalando dependencias de pruebas..."
        $DOCKER_CMD compose exec -T backend python -m pip install -q -r requirements-dev.txt
    fi

    if $DOCKER_CMD compose exec -T backend python -m pytest -q; then
        echo "  [OK]   pytest: todas las pruebas pasan"
    else
        echo "  [FAIL] pytest: hay pruebas fallidas"
        FALLOS=$((FALLOS + 1))
    fi
fi

echo
echo "== Estado del backend =="
if curl -fsS "${BASE_URL}/health" > /dev/null 2>&1; then
    echo "  [OK]   ${BASE_URL}/health responde"
else
    echo "  [FAIL] ${BASE_URL}/health no responde"
    FALLOS=$((FALLOS + 1))
fi

echo
if [ "${FALLOS}" -eq 0 ]; then
    echo "== Verificacion de la FASE 2 completada: TODAS las comprobaciones OK =="
    exit 0
fi

echo "== Verificacion de la FASE 2 con ${FALLOS} fallo(s) =="
exit 1