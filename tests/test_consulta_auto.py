"""Pruebas del chat conversacional con deteccion de dominio (fase H).

Offline: embeddings/generacion son dobles y la RPC se parchea. Se verifica que
NUNCA se redacte normativa a partir de datos de prueba (seed://), que las
preguntas ambiguas no consulten a ciegas y que los fallos se distingan.
"""

from contextlib import asynccontextmanager

import pytest

from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    vector_financiero,
)

from app.models.consulta import PeticionConsultaAuto
from app.models.normativa import NormativaMatch
from app.services import chat as chat_svc
from app.services import consulta as consulta_svc
from app.services.chat import (
    ACLARACION_AMBIGUA,
    ADVERTENCIA_SEED_PARCIAL,
    ADVERTENCIA_SEED_UNICA,
    FUERA_DE_DOMINIO,
    COLECCION_NO_DISPONIBLE,
)
from app.services.consulta import SIN_EVIDENCIA


class EmbFalso:
    def __init__(self, vector=None):
        self.vector = vector or vector_financiero()
        self.textos = []

    async def generar(self, textos, cliente=None):
        self.textos = list(textos)
        return [list(self.vector)]


class GenFalso:
    def __init__(self, texto="respuesta generada"):
        self.texto = texto
        self.prompts = []

    async def generar(self, prompt, cliente=None):
        self.prompts.append(prompt)
        return self.texto


@asynccontextmanager
async def sesion_falsa():
    yield object()


class RpcRegistro:
    def __init__(self, filas=None):
        self.filas = filas or []

    async def __call__(self, sesion, **kwargs):
        return self.filas


def fila(coleccion, ident, origen="https://x.org/doc"):
    return NormativaMatch(
        id=ident,
        coleccion_id=coleccion,
        contenido=f"contenido {ident}",
        origen_url=origen,
        distancia=0.1,
        similitud=0.9,
    )


TODAS = {COLECCION_FINANCIERO, COLECCION_TRIBUTARIO}


async def test_pregunta_financiera_elige_coleccion_financiera(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila(COLECCION_FINANCIERO, 1), fila(COLECCION_FINANCIERO, 2)]),
    )
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa de interes del sistema financiero?"),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio == "financiero"
    assert resultado.coleccion_id == COLECCION_FINANCIERO
    assert resultado.respuesta == "respuesta generada"
    assert resultado.total_evidencias == 2
    assert resultado.advertencia is None
    assert len(gen.prompts) == 1


async def test_pregunta_tributaria_elige_coleccion_tributaria(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila(COLECCION_TRIBUTARIO, 1)]),
    )
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Como se declara el IVA de las facturas?"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio == "tributario"
    assert resultado.coleccion_id == COLECCION_TRIBUTARIO
    assert resultado.total_evidencias == 1


async def test_pregunta_ambigua_no_consulta(monkeypatch):
    llamado = {"n": 0}

    async def rpc(sesion, **kwargs):
        llamado["n"] += 1
        return []

    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", rpc)
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa del impuesto?"),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.ambigua is True
    assert resultado.dominio is None
    assert resultado.respuesta == ACLARACION_AMBIGUA
    assert resultado.total_evidencias == 0
    assert emb.textos == [] and llamado["n"] == 0


async def test_pregunta_fuera_de_dominio(monkeypatch):
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Como esta el clima hoy?"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio is None
    assert resultado.ambigua is False
    assert resultado.respuesta == FUERA_DE_DOMINIO


async def test_coleccion_no_disponible(monkeypatch):
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa de interes del sistema financiero?"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles=set(),
    )
    assert resultado.dominio == "financiero"
    assert resultado.coleccion_id == COLECCION_FINANCIERO
    assert resultado.respuesta == COLECCION_NO_DISPONIBLE.format(
        coleccion=COLECCION_FINANCIERO, dominio="financiero"
    )


async def test_solo_evidencia_seed_no_invoca_llm(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([
            fila(COLECCION_FINANCIERO, 1, "seed://aislamiento/financiero_bolivia_2026/1"),
            fila(COLECCION_FINANCIERO, 2, "seed://aislamiento/financiero_bolivia_2026/2"),
        ]),
    )
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa de interes del sistema financiero?"),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.respuesta == ADVERTENCIA_SEED_UNICA
    assert resultado.advertencia == ADVERTENCIA_SEED_UNICA
    assert gen.prompts == [], "no debe invocar al LLM con datos de prueba"
    assert all(ev.es_datos_prueba for ev in resultado.evidencias)


async def test_mezcla_seed_y_real_genera_solo_con_real(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([
            fila(COLECCION_FINANCIERO, 1, "seed://x/1"),
            fila(COLECCION_FINANCIERO, 2, "https://asfi.gob.bo/real.pdf"),
        ]),
    )
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa de interes del sistema financiero?"),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.advertencia == ADVERTENCIA_SEED_PARCIAL
    assert len(gen.prompts) == 1
    assert "seed://" not in gen.prompts[0], "el prompt no debe incluir datos de prueba"
    assert resultado.total_evidencias == 2  # se reportan todas, marcadas


async def test_dominio_forzado(monkeypatch):
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila(COLECCION_TRIBUTARIO, 1)]),
    )
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="una pregunta cualquiera", dominio="tributario"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio == "tributario"
    assert resultado.coleccion_id == COLECCION_TRIBUTARIO


async def test_sin_evidencia(monkeypatch):
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", RpcRegistro([]))
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Cual es la tasa de interes del sistema financiero?"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.total_evidencias == 0
    assert resultado.respuesta == SIN_EVIDENCIA


async def test_coleccion_explicita_con_dominio_desconocido(monkeypatch):
    """Una coleccion explicita ajena no rompe la inferencia de dominio."""
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila("otra_coleccion", 1)]),
    )
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="pregunta", coleccion_id="otra_coleccion"),
        cliente_embeddings=EmbFalso(), cliente_generacion=GenFalso(),
        abrir=sesion_falsa, colecciones_disponibles={"otra_coleccion"},
    )
    assert resultado.coleccion_id == "otra_coleccion"
    assert resultado.dominio is None
    assert resultado.total_evidencias == 1


# --- Recuperacion de la Resolucion de Directorio BCB 138/2026 -----------------

RD138_URL = (
    "https://www.bcb.gob.bo/webdocs/01_resoluciones/"
    "RD%20N%C2%B0%20138-2026%20%2821-09-2026%29.pdf"
)


def fila_rd138(ident: int) -> NormativaMatch:
    return NormativaMatch(
        id=ident,
        coleccion_id=COLECCION_FINANCIERO,
        contenido=(
            "REGLAMENTO DE POSICION DE CAMBIOS PARA LAS ENTIDADES DE "
            "INTERMEDIACION FINANCIERA. Articulo 1. La posicion de cambios "
            "se calcula segun el tipo de cambio oficial del BCB."
        ),
        origen_url=RD138_URL,
        distancia=0.05,
        similitud=0.95,
    )


async def test_rd138_se_clasifica_y_usa_como_contexto(monkeypatch):
    """Una pregunta sobre la RD 138/2026 se enruta a financiero y genera con
    la evidencia real recuperada (origen_url del PDF del BCB)."""
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila_rd138(1), fila_rd138(2)]),
    )
    emb, gen = EmbFalso(), GenFalso(texto="respuesta basada en la RD 138/2026")
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(
            pregunta="Que establece el Reglamento de Posicion de Cambios del BCB?"
        ),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio == "financiero"
    assert resultado.coleccion_id == COLECCION_FINANCIERO
    assert resultado.respuesta == "respuesta basada en la RD 138/2026"
    assert resultado.total_evidencias == 2
    assert len(gen.prompts) == 1
    # El contexto entregado al LLM procede del documento real.
    assert RD138_URL in gen.prompts[0]
    assert "POSICION DE CAMBIOS" in gen.prompts[0]
    assert all(ev.origen_url == RD138_URL for ev in resultado.evidencias)


async def test_rd138_sin_evidencia_no_inventa(monkeypatch):
    """Si no se recupera evidencia de la RD 138/2026, NO se llama al LLM."""
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", RpcRegistro([]))
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(pregunta="Que establece el Reglamento de Posicion de Cambios?"),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.dominio == "financiero"
    assert resultado.total_evidencias == 0
    assert resultado.respuesta == SIN_EVIDENCIA
    assert gen.prompts == [], "sin evidencia no debe invocarse al LLM"


async def test_aislamiento_no_recupera_otra_coleccion(monkeypatch):
    """Una consulta financiera no debe ver filas tributarias: si la RPC las
    devolviera, se aborta con ErrorContaminacion y no llega nada al LLM."""
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila(COLECCION_TRIBUTARIO, 1)]),
    )
    emb, gen = EmbFalso(), GenFalso()
    with pytest.raises(consulta_svc.ErrorContaminacion):
        await chat_svc.ejecutar_consulta_auto(
            PeticionConsultaAuto(
                pregunta="Que establece el Reglamento de Posicion de Cambios?"
            ),
            cliente_embeddings=emb, cliente_generacion=gen,
            abrir=sesion_falsa, colecciones_disponibles=TODAS,
        )
    assert gen.prompts == []


async def test_solo_recuperacion_no_invoca_llm(monkeypatch):
    """Con generar=False se devuelven las evidencias reales sin llamar al LLM."""
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion",
        RpcRegistro([fila(COLECCION_FINANCIERO, 1), fila(COLECCION_FINANCIERO, 2)]),
    )
    emb, gen = EmbFalso(), GenFalso()
    resultado = await chat_svc.ejecutar_consulta_auto(
        PeticionConsultaAuto(
            pregunta="Que establece el Reglamento de Posicion de Cambios?",
            generar=False,
        ),
        cliente_embeddings=emb, cliente_generacion=gen,
        abrir=sesion_falsa, colecciones_disponibles=TODAS,
    )
    assert resultado.solo_recuperacion is True
    assert resultado.total_evidencias == 2
    assert resultado.respuesta == consulta_svc.MENSAJE_SOLO_RECUPERACION
    assert gen.prompts == [], "generar=False no debe invocar al LLM"

