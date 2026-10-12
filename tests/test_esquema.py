"""Comprobaciones de que el esquema de la FASE 2 esta correctamente creado.

Verifican los requisitos 1 a 6 de la fase:
    - PostgreSQL y pgvector funcionan
    - `coleccion_id` existe como VARCHAR(50) NOT NULL
    - el indice B-tree sobre `coleccion_id` existe
    - `embedding` es `vector(768)` y coincide con la configuracion
    - `match_normativa_coleccion` existe con la firma exigida
"""

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.database.repository import VECTOR_DIMENSION

TABLA = "normativa_bancaria"
INDICE = "idx_normativa_bancaria_coleccion_id"
RPC = "match_normativa_coleccion"
FIRMA_ESPERADA = (
    "p_coleccion_id character varying, "
    "p_query_embedding vector, "
    "p_match_threshold double precision, "
    "p_match_count integer"
)


async def _fila(sesion, consulta: str) -> dict:
    resultado = await sesion.execute(text(consulta))
    return dict(resultado.mappings().one())


@pytest.mark.asyncio
async def test_postgresql_responde(sesion):
    """1. PostgreSQL funciona y es la version esperada."""
    fila = await _fila(sesion, "SELECT current_database() AS db, version() AS v")
    assert fila["db"] == settings.postgres_db
    assert "PostgreSQL 16" in fila["v"]


@pytest.mark.asyncio
async def test_pgvector_instalada(sesion):
    """2. pgvector esta instalada en la base de datos."""
    fila = await _fila(
        sesion,
        "SELECT extversion AS version FROM pg_extension WHERE extname = 'vector'",
    )
    assert fila["version"] is not None
    assert tuple(map(int, fila["version"].split("."))) >= (0, 7)


@pytest.mark.asyncio
async def test_coleccion_id_existe(sesion):
    """3. `coleccion_id` es VARCHAR(50) y no admite NULL."""
    fila = await _fila(
        sesion,
        f"""
        SELECT data_type, character_maximum_length, is_nullable
        FROM information_schema.columns
        WHERE table_name = '{TABLA}' AND column_name = 'coleccion_id'
        """,
    )
    assert fila["data_type"] == "character varying"
    assert fila["character_maximum_length"] == 50
    assert fila["is_nullable"] == "NO"


@pytest.mark.asyncio
async def test_coleccion_id_no_vacio(sesion):
    """El `coleccion_id` no puede ser una cadena en blanco."""
    resultado = await sesion.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'normativa_bancaria_coleccion_id_no_vacia'"
        )
    )
    definicion = resultado.scalar_one_or_none()
    assert definicion is not None, "falta la restriccion de no-vacio"
    assert "btrim" in definicion


@pytest.mark.asyncio
async def test_indice_btree_sobre_coleccion_id(sesion):
    """4. Existe un indice B-tree sobre `coleccion_id`."""
    fila = await _fila(
        sesion,
        f"SELECT indexdef FROM pg_indexes "
        f"WHERE tablename = '{TABLA}' AND indexname = '{INDICE}'",
    )
    definicion = fila["indexdef"]
    assert "USING btree" in definicion
    assert "coleccion_id" in definicion


@pytest.mark.asyncio
async def test_embedding_es_vector_768(sesion):
    """3. `embedding` es `vector(768)` y coincide con la configuracion."""
    fila = await _fila(
        sesion,
        f"""
        SELECT format_type(a.atttypid, a.atttypmod) AS tipo
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = '{TABLA}'
          AND a.attname = 'embedding'
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
    )
    assert fila["tipo"] == "vector(768)"
    assert VECTOR_DIMENSION == 768, "PGVECTOR_DIMENSION debe ser 768"
    assert settings.pgvector_dimension == 768


@pytest.mark.asyncio
async def test_rpc_existe_con_firma_esperada(sesion):
    """5. `match_normativa_coleccion` existe con la firma exigida."""
    resultado = await sesion.execute(
        text(
            "SELECT pg_get_function_identity_arguments(oid) AS firma "
            "FROM pg_proc WHERE proname = :nombre"
        ),
        {"nombre": RPC},
    )
    firmas = [fila[0] for fila in resultado.all()]
    assert FIRMA_ESPERADA in firmas, f"firmas encontradas: {firmas}"


@pytest.mark.asyncio
async def test_rpc_filtrada_por_coleccion(sesion):
    """El cuerpo de la funcion filtra por `coleccion_id` antes de ordenar."""
    resultado = await sesion.execute(
        text("SELECT prosrc FROM pg_proc WHERE proname = :nombre"),
        {"nombre": RPC},
    )
    cuerpo = resultado.scalar_one()
    assert "nb.coleccion_id = p_coleccion_id" in cuerpo
    assert cuerpo.index("nb.coleccion_id = p_coleccion_id") < cuerpo.index("ORDER BY")


@pytest.mark.asyncio
async def test_tabla_sin_registros_ajenos(sesion):
    """Toda fila de la tabla pertenece a una coleccion conocida."""
    resultado = await sesion.execute(
        text(
            "SELECT count(*) FROM normativa_bancaria "
            "WHERE coleccion_id IS NULL OR btrim(coleccion_id) = ''"
        )
    )
    assert int(resultado.scalar_one()) == 0