"""Pruebas de la FASE 4 (consulta RAG) 100% mockeadas.

NO tocan red ni base de datos reales: se inyectan clientes de embeddings y de
generacion falsos, y se parchea la funcion de busqueda. Cubren los requisitos
de la consulta desde el flujo de servicio hasta el mapeo HTTP del endpoint.

- REQUISITO 1: validacion (pydantic 422 y dominio 400).
- REQUISITO 2: la pregunta se embehde con el mismo cliente de FASE 3.
- REQUISITO 3: el LLM recibe temperatura 0.0 y el modelo configurado.
- REQUISITO 4: la RPC se invoca con `coleccion_id`, `query_embedding`,
  `match_threshold` y `match_count` exactos.
- REQUISITO 5: cualquier fila ajena a la coleccion pedida aborta la consulta
  y la evidencia NUNCA llega al LLM.
- REQUISITO 6: sin evidencia -> NO se invoca al LLM y `total_evidencias=0`.
- REQUISITOS 7-9: respuesta estructurada, prompt restrictivo y traduccion de
  errores (429 / timeouts / proveedor caido -> 502).
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import ValidationError

import app.api.v1.consulta as api_consulta
import app.services.consulta as consulta_svc
from app.api.v1.consulta import consultar
from app.models.consulta import PREGUNTA_MAX_CARACTERES, PeticionConsulta
from app.models.normativa import NormativaMatch
from app.services.consulta import (
    SIN_EVIDENCIA,
    ErrorBaseDatos,
    ErrorContaminacion,
    ejecutar_consulta,
)
from app.services.embeddings import ErrorEmbeddings
from app.services.generacion import ClienteGeneracionGemini, ErrorGeneracion
from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    vector_financiero,
    vector_tributario,
)


# ---------------------------------------------------------------------------
# Dobles de pruebas
# ---------------------------------------------------------------------------


class EmbFalso:
    """Cliente de embeddings falsos: registra la pregunta que recibe."""

    def __init__(self, respuesta=None, error=None):
        self.respuesta = [vector_financiero()] if respuesta is None else respuesta
        self.error = error
        self.textos = []

    async def generar(self, textos, cliente=None):
        self.textos = list(textos)
        if self.error:
            raise self.error
        return [list(v) for v in self.respuesta]


class GeneradorFalso:
    """Cliente de generacion falso: registra los prompts que recibe."""

    def __init__(self, texto="respuesta de prueba", error=None):
        self.texto = texto
        self.error = error
        self.prompts = []

    async def generar(self, prompt, cliente=None):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.texto


@asynccontextmanager
async def sesion_falsa():
    """Sesion vacia: la busqueda se parchea, no llega a usarse."""
    yield object()


class RpcRegistro:
    """Sustituto de `buscar_por_coleccion`: registra los argumentos."""

    def __init__(self, filas=None):
        self.filas = filas if filas is not None else []
        self.kwargs = None

    async def __call__(self, sesion, **kwargs):
        self.kwargs = kwargs
        return self.filas


def fila_normativa(coleccion: str, ident: int, limite: float = 0.9) -> NormativaMatch:
    """Una fila de la RPC perteneciente a una coleccion concreta."""
    return NormativaMatch(
        id=ident,
        coleccion_id=coleccion,
        contenido=f"contenido de {coleccion} {ident}",
        origen_url=f"https://ejemplo.org/{coleccion}/{ident}",
        distancia=0.1,
        similitud=limite,
    )


# ---------------------------------------------------------------------------
# REQUISITO 1: validacion (a nivel de modelo y de dominio)
# ---------------------------------------------------------------------------


async def test_pregunta_vacia_o_solo_espacios_no_valida():
    for pregunta in ("", "   ", "\t\n  "):
        with pytest.raises(ValidationError):
            PeticionConsulta(coleccion_id=COLECCION_FINANCIERO, pregunta=pregunta)


async def test_pregunta_demasiado_larga_no_valida():
    con_exceso = "x" * (PREGUNTA_MAX_CARACTERES + 1)
    with pytest.raises(ValidationError):
        PeticionConsulta(coleccion_id=COLECCION_FINANCIERO, pregunta=con_exceso)


async def test_top_k_y_threshold_fuera_de_rango_no_validan():
    with pytest.raises(ValidationError):
        PeticionConsulta(
            coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta valida", top_k=0
        )
    with pytest.raises(ValidationError):
        PeticionConsulta(
            coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta valida", top_k=51
        )
    with pytest.raises(ValidationError):
        PeticionConsulta(
            coleccion_id=COLECCION_FINANCIERO,
            pregunta="pregunta valida",
            similarity_threshold=1.5,
        )


async def test_coleccion_id_ilegal_se_rechaza_sin_buscar(monkeypatch):
    """Un `coleccion_id` ilegal se rechaza ANTES de embeber la pregunta."""
    rpc = RpcRegistro(filas=[fila_normativa(COLECCION_FINANCIERO, 1)])
    emb = EmbFalso()
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", rpc)

    peticion = PeticionConsulta(
        coleccion_id="financiero_bolivia_2026/extra", pregunta="pregunta"
    )
    with pytest.raises(ValueError):
        await ejecutar_consulta(
            peticion,
            cliente_embeddings=emb,
            cliente_generacion=GeneradorFalso(),
            abrir=sesion_falsa,
        )
    assert emb.textos == []  # ni siquiera se embehde la pregunta
    assert rpc.kwargs is None  # ni se invoca la RPC


# ---------------------------------------------------------------------------
# REQUISITOS 2-7: flujo feliz
# ---------------------------------------------------------------------------


async def test_consulta_financiero_devuelve_respuesta_estructurada(monkeypatch):
    emb = EmbFalso()
    generador = GeneradorFalso()
    rpc = RpcRegistro(
        filas=[fila_normativa(COLECCION_FINANCIERO, i) for i in range(1, 5)]
    )
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", rpc)
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="Que regula la ASFI?"
    )

    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=emb,
        cliente_generacion=generador,
        abrir=sesion_falsa,
    )

    assert resultado.coleccion_id == COLECCION_FINANCIERO
    assert resultado.pregunta == "Que regula la ASFI?"
    assert resultado.total_evidencias == 4
    assert resultado.respuesta == "respuesta de prueba"
    assert [e.contenido for e in resultado.evidencias] == [
        f"contenido de {COLECCION_FINANCIERO} {i}" for i in range(1, 5)
    ]
    assert {e.coleccion_id for e in resultado.evidencias} == {COLECCION_FINANCIERO}

    # REQUISITO 2: la pregunta es lo que se embehde.
    assert emb.textos == ["Que regula la ASFI?"]

    # REQUISITO 7: el prompt usa la evidencia recuperada y es restrictivo.
    assert len(generador.prompts) == 1
    prompt = generador.prompts[0]
    assert peticion.pregunta in prompt
    assert COLECCION_FINANCIERO in prompt
    for evidencia in resultado.evidencias:
        assert evidencia.contenido in prompt
    for regla in ("No inventes", "normativa recuperada", "solo la evidencia"):
        assert regla in prompt


async def test_consulta_tributario_devuelve_respuesta_estructurada(monkeypatch):
    emb = EmbFalso(respuesta=[vector_tributario()])
    generador = GeneradorFalso(texto="respuesta tributaria")
    rpc = RpcRegistro(
        filas=[fila_normativa(COLECCION_TRIBUTARIO, i) for i in range(1, 5)]
    )
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", rpc)
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_TRIBUTARIO, pregunta="Que es el IVA?"
    )

    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=emb,
        cliente_generacion=generador,
        abrir=sesion_falsa,
    )

    assert resultado.total_evidencias == 4
    assert {e.coleccion_id for e in resultado.evidencias} == {COLECCION_TRIBUTARIO}
    assert resultado.respuesta == "respuesta tributaria"
    assert emb.textos == ["Que es el IVA?"]


async def test_rpc_recibe_parametros_exactos(monkeypatch):
    rpc = RpcRegistro(filas=[fila_normativa(COLECCION_FINANCIERO, 1)])
    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", rpc)
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO,
        pregunta="pregunta",
        top_k=3,
        similarity_threshold=0.7,
    )

    await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbFalso(),
        cliente_generacion=GeneradorFalso(),
        abrir=sesion_falsa,
    )

    # REQUISITO 4: args exactos -> la RPC filtra por coleccion y por top_k.
    assert rpc.kwargs["coleccion_id"] == COLECCION_FINANCIERO
    assert rpc.kwargs["match_count"] == 3
    assert rpc.kwargs["match_threshold"] == 0.7
    assert len(rpc.kwargs["query_embedding"]) == 768


# ---------------------------------------------------------------------------
# REQUISITO 5: aislamiento estricto
# ---------------------------------------------------------------------------


async def test_fila_de_otra_coleccion_aborta_sin_pasar_al_llm(monkeypatch):
    """La contaminacion cruzada se detecta y NUNCA llega al LLM (REQUISITO 5)."""
    filas = [fila_normativa(COLECCION_FINANCIERO, i) for i in range(1, 4)]
    filas.append(fila_normativa(COLECCION_TRIBUTARIO, 99))
    generador = GeneradorFalso()
    monkeypatch.setattr(
        consulta_svc,
        "buscar_por_coleccion",
        RpcRegistro(filas=filas),
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta"
    )

    with pytest.raises(ErrorContaminacion) as error:
        await ejecutar_consulta(
            peticion,
            cliente_embeddings=EmbFalso(),
            cliente_generacion=generador,
            abrir=sesion_falsa,
        )
    assert COLECCION_TRIBUTARIO in str(error.value)
    assert generador.prompts == []  # la evidencia ajena no llego al prompt


# ---------------------------------------------------------------------------
# REQUISITO 6: sin evidencia -> sin LLM
# ---------------------------------------------------------------------------


async def test_sin_evidencia_no_se_invoca_al_llm(monkeypatch):
    generador = GeneradorFalso()
    monkeypatch.setattr(
        consulta_svc, "buscar_por_coleccion", RpcRegistro(filas=[])
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta sin respuesta"
    )

    resultado = await ejecutar_consulta(
        peticion,
        cliente_embeddings=EmbFalso(),
        cliente_generacion=generador,
        abrir=sesion_falsa,
    )

    assert resultado.total_evidencias == 0
    assert resultado.evidencias == []
    assert resultado.respuesta == SIN_EVIDENCIA
    assert generador.prompts == []  # el LLM NO se llamo


# ---------------------------------------------------------------------------
# REQUISITOS 8-9: prompt determinista y traduccion de errores -> 502
# ---------------------------------------------------------------------------


async def test_cliente_generacion_usa_temperatura_0_y_modelo_configurado():
    capturas: dict = {}

    def transporte(peticion_http: httpx.Request) -> httpx.Response:
        capturas["json"] = peticion_http.content
        capturas["url"] = str(peticion_http.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "respuesta generada"}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    clave = "clave-de-prueba"
    generador = ClienteGeneracionGemini(
        api_key=clave, modelo="gemini-2.0-flash", temperatura=0.0
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transporte)) as cliente:
        texto = await generador.generar("prompt de prueba", cliente=cliente)

    import json as _json

    payload = _json.loads(capturas["json"])
    assert payload["generationConfig"]["temperature"] == 0.0
    assert "generateContent" in capturas["url"]
    assert "gemini-2.0-flash" in capturas["url"]
    assert texto == "respuesta generada"


async def test_429_de_embeddings_propaga_como_error_de_proveedor():
    emb = EmbFalso(
        error=ErrorEmbeddings(
            "Gemini no respondio tras 5 intentos: HTTP 429: quota exceeded"
        )
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta"
    )
    with pytest.raises(ErrorEmbeddings):
        await ejecutar_consulta(
            peticion,
            cliente_embeddings=emb,
            cliente_generacion=GeneradorFalso(),
            abrir=sesion_falsa,
        )


async def test_error_de_generacion_propaga(monkeypatch):
    generador = GeneradorFalso(
        error=ErrorGeneracion("Gemini devolvio una respuesta vacia.")
    )
    monkeypatch.setattr(
        consulta_svc,
        "buscar_por_coleccion",
        RpcRegistro(filas=[fila_normativa(COLECCION_FINANCIERO, 1)]),
    )
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta"
    )
    with pytest.raises(ErrorGeneracion):
        await ejecutar_consulta(
            peticion,
            cliente_embeddings=EmbFalso(),
            cliente_generacion=generador,
            abrir=sesion_falsa,
        )


async def test_error_de_basededatos_se_envuelve(monkeypatch):
    async def error_db(sesion, **kwargs):
        raise RuntimeError("conexion perdida con postgres")

    monkeypatch.setattr(consulta_svc, "buscar_por_coleccion", error_db)
    peticion = PeticionConsulta(
        coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta"
    )
    with pytest.raises(ErrorBaseDatos):
        await ejecutar_consulta(
            peticion,
            cliente_embeddings=EmbFalso(),
            cliente_generacion=GeneradorFalso(),
            abrir=sesion_falsa,
        )


async def peticion_valida() -> PeticionConsulta:
    return PeticionConsulta(coleccion_id=COLECCION_FINANCIERO, pregunta="pregunta")


async def test_endpoint_traduccion_a_codigos_http(monkeypatch):
    import fastapi

    base = await peticion_valida()

    casos = [
        (ValueError("coleccion no valida"), fastapi.status.HTTP_400_BAD_REQUEST),
        (ErrorEmbeddings("429"), fastapi.status.HTTP_502_BAD_GATEWAY),
        (ErrorGeneracion("vacio"), fastapi.status.HTTP_502_BAD_GATEWAY),
        (ErrorBaseDatos("caida"), fastapi.status.HTTP_502_BAD_GATEWAY),
        (ErrorContaminacion("ajena"), fastapi.status.HTTP_502_BAD_GATEWAY),
        (httpx.ConnectError("sin red"), fastapi.status.HTTP_502_BAD_GATEWAY),
        (httpx.TimeoutException("lento"), fastapi.status.HTTP_502_BAD_GATEWAY),
    ]
    for excepcion, codigo in casos:
        async def defecto(peticion_interna, fallo=excepcion):
            raise fallo

        monkeypatch.setattr(api_consulta, "ejecutar_consulta", defecto)
        with pytest.raises(fastapi.HTTPException) as error:
            await consultar(base)
        assert error.value.status_code == codigo, excepcion