"""Consultas sobre `normativa_bancaria` y su funcion RPC.

Esta capa es el unico punto de llamada previsto para la busqueda vectorial:
todas las consultas pasan por `match_normativa_coleccion`, que filtra por
`coleccion_id` antes de ordenar y limitar.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.normativa import (
    ColeccionInfo,
    Embedding,
    NormativaMatch,
    embedding_a_literal_pgvector,
    validar_coleccion_id,
)

# Dimension del embedding, fijada en la configuracion. Tiene que coincidir con
# VECTOR(768) del esquema y con la firma de la funcion; tests/test_esquema.py
# lo comprueba para que una discrepancia salga alla y no en produccion.
VECTOR_DIMENSION = settings.pgvector_dimension

# La dimension se interpola como literal: PostgreSQL no admite un parametro
# enlazado como modificador de tipo (`vector($1)` produce "invalid type
# modifier"). El valor procede de la configuracion, no del cliente.
_SQL_BUSCAR = f"""
SELECT id, coleccion_id, contenido, origen_url, distancia, similitud
FROM match_normativa_coleccion(
    p_coleccion_id    => :coleccion_id,
    p_query_embedding => CAST(CAST(:query_embedding AS TEXT) AS vector({VECTOR_DIMENSION})),
    p_match_threshold => :match_threshold,
    p_match_count     => :match_count
)
ORDER BY distancia
"""

_SQL_CONTEO = """
SELECT count(*)
FROM normativa_bancaria
WHERE coleccion_id = :coleccion_id
"""

_SQL_LISTAR = """
SELECT coleccion_id, count(*)
FROM normativa_bancaria
GROUP BY coleccion_id
ORDER BY coleccion_id
"""

_SQL_INSERTAR = f"""
INSERT INTO normativa_bancaria (coleccion_id, contenido, origen_url, embedding)
VALUES (
    :coleccion_id,
    :contenido,
    :origen_url,
    CAST(:embedding AS vector({VECTOR_DIMENSION}))
)
"""


async def buscar_por_coleccion(
    session: AsyncSession,
    *,
    coleccion_id: str,
    query_embedding: Embedding,
    match_threshold: float = 0.5,
    match_count: int = 10,
) -> list[NormativaMatch]:
    """Busca los fragmentos mas cercanos de UNA sola coleccion.

    Lanza `ValueError` si `coleccion_id` no es valido: nunca llega un identificador
    malformado a la base de datos.
    """
    coleccion_id = validar_coleccion_id(coleccion_id)

    if match_count < 1:
        raise ValueError("match_count debe ser mayor o igual a 1")
    if len(query_embedding) != VECTOR_DIMENSION:
        raise ValueError(
            f"query_embedding debe tener {VECTOR_DIMENSION} dimensiones, "
            f"no {len(query_embedding)}"
        )

    resultado = await session.execute(
        text(_SQL_BUSCAR),
        {
            "coleccion_id": coleccion_id,
            "query_embedding": embedding_a_literal_pgvector(query_embedding),
            "match_threshold": float(match_threshold),
            "match_count": int(match_count),
        },
    )
    return [
        NormativaMatch(
            id=fila.id,
            coleccion_id=fila.coleccion_id,
            contenido=fila.contenido,
            origen_url=fila.origen_url,
            distancia=fila.distancia,
            similitud=fila.similitud,
        )
        for fila in resultado.mappings()
    ]


async def contar_coleccion(session: AsyncSession, coleccion_id: str) -> int:
    """Devuelve el numero de fragmentos de una coleccion."""
    coleccion_id = validar_coleccion_id(coleccion_id)
    resultado = await session.execute(
        text(_SQL_CONTEO), {"coleccion_id": coleccion_id}
    )
    return int(resultado.scalar_one())


async def listar_colecciones(session: AsyncSession) -> list[tuple[str, int]]:
    """Devuelve ``(coleccion_id, fragmentos)`` de todas las colecciones."""
    resultado = await session.execute(text(_SQL_LISTAR))
    return [(fila[0], int(fila[1])) for fila in resultado.all()]


async def info_coleccion(session: AsyncSession, coleccion_id: str) -> ColeccionInfo:
    """Devuelve el diagnostico de una coleccion validando su identificador."""
    coleccion_id = validar_coleccion_id(coleccion_id)
    fragmentos = await contar_coleccion(session, coleccion_id)
    return ColeccionInfo(
        coleccion_id=coleccion_id,
        existe=fragmentos > 0,
        fragmentos=fragmentos,
    )


async def insertar_fragmentos(
    session: AsyncSession,
    *,
    coleccion_id: str,
    registros: list[dict[str, str | Embedding]],
) -> int:
    """Inserta fragmentos + embeddings, estampando SIEMPRE ``coleccion_id``.

    Aislamiento por construccion (requisito 6): los registros de entrada NO
    llevan ``coleccion_id``. La unica fuente del valor es el parametro
    validado con ``validar_coleccion_id``; es materialmente imposible que una
    ingesta de una coleccion escriba filas en otra.

    ``registros`` es una lista de ``{"contenido", "origen_url", "embedding"}``.
    No hace commit: el servicio decide cuando persistir (permite a los tests
    insertar dentro de una transaccion y hacer rollback sin tocar la BD).

    Devuelve el numero de filas insertadas.
    """
    if isinstance(coleccion_id, str):
        coleccion_id = validar_coleccion_id(coleccion_id)
    else:
        raise ValueError("coleccion_id debe ser una cadena")

    if not registros:
        return 0

    filas = [
        {
            "coleccion_id": coleccion_id,
            "contenido": str(fila["contenido"]),
            "origen_url": (
                str(fila["origen_url"]) if fila.get("origen_url") else None
            ),
            "embedding": embedding_a_literal_pgvector(fila["embedding"]),
        }
        for fila in registros
    ]

    await session.execute(text(_SQL_INSERTAR), filas)
    return len(filas)