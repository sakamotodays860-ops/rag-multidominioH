"""Pruebas de aislamiento en la CONSULTA (FASE 4, requisito 5).

A diferencia de ``test_consulta.py`` (mocks puros), aqui la consulta corre
contra la base de datos y la RPC REAL ``match_normativa_coleccion``. Solo los
clientes de Gemini (embeddings de la pregunta y generacion) estan simulados,
de modo que ninguna llamada saliente depende del exterior.

La premisa de la FASE 2: con el vector del dominio financiero despegado (eje
0) o el tributario (eje 1), la RPC devuelve SOLO filas de la coleccion pedida.
Desde la FASE 6 las colecciones pueden tener ademas documentos reales, por lo
que se exige al menos el tamano semilla y que no haya contaminacion cruzada.
"""

from contextlib import asynccontextmanager

from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    FRAGMENTOS_ESPERADOS,
    vector_financiero,
    vector_tributario,
)

from app.models.consulta import PeticionConsulta
from app.services.consulta import ejecutar_consulta


@asynccontextmanager
async def usar_sesion(sesion):
    """Sesion de prueba real (inyectada como `abrir`)."""
    yield sesion


class EmbamientosFijos:
    """Devuelve siempre el vector indicado (nunca toca red)."""

    def __init__(self, vector):
        self.vector = list(vector)

    async def generar(self, textos, cliente=None):
        return [list(self.vector)]


class GeneradorFijo:
    """Respuesta fija: nunca toca red."""

    def __init__(self, texto="OK (respuesta simulada)"):
        self.texto = texto

    async def generar(self, prompt, cliente=None):
        return self.texto


async def test_consulta_financiero_devuelve_solo_financiero(sesion):
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO,
        pregunta="Que regula el sistema financiero boliviano?",
    )
    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbamientosFijos(vector_financiero()),
        cliente_generacion=GeneradorFijo(),
        abrir=lambda: usar_sesion(sesion),
    )

    assert resultado.total_evidencias >= FRAGMENTOS_ESPERADOS
    assert resultado.coleccion_id == COLECCION_FINANCIERO
    assert {e.coleccion_id for e in resultado.evidencias} == {COLECCION_FINANCIERO}
    assert resultado.respuesta.startswith("OK")


async def test_consulta_tributario_devuelve_solo_tributario(sesion):
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_TRIBUTARIO,
        pregunta="Que es el Impuesto al Valor Agregado?",
    )
    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbamientosFijos(vector_tributario()),
        cliente_generacion=GeneradorFijo(),
        abrir=lambda: usar_sesion(sesion),
    )

    assert resultado.total_evidencias >= FRAGMENTOS_ESPERADOS
    assert {e.coleccion_id for e in resultado.evidencias} == {COLECCION_TRIBUTARIO}
    assert resultado.respuesta.startswith("OK")


async def test_consulta_financiero_con_vector_tributario_no_fuga(sesion):
    """Vector TRIBUTARIO + coleccion FINANCIERO: la RPC, acotada a la coleccion
    pedida, ni siquiera alcanza el umbral (0.5) -> 0 evidencias y respuesta
    controlada. Prueba que nada ajeno se desliza y que el umbral funciona."""
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO,
        pregunta="una pregunta cualquiera",
    )
    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbamientosFijos(vector_tributario()),
        cliente_generacion=GeneradorFijo(),
        abrir=lambda: usar_sesion(sesion),
    )

    assert resultado.total_evidencias == 0
    assert resultado.evidencias == []
    assert "No se encontro evidencia" in resultado.respuesta