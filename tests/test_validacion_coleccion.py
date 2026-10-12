"""Pruebas de la validacion de `coleccion_id` (requisito 8 de la FASE 2).

Son pruebas puras: no necesitan base de datos y no tocan ningun registro.
Comprueban que el identificador se valida ANTES de construir la consulta, de
modo que un valor malformado nunca llega al servidor de bases de datos.
"""

import pytest

from helpers import COLECCION_FINANCIERO, COLECCION_TRIBUTARIO

from app.models.normativa import (
    COLECCION_ID_MAX_LEN,
    embedding_a_literal_pgvector,
    validar_coleccion_id,
)


class _SesionQueNoDebeUsarse:
    """Falla si cualquier consulta llega a ejecutarse."""

    async def execute(self, *args, **kwargs):
        raise AssertionError("la consulta no debia llegar a la base de datos")


@pytest.mark.parametrize(
    "coleccion",
    [
        COLECCION_FINANCIERO,
        COLECCION_TRIBUTARIO,
        "a",
        "Entidad-Reguladora_2026",
        "norma.v1.2",
        "0",
    ],
)
def test_colecciones_validas_se_admiten(coleccion):
    assert validar_coleccion_id(coleccion) == coleccion


@pytest.mark.parametrize(
    "coleccion, motivo",
    [
        ("", "vacio"),
        ("   ", "solo espacios"),
        (" espaciada", "espacio inicial"),
        ("espaciada ", "espacio final"),
        ("interior con espacio", "espacio interior"),
        ("x" * (COLECCION_ID_MAX_LEN + 1), "demasiado largo"),
        ("coleccion/otra", "barra no admitida"),
        ("coleccion#", "caracter especial"),
        ("coleccionñ", "no ascii"),
        ("coleccion id\n", "salto de linea"),
    ],
)
def test_colecciones_invalidas_se_rechazan(coleccion, motivo):
    with pytest.raises(ValueError) as error:
        validar_coleccion_id(coleccion)
    assert str(error.value)


def test_longitud_maxima_extremo_aceptada():
    limite = "a" * COLECCION_ID_MAX_LEN
    assert validar_coleccion_id(limite) == limite


def test_coleccion_demasiada_larga():
    with pytest.raises(ValueError):
        validar_coleccion_id("b" * (COLECCION_ID_MAX_LEN + 1))


@pytest.mark.asyncio
async def test_validacion_ocurre_antes_de_consultar_la_bd():
    """Un identificador invalido nunca genera una llamada a la base de datos."""
    from app.database.repository import buscar_por_coleccion

    with pytest.raises(ValueError):
        await buscar_por_coleccion(
            _SesionQueNoDebeUsarse(),  # type: ignore[arg-type]
            coleccion_id="",
            query_embedding=[0.0] * 768,
        )

    with pytest.raises(ValueError):
        await buscar_por_coleccion(
            _SesionQueNoDebeUsarse(),  # type: ignore[arg-type]
            coleccion_id="con espacio",
            query_embedding=[0.0] * 768,
        )


@pytest.mark.asyncio
async def test_dimension_del_embedding_se_comprueba():
    """Una consulta con la dimension equivocada se rechaza en Python."""
    from app.database.repository import buscar_por_coleccion

    with pytest.raises(ValueError):
        await buscar_por_coleccion(
            _SesionQueNoDebeUsarse(),  # type: ignore[arg-type]
            coleccion_id=COLECCION_FINANCIERO,
            query_embedding=[0.0] * 10,
        )


def test_embedding_a_literal_pgvector():
    assert embedding_a_literal_pgvector([1.0, 0.5, -0.25]) == "[1.0,0.5,-0.25]"


def test_embedding_vacio_se_rechaza():
    with pytest.raises(ValueError):
        embedding_a_literal_pgvector([])