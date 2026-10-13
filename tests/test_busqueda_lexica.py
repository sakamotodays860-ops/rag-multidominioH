"""Pruebas de la busqueda LEXICA (PostgreSQL nativo) contra la base real.

Son pruebas de SOLO LECTURA: no insertan, ni borran, ni modifican datos, y NO
llaman a Gemini (la busqueda lexica no necesita embeddings). Verifican:

  A) recuperacion por numero de resolucion exacto (p. ej. ``138/2026``);
  B) normalizacion de acentos y mayusculas (diccionario ``spanish``);
  C) consulta sin lexemas utiles -> resultado vacio;
  D) aislamiento: una coleccion nunca devuelve filas de otra;
  E) coleccion desconocida -> vacio;
  F) la busqueda no altera el numero de registros.
"""

import pytest

from helpers import COLECCION_FINANCIERO, COLECCION_TRIBUTARIO

from app.database.repository import (
    buscar_lexico_por_coleccion,
    listar_colecciones,
)


@pytest.mark.asyncio
async def test_lexica_por_resolucion_exacta(sesion):
    """Un numero de resolucion literal prioriza fragmentos con coincidencia exacta."""
    filas = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="R.D. N° 138/2026",
        match_count=5,
    )
    assert filas, "deberia recuperar la resolucion 138/2026 indexada"
    assert all(f.coleccion_id == COLECCION_FINANCIERO for f in filas)
    assert any(f.coincidencia_exacta for f in filas)
    # La coincidencia exacta va primero (orden reproducible).
    assert filas[0].coincidencia_exacta is True


@pytest.mark.asyncio
async def test_lexica_normaliza_acentos_y_mayusculas(sesion):
    """'POSICION DE CAMBIOS' y 'posición de cambios' recuperan lo mismo."""
    mayus = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="POSICION DE CAMBIOS",
        match_count=5,
    )
    minus = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="posición de cambios",
        match_count=5,
    )
    assert mayus, "deberia haber normativa sobre posicion de cambios"
    assert [f.id for f in mayus] == [f.id for f in minus]


@pytest.mark.asyncio
async def test_lexica_sin_lexemas_devuelve_vacio(sesion):
    """Una consulta solo con palabras vacias no inventa coincidencias."""
    filas = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="de la el los las un una",
        match_count=5,
    )
    assert filas == []


@pytest.mark.asyncio
async def test_lexica_respeta_aislamiento(sesion):
    """Consultar una coleccion jamas devuelve filas de la otra."""
    financiero = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="aduana importacion resolucion",
        match_count=10,
    )
    tributario = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_TRIBUTARIO,
        consulta="reglamento posicion de cambios encaje",
        match_count=10,
    )
    assert all(f.coleccion_id == COLECCION_FINANCIERO for f in financiero)
    assert all(f.coleccion_id == COLECCION_TRIBUTARIO for f in tributario)


@pytest.mark.asyncio
async def test_lexica_coleccion_desconocida_vacia(sesion):
    filas = await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id="coleccion_que_no_existe",
        consulta="posicion de cambios",
        match_count=5,
    )
    assert filas == []


@pytest.mark.asyncio
async def test_lexica_valida_entradas(sesion):
    with pytest.raises(ValueError):
        await buscar_lexico_por_coleccion(
            sesion, coleccion_id="mal id", consulta="algo", match_count=5
        )
    with pytest.raises(ValueError):
        await buscar_lexico_por_coleccion(
            sesion, coleccion_id=COLECCION_FINANCIERO, consulta="   ", match_count=5
        )
    with pytest.raises(ValueError):
        await buscar_lexico_por_coleccion(
            sesion,
            coleccion_id=COLECCION_FINANCIERO,
            consulta="algo",
            match_count=0,
        )


@pytest.mark.asyncio
async def test_lexica_no_modifica_datos(sesion):
    antes = await listar_colecciones(sesion)
    await buscar_lexico_por_coleccion(
        sesion,
        coleccion_id=COLECCION_FINANCIERO,
        consulta="posicion de cambios",
        match_count=10,
    )
    despues = await listar_colecciones(sesion)
    assert antes == despues
