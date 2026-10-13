"""Consultas sobre `normativa_bancaria` y su funcion RPC.

Esta capa es el unico punto de llamada previsto para la busqueda vectorial:
todas las consultas pasan por `match_normativa_coleccion`, que filtra por
`coleccion_id` antes de ordenar y limitar.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.normativa import (
    ColeccionInfo,
    Embedding,
    NormativaMatch,
    NormativaMatchLexico,
    embedding_a_literal_pgvector,
    validar_coleccion_id,
)
from app.utils.dedup import huella_contenido
from app.utils.referencias import extraer_frase_exacta

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

# Busqueda LEXICA nativa de PostgreSQL (sin extensiones: usa el diccionario
# `spanish` integrado y `strpos`). Complementa a la busqueda vectorial; NO la
# sustituye. Filtra SIEMPRE por `coleccion_id` (aislamiento por construccion) y
# el orden es reproducible: coincidencia exacta, lexemas coincidentes,
# relevancia textual y, como desempate final, el id.
#
# La coincidencia por lexemas usa interseccion de conjuntos (OR de contenido):
# `terminos` cuenta cuantos lexemas (palabras normalizadas) de la consulta
# aparecen en el fragmento. Se exige al menos 1 para no perder recall cuando la
# pregunta tiene muchas palabras; el ranking prioriza los que comparten mas.
# `plainto_tsquery` (AND) aporta la relevancia `ts_rank` cuando TODOS coinciden.
#
# Todos los valores del cliente viajan como parametros enlazados (`:consulta`,
# `:frase`); nunca se concatenan en el SQL.
_SQL_BUSCAR_LEXICO = """
WITH consulta_lexemas AS (
    SELECT tsvector_to_array(to_tsvector('spanish', :consulta)) AS lexemas
)
SELECT
    c.id,
    c.coleccion_id,
    c.contenido,
    c.origen_url,
    c.rank_fts,
    c.terminos,
    c.coincidencia_exacta
FROM (
    SELECT
        nb.id,
        nb.coleccion_id,
        nb.contenido,
        nb.origen_url,
        ts_rank(
            to_tsvector('spanish', nb.contenido),
            plainto_tsquery('spanish', :consulta)
        ) AS rank_fts,
        (
            SELECT count(*)
            FROM (
                SELECT unnest(
                    tsvector_to_array(to_tsvector('spanish', nb.contenido))
                )
                INTERSECT
                SELECT unnest(cl.lexemas)
            ) AS coincidentes
        ) AS terminos,
        CASE
            WHEN :frase <> ''
             AND strpos(lower(nb.contenido), lower(:frase)) > 0
            THEN TRUE ELSE FALSE
        END AS coincidencia_exacta
    FROM normativa_bancaria nb, consulta_lexemas cl
    WHERE nb.coleccion_id = :coleccion_id
      AND (
            (
                SELECT count(*)
                FROM (
                    SELECT unnest(
                        tsvector_to_array(to_tsvector('spanish', nb.contenido))
                    )
                    INTERSECT
                    SELECT unnest(cl.lexemas)
                ) AS coincidentes
            ) >= 1
         OR (:frase <> ''
             AND strpos(lower(nb.contenido), lower(:frase)) > 0)
      )
) AS c
ORDER BY
    c.coincidencia_exacta DESC,
    c.terminos DESC,
    c.rank_fts DESC,
    c.id ASC
LIMIT :match_count
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

# Contenido ya almacenado en una coleccion. Se usa para no volver a insertar
# fragmentos identicos en una segunda ingesta (idempotencia frente a lo YA
# almacenado, no solo frente a duplicados dentro de la misma peticion).
_SQL_CONTENIDOS_COLECCION = """
SELECT contenido
FROM normativa_bancaria
WHERE coleccion_id = :coleccion_id
"""

# Cerrojo de transaccion por coleccion. Serializa las ingestas concurrentes de
# la MISMA coleccion: la segunda espera a que la primera termine su transaccion
# (commit o rollback) antes de comprobar e insertar, evitando la condicion de
# carrera "comprobar y luego insertar". `hashtextextended` convierte el texto de
# la coleccion en la clave bigint que exige `pg_advisory_xact_lock`; el cerrojo
# se libera automaticamente al terminar la transaccion. Colecciones distintas
# usan claves distintas y NO se bloquean entre si (se conserva el aislamiento y
# la concurrencia entre dominios).
_SQL_BLOQUEAR_COLECCION = "SELECT pg_advisory_xact_lock(hashtextextended(:coleccion_id, 0))"


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


async def buscar_lexico_por_coleccion(
    session: AsyncSession,
    *,
    coleccion_id: str,
    consulta: str,
    match_count: int = 10,
) -> list[NormativaMatchLexico]:
    """Busca por texto (PostgreSQL nativo) en UNA sola coleccion.

    Complementa a :func:`buscar_por_coleccion` (vectorial). No requiere el
    embedding de la consulta, por lo que funciona aunque el proveedor de
    embeddings no este disponible.

    Lanza ``ValueError`` si ``coleccion_id`` no es valido o si la consulta esta
    vacia: nunca llega un identificador malformado a la base de datos.
    """
    coleccion_id = validar_coleccion_id(coleccion_id)
    if match_count < 1:
        raise ValueError("match_count debe ser mayor o igual a 1")
    consulta = (consulta or "").strip()
    if not consulta:
        raise ValueError("consulta no puede estar vacia")

    resultado = await session.execute(
        text(_SQL_BUSCAR_LEXICO),
        {
            "coleccion_id": coleccion_id,
            "consulta": consulta,
            "frase": extraer_frase_exacta(consulta),
            "match_count": int(match_count),
        },
    )
    return [
        NormativaMatchLexico(
            id=fila.id,
            coleccion_id=fila.coleccion_id,
            contenido=fila.contenido,
            origen_url=fila.origen_url,
            rank_fts=float(fila.rank_fts),
            terminos=int(fila.terminos),
            coincidencia_exacta=bool(fila.coincidencia_exacta),
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


@dataclass(frozen=True)
class ResultadoInsercion:
    """Recuento de una insercion idempotente.

    ``insertados`` son las filas nuevas; ``omitidos`` los fragmentos que ya
    existian en la coleccion (mismo contenido normalizado) y que por tanto NO
    se volvieron a insertar.
    """

    insertados: int
    omitidos: int


def _preparar_filas(
    coleccion_id: str, registros: list[dict[str, str | Embedding]]
) -> list[dict[str, str | None]]:
    """Normaliza los registros de entrada para el INSERT.

    Estampa SIEMPRE ``coleccion_id`` (unica fuente, ya validada) y convierte el
    embedding a su literal pgvector. No depende de que los registros traigan
    ``coleccion_id``.
    """
    return [
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

    filas = _preparar_filas(coleccion_id, registros)
    await session.execute(text(_SQL_INSERTAR), filas)
    return len(filas)


async def huellas_existentes(
    session: AsyncSession, *, coleccion_id: str
) -> set[str]:
    """Huellas de contenido ya presentes en ``coleccion_id``.

    Se calcula la huella normalizada (misma logica que el deduplicado en
    memoria) de cada ``contenido`` almacenado. Sirve para que una nueva ingesta
    no vuelva a insertar fragmentos identicos.
    """
    coleccion_id = validar_coleccion_id(coleccion_id)
    resultado = await session.execute(
        text(_SQL_CONTENIDOS_COLECCION), {"coleccion_id": coleccion_id}
    )
    return {huella_contenido(contenido) for contenido in resultado.scalars().all()}


async def insertar_fragmentos_sin_duplicados(
    session: AsyncSession,
    *,
    coleccion_id: str,
    registros: list[dict[str, str | Embedding]],
) -> ResultadoInsercion:
    """Inserta solo los fragmentos cuyo contenido NO exista ya en la coleccion.

    Estrategia idempotente y segura frente a concurrencia:

    1. Toma un cerrojo de transaccion por coleccion
       (``pg_advisory_xact_lock``): dos ingestas concurrentes de la MISMA
       coleccion se serializan; colecciones distintas no se bloquean.
    2. Lee las huellas de contenido ya almacenadas en la coleccion.
    3. Descarta los registros cuya huella ya existe (o se repitio en la propia
       entrada) y NO los inserta.
    4. Inserta los restantes en una unica sentencia, todo dentro de la misma
       transaccion del cerrojo.

    El chequeo se hace bajo el cerrojo, por lo que no hay ventana entre
    "comprobar" e "insertar" dentro de esta logica. No hace commit: el llamador
    decide cuando persistir (y el cerrojo dura hasta ese commit/rollback).

    Aislamiento: la huella se compara SOLO dentro de ``coleccion_id``; el mismo
    contenido en dos colecciones distintas es legitimo y se inserta en ambas.
    No se deduplica por ``origen_url`` (regla 9): un contenido diferente servido
    desde la misma URL es un documento distinto y se conserva.
    """
    if not isinstance(coleccion_id, str):
        raise ValueError("coleccion_id debe ser una cadena")
    coleccion_id = validar_coleccion_id(coleccion_id)

    if not registros:
        return ResultadoInsercion(insertados=0, omitidos=0)

    # (1) Serializa las ingestas concurrentes de la MISMA coleccion.
    await session.execute(
        text(_SQL_BLOQUEAR_COLECCION), {"coleccion_id": coleccion_id}
    )

    filas = _preparar_filas(coleccion_id, registros)

    # (2) Huellas ya almacenadas.
    ya_presentes = await huellas_existentes(session, coleccion_id=coleccion_id)

    nuevas: list[dict[str, str | None]] = []
    omitidos = 0
    for fila in filas:
        huella = huella_contenido(str(fila["contenido"]))
        if huella in ya_presentes:
            omitidos += 1
            continue
        ya_presentes.add(huella)
        nuevas.append(fila)

    if nuevas:
        await session.execute(text(_SQL_INSERTAR), nuevas)

    return ResultadoInsercion(insertados=len(nuevas), omitidos=omitidos)