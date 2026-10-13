"""Pruebas automatizadas de aisamiento vectorial entre colecciones (FASE 2).

Demuestran los requisitos 7, 8 y 9 de la fase:

  * una consulta orientada al dominio financiero devuelve SOLO
    `financiero_bolivia_2026`;
  * una consulta orientada al dominio tributario devuelve SOLO
    `tributario_bolivia_2026`;
  * no existe contaminacion cruzada, ni siquiera con un umbral tan
    permisivo que aceptaria todas las filas de la base de datos;
  * ninguna consulta modifica los datos.

TODAS las pruebas son de solo lectura.
"""

import pytest
from sqlalchemy import text

from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    FRAGMENTOS_ESPERADOS,
    PREFIJO_SEMILLA,
    vector_financiero,
    vector_tributario,
)

from app.database.repository import buscar_por_coleccion

# Umbral tan bajo que aceptaria cualquier vector: si el filtro por
# `coleccion_id` fallara, se devolverian filas de otras colecciones.
UMBRAL_PERMISIVO = -1.0
MATCH_COUNT_GRANDE = 100


async def _contar_todo(sesion) -> dict[str, int]:
    resultado = await sesion.execute(
        text(
            "SELECT coleccion_id, count(*) FROM normativa_bancaria "
            "GROUP BY coleccion_id ORDER BY coleccion_id"
        )
    )
    return {fila[0]: int(fila[1]) for fila in resultado.all()}


async def _contar_semilla(sesion) -> dict[str, int]:
    """Cuenta solo las filas sinteticas de demostracion (``seed://``)."""
    resultado = await sesion.execute(
        text(
            "SELECT coleccion_id, count(*) FROM normativa_bancaria "
            "WHERE origen_url LIKE :p GROUP BY coleccion_id ORDER BY coleccion_id"
        ),
        {"p": f"{PREFIJO_SEMILLA}%"},
    )
    return {fila[0]: int(fila[1]) for fila in resultado.all()}


@pytest.mark.asyncio
async def test_los_datos_de_demostracion_existen(sesion):
    """Los registros SEMILLA existen con el tamano esperado y NO se crean mas.

    Desde la FASE 6 pueden existir documentos reales en las mismas
    colecciones, por lo que se exige el tamano EXACTO de las filas semilla
    (``seed://``) y que el total de cada coleccion sea al menos ese numero.
    """
    conteos = await _contar_todo(sesion)
    semilla = await _contar_semilla(sesion)
    assert semilla.get(COLECCION_FINANCIERO) == FRAGMENTOS_ESPERADOS, (
        f"{COLECCION_FINANCIERO} deberia tener {FRAGMENTOS_ESPERADOS} filas "
        f"semilla; conteos: {semilla}"
    )
    assert semilla.get(COLECCION_TRIBUTARIO) == FRAGMENTOS_ESPERADOS, (
        f"{COLECCION_TRIBUTARIO} deberia tener {FRAGMENTOS_ESPERADOS} filas "
        f"semilla; conteos: {semilla}"
    )
    assert conteos.get(COLECCION_FINANCIERO, 0) >= FRAGMENTOS_ESPERADOS
    assert conteos.get(COLECCION_TRIBUTARIO, 0) >= FRAGMENTOS_ESPERADOS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coleccion, vector",
    [
        (COLECCION_FINANCIERO, vector_financiero()),
        (COLECCION_TRIBUTARIO, vector_tributario()),
    ],
)
async def test_consulta_devuelve_solo_su_coleccion(sesion, coleccion, vector):
    """Requisito 7: la funcion solo devuelve filas de la coleccion pedida."""
    resultados = await buscar_por_coleccion(
        sesion,
        coleccion_id=coleccion,
        query_embedding=vector,
        match_threshold=0.5,
        match_count=10,
    )

    assert resultados, f"la consulta a {coleccion} no devolvio nada"
    assert len(resultados) >= FRAGMENTOS_ESPERADOS

    for fila in resultados:
        assert fila.coleccion_id == coleccion, (
            f"CONTAMINACION: se pidio {coleccion} y llego "
            f"{fila.coleccion_id} (id={fila.id})"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coleccion, vector",
    [
        (COLECCION_FINANCIERO, vector_financiero()),
        (COLECCION_TRIBUTARIO, vector_tributario()),
    ],
)
async def test_sin_contaminacion_con_umbral_permisivo(sesion, coleccion, vector):
    """Requisito 9: aun con umbral permisivo no se cruza de coleccion.

    `UMBRAL_PERMISIVO = -1.0` acepta cualquier vector y
    `MATCH_COUNT_GRANDE = 100` supera el total de filas existentes (incluidas
    otras colecciones como `benchmark_2026`). Si el filtro por `coleccion_id`
    no se aplicara antes de ordenar y limitar, esta prueba devolveria filas de
    la otra coleccion.
    """
    resultados = await buscar_por_coleccion(
        sesion,
        coleccion_id=coleccion,
        query_embedding=vector,
        match_threshold=UMBRAL_PERMISIVO,
        match_count=MATCH_COUNT_GRANDE,
    )

    assert len(resultados) >= FRAGMENTOS_ESPERADOS
    ajenos = [f for f in resultados if f.coleccion_id != coleccion]
    assert ajenos == [], (
        f"contaminacion cruzada en {coleccion}: "
        f"{[(f.id, f.coleccion_id) for f in ajenos]}"
    )


@pytest.mark.asyncio
async def test_dominios_distintos_no_se_prestan_resultados(sesion):
    """Una consulta financiera no recupera nada del dominio tributario."""
    financieros = await buscar_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        query_embedding=vector_tributario(),
        match_threshold=UMBRAL_PERMISIVO,
        match_count=MATCH_COUNT_GRANDE,
    )
    tributarios = await buscar_por_coleccion(
        sesion,
        coleccion_id=COLECCION_TRIBUTARIO,
        query_embedding=vector_financiero(),
        match_threshold=UMBRAL_PERMISIVO,
        match_count=MATCH_COUNT_GRANDE,
    )

    assert {f.coleccion_id for f in financieros} == {COLECCION_FINANCIERO}
    assert {f.coleccion_id for f in tributarios} == {COLECCION_TRIBUTARIO}
    assert financieros, tributarios


@pytest.mark.asyncio
async def test_coleccion_desconocida_devuelve_vacio(sesion):
    """Pedir una coleccion inexistente no devuelve las de los otros dominios."""
    resultados = await buscar_por_coleccion(
        sesion,
        coleccion_id="coleccion_que_no_existe",
        query_embedding=vector_financiero(),
        match_threshold=UMBRAL_PERMISIVO,
        match_count=MATCH_COUNT_GRANDE,
    )
    assert resultados == []


@pytest.mark.asyncio
async def test_umbral_excluye_elementos_del_dominio_contrario(sesion):
    """Con un umbral exigente, solo pasa la coleccion solicitada."""
    financieros = await buscar_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        query_embedding=vector_financiero(),
        match_threshold=0.9,
        match_count=10,
    )
    assert len(financieros) >= FRAGMENTOS_ESPERADOS
    assert all(f.similitud >= 0.9 for f in financieros)
    assert all(f.coleccion_id == COLECCION_FINANCIERO for f in financieros)


@pytest.mark.asyncio
async def test_las_consultas_no_modifican_los_registros(sesion):
    """Ninguna consulta altera el numero de registros de la tabla."""
    antes = await _contar_todo(sesion)

    for coleccion, vector in (
        (COLECCION_FINANCIERO, vector_financiero()),
        (COLECCION_TRIBUTARIO, vector_tributario()),
    ):
        await buscar_por_coleccion(
            sesion,
            coleccion_id=coleccion,
            query_embedding=vector,
            match_threshold=UMBRAL_PERMISIVO,
            match_count=MATCH_COUNT_GRANDE,
        )
        await buscar_por_coleccion(
            sesion,
            coleccion_id="coleccion_que_no_existe",
            query_embedding=vector,
            match_threshold=UMBRAL_PERMISIVO,
            match_count=MATCH_COUNT_GRANDE,
        )

    despues = await _contar_todo(sesion)
    assert despues == antes, f"los datos cambiaron: {antes} -> {despues}"