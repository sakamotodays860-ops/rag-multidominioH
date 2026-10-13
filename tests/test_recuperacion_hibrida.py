"""Pruebas de la busqueda HIBRIDA (fusion vectorial + lexica) y del flujo de
servicio con busqueda lexica.

Offline: la RPC vectorial y la busqueda lexica se inyectan/parchean, y los
clientes de embeddings/generacion son dobles. No hay red, ni Gemini, ni base de
datos. Se comprueba que la fusion deduplica, ordena de forma reproducible, no
inventa similitudes y respeta el aislamiento.
"""

from contextlib import asynccontextmanager

import pytest

import app.services.consulta as consulta_svc
from app.models.consulta import PeticionConsulta
from app.models.normativa import NormativaMatch, NormativaMatchLexico
from app.services.consulta import (
    ErrorContaminacion,
    ejecutar_consulta,
    recuperar_evidencias,
)
from app.services.embeddings import ErrorEmbeddings
from app.services.recuperacion import (
    TIPO_HIBRIDA,
    TIPO_LEXICA,
    TIPO_VECTORIAL,
    fusionar_resultados,
)
from helpers import COLECCION_FINANCIERO, COLECCION_TRIBUTARIO, vector_financiero


def es_prueba(origen_url):
    return bool(origen_url) and origen_url.startswith("seed://")


def vec(ident, coleccion=COLECCION_FINANCIERO, similitud=0.9, url=None):
    return NormativaMatch(
        id=ident,
        coleccion_id=coleccion,
        contenido=f"contenido vectorial {ident}",
        origen_url=url or f"https://x.org/{ident}",
        distancia=1.0 - similitud,
        similitud=similitud,
    )


def lex(ident, coleccion=COLECCION_FINANCIERO, terminos=2, exacta=False, url=None):
    return NormativaMatchLexico(
        id=ident,
        coleccion_id=coleccion,
        contenido=f"contenido lexico {ident}",
        origen_url=url or f"https://x.org/{ident}",
        rank_fts=0.3,
        terminos=terminos,
        coincidencia_exacta=exacta,
    )


# --- Fusion por RRF ---------------------------------------------------------


def test_fusion_deduplica_y_marca_hibrida():
    resultado = fusionar_resultados(
        [vec(1), vec(2)],
        [lex(2, terminos=3), lex(3)],
        limite=10,
        es_prueba=es_prueba,
    )
    ids = [ev.id for ev in resultado]
    assert ids.count(2) == 1, "el mismo fragmento no se duplica"
    comun = next(ev for ev in resultado if ev.id == 2)
    assert comun.tipo_busqueda == TIPO_HIBRIDA
    assert comun.similitud == 0.9  # conserva la similitud vectorial
    assert comun.puntuacion_lexica == 0.3
    assert comun.terminos_coincidentes == 3


def test_fusion_lexica_sin_similitud_no_inventa():
    resultado = fusionar_resultados([], [lex(7)], limite=10, es_prueba=es_prueba)
    assert len(resultado) == 1
    ev = resultado[0]
    assert ev.tipo_busqueda == TIPO_LEXICA
    assert ev.similitud is None, "no hay vector: no se inventa una similitud"
    assert ev.puntuacion_rrf is not None


def test_fusion_vectorial_conserva_tipo_y_similitud():
    resultado = fusionar_resultados([vec(1)], [], limite=10, es_prueba=es_prueba)
    assert resultado[0].tipo_busqueda == TIPO_VECTORIAL
    assert resultado[0].similitud == 0.9


def test_fusion_orden_reproducible():
    a = fusionar_resultados([vec(1), vec(2), vec(3)], [], limite=10, es_prueba=es_prueba)
    b = fusionar_resultados([vec(1), vec(2), vec(3)], [], limite=10, es_prueba=es_prueba)
    assert [ev.id for ev in a] == [ev.id for ev in b] == [1, 2, 3]


def test_fusion_prioriza_coincidencia_exacta():
    # Sin bono, C empataria con A; el bono por coincidencia exacta la adelanta.
    resultado = fusionar_resultados(
        [vec(1), vec(2)],
        [lex(3, exacta=True)],
        limite=10,
        es_prueba=es_prueba,
    )
    assert resultado[0].id == 3
    assert resultado[0].coincidencia_exacta is True


def test_fusion_respeta_limite():
    resultado = fusionar_resultados(
        [vec(i) for i in range(1, 6)],
        [lex(i) for i in range(10, 15)],
        limite=3,
        es_prueba=es_prueba,
    )
    assert len(resultado) == 3


def test_fusion_marca_datos_de_prueba():
    resultado = fusionar_resultados(
        [vec(1, url="seed://x/1")], [], limite=10, es_prueba=es_prueba
    )
    assert resultado[0].es_datos_prueba is True


# --- Flujo de servicio ------------------------------------------------------


@asynccontextmanager
async def sesion_falsa():
    yield object()


class EmbFalso:
    def __init__(self, error=None):
        self.error = error
        self.textos = []

    async def generar(self, textos, cliente=None):
        self.textos = list(textos)
        if self.error:
            raise self.error
        return [vector_financiero()]


class GenFalso:
    def __init__(self):
        self.prompts = []

    async def generar(self, prompt, cliente=None):
        self.prompts.append(prompt)
        return "respuesta generada"


class Registro:
    def __init__(self, filas):
        self.filas = filas
        self.kwargs = None

    async def __call__(self, sesion, **kwargs):
        self.kwargs = kwargs
        return self.filas


@pytest.mark.asyncio
async def test_hibrido_combina_vectorial_y_lexica(monkeypatch):
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", Registro([vec(1), vec(2)]))
    monkeypatch.setattr(
        consulta_svc, "buscar_lexico_por_coleccion", Registro([lex(3, terminos=4)])
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="posicion de cambios"
    )
    evidencias = await recuperar_evidencias(
        peticion,
        cliente_embeddings=EmbFalso(),
        abrir=sesion_falsa,
        usar_lexico=True,
    )
    tipos = {ev.id: ev.tipo_busqueda for ev in evidencias}
    assert tipos[1] == TIPO_VECTORIAL
    assert tipos[3] == TIPO_LEXICA
    assert len(evidencias) == 3


@pytest.mark.asyncio
async def test_lexica_sin_embedding_continua_si_se_permite(monkeypatch):
    monkeypatch.setattr(
        consulta_svc,
        "buscar_lexico_por_coleccion",
        Registro([lex(3, terminos=4)]),
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="138/2026"
    )
    evidencias = await recuperar_evidencias(
        peticion,
        cliente_embeddings=EmbFalso(error=ErrorEmbeddings("429 cuota agotada")),
        abrir=sesion_falsa,
        usar_lexico=True,
        permitir_lexico_sin_embedding=True,
    )
    assert len(evidencias) == 1
    assert evidencias[0].tipo_busqueda == TIPO_LEXICA
    assert evidencias[0].similitud is None


@pytest.mark.asyncio
async def test_lexica_sin_embedding_repropaga_si_no_se_permite(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_lexico_por_coleccion", Registro([lex(3)])
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="138/2026"
    )
    with pytest.raises(ErrorEmbeddings):
        await recuperar_evidencias(
            peticion,
            cliente_embeddings=EmbFalso(error=ErrorEmbeddings("429")),
            abrir=sesion_falsa,
            usar_lexico=True,
            permitir_lexico_sin_embedding=False,
        )


@pytest.mark.asyncio
async def test_aislamiento_de_la_busqueda_lexica_aborta(monkeypatch):
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", Registro([]))
    monkeypatch.setattr(
        consulta_svc,
        "buscar_lexico_por_coleccion",
        Registro([lex(99, coleccion=COLECCION_TRIBUTARIO)]),
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="posicion de cambios"
    )
    with pytest.raises(ErrorContaminacion):
        await recuperar_evidencias(
            peticion,
            cliente_embeddings=EmbFalso(),
            abrir=sesion_falsa,
            usar_lexico=True,
        )


@pytest.mark.asyncio
async def test_generar_false_no_invoca_al_llm(monkeypatch):
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", Registro([vec(1)]))
    gen = GenFalso()
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta", generar=False
    )
    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbFalso(),
        cliente_generacion=gen,
        abrir=sesion_falsa,
    )
    assert resultado.solo_recuperacion is True
    assert resultado.total_evidencias == 1
    assert gen.prompts == [], "no debe invocarse al LLM con generar=False"
