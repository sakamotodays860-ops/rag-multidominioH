"""Pruebas de aislamiento DURANTE la insercion (FASE 3, requisito 6).

`insertar_fragmentos` estampa ``coleccion_id`` una sola vez (a partir del
parametro ya validado) y las filas nunca llegan con un identificador ajeno:
es la garantia de que la ingesta de una coleccion no contamina otra.

Estrategia de seguridad: las inserciones ocurren en una transaccion que las
pruebas NO confirman; al hacer ``rollback`` no queda ni una fila nueva. Las
8 filas de la FASE 2 (4 + 4) quedan intactas.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    FRAGMENTOS_ESPERADOS,
    vector_financiero,
)

from app.database.repository import (
    buscar_por_coleccion,
    contar_coleccion,
    insertar_fragmentos,
)

COLECCION_DE_PRUEBA = "fase3_prueba_aislamiento"

REGISTROS = [
    {
        "contenido": "Articulo 1 de la coleccion de prueba.",
        "origen_url": "https://example.com/a",
        "embedding": vector_financiero(),
    },
    {
        "contenido": "Articulo 2 de la coleccion de prueba.",
        "origen_url": "https://example.com/b",
        "embedding": vector_financiero(),
    },
    {
        "contenido": "Articulo 3 de la coleccion de prueba.",
        "origen_url": None,
        "embedding": vector_financiero(),
    },
]


async def _conteos_por_coleccion(sesion) -> dict[str, int]:
    resultado = await sesion.execute(
        text(
            "SELECT coleccion_id, count(*) FROM normativa_bancaria "
            "GROUP BY coleccion_id ORDER BY coleccion_id"
        )
    )
    return {fila[0]: int(fila[1]) for fila in resultado.all()}


@pytest.mark.asyncio
async def test_insercion_estampa_el_coleccion_id_del_parametro(sesion):
    """Cada fila insertada pertenece a la coleccion pedida, nunca otra."""
    insertados = await insertar_fragmentos(
        sesion, coleccion_id=COLECCION_DE_PRUEBA, registros=REGISTROS
    )
    assert insertados == len(REGISTROS)

    conteos = await _conteos_por_coleccion(sesion)
    # Dentro de la MISMA transaccion se ven las filas nuevas, con el id
    # estampado por el parametro y no por los registros...
    assert conteos[COLECCION_DE_PRUEBA] == len(REGISTROS)

    filas = await sesion.execute(
        text(
            "SELECT coleccion_id FROM normativa_bancaria "
            "WHERE contenido LIKE 'Articulo % de la coleccion de prueba.'"
        )
    )
    origenes = [fila[0] for fila in filas.all()]
    assert len(origenes) == len(REGISTROS)
    assert set(origenes) == {COLECCION_DE_PRUEBA}, f"contaminado: {origenes}"

    await sesion.rollback()


@pytest.mark.asyncio
async def test_las_8_filas_de_fase_2_no_cambian_durante_la_insercion(sesion):
    """Mientras hay filas pendientes, la base de demostracion sigue 4 + 4."""
    assert await contar_coleccion(sesion, COLECCION_FINANCIERO) == FRAGMENTOS_ESPERADOS
    assert await contar_coleccion(sesion, COLECCION_TRIBUTARIO) == FRAGMENTOS_ESPERADOS

    await insertar_fragmentos(
        sesion, coleccion_id=COLECCION_DE_PRUEBA, registros=REGISTROS
    )

    assert await contar_coleccion(sesion, COLECCION_FINANCIERO) == FRAGMENTOS_ESPERADOS
    assert await contar_coleccion(sesion, COLECCION_TRIBUTARIO) == FRAGMENTOS_ESPERADOS

    # La busqueda vectorial financiera sigue sin ver las filas de la prueba.
    financieros = await buscar_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        query_embedding=vector_financiero(),
        match_threshold=-1.0,
        match_count=100,
    )
    assert {f.coleccion_id for f in financieros} == {COLECCION_FINANCIERO}

    await sesion.rollback()


@pytest.mark.asyncio
async def test_insertar_vacio_devuelve_cero(sesion):
    assert (
        await insertar_fragmentos(
            sesion, coleccion_id=COLECCION_DE_PRUEBA, registros=[]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_coleccion_id_invalido_no_inserte_nada(sesion):
    with pytest.raises(ValueError):
        await insertar_fragmentos(
            sesion, coleccion_id="id con espacios", registros=REGISTROS
        )


@pytest.mark.asyncio
async def test_tras_rollback_no_queda_rastro_en_doble_sesion(motor, sesion):
    """Tras `rollback` ninguna sesion nueva encuentra las filas de prueba."""
    await insertar_fragmentos(
        sesion, coleccion_id=COLECCION_DE_PRUEBA, registros=REGISTROS
    )
    assert await contar_coleccion(sesion, COLECCION_DE_PRUEBA) == len(REGISTROS)
    await sesion.rollback()

    # Sesion NUEVA: las filas rollbacked no existen ni se ven.
    factoria = async_sessionmaker(motor, expire_on_commit=False)
    async with factoria() as sesion_nueva:
        conteos = await _conteos_por_coleccion(sesion_nueva)
    assert COLECCION_DE_PRUEBA not in conteos
    assert conteos.get(COLECCION_FINANCIERO) == FRAGMENTOS_ESPERADOS
    assert conteos.get(COLECCION_TRIBUTARIO) == FRAGMENTOS_ESPERADOS