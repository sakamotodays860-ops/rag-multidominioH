"""Pruebas del cliente de embeddings Gemini (FASE 3, requisito 5).

TODAS usan `httpx.MockTransport`: nunca hay una llamada real a Gemini. Se
verifican los comportamientos del enunciado:

* el modelo solicitado es exactamente `models/gemini-embedding-001`;
* se pide `outputDimensionality: 768` (compatibilidad VECTOR(768));
* las llamadas son POR LOTES (`:batchEmbedContents`), no una por chunk;
* ante 429 / RESOURCE_EXHAUSTED se reintenta un numero FINITO de veces con
  backoff progresivo y limite superior (`Retry-After` respetado);
* los errores definitivos elevan :class:`ErrorEmbeddings`.
"""

import json

import httpx
import pytest

from app.services.embeddings import ClienteEmbeddingsGemini, ErrorEmbeddings

DIMENSION = 768


def vector_n(dimension: int = DIMENSION, semilla: float = 0.5) -> list[float]:
    """Vector de `dimension` floats no trivial para verificar respuestas."""
    return [semilla + (i / 10_000.0) for i in range(dimension)]


def _exito(request: httpx.Request) -> httpx.Response:
    """Respuesta 200 de `batchEmbedContents` construida DESDE la peticion real.

    Debe reflejar el payload enviado: si el cliente mando 3 textos, Gemini
    devuelve 3 embeddings; si mando 250, devuelve 250.
    """
    payload = json.loads(request.content)
    n = len(payload.get("requests", []))
    return httpx.Response(200, json={"embeddings": [{"values": vector_n()} for _ in range(n)]})


def _cliente_api(
    max_reintentos: int = 5,
    batch_size: int = 100,
    backoff_base_s: float = 0.001,
    backoff_max_s: float = 0.05,
) -> ClienteEmbeddingsGemini:
    return ClienteEmbeddingsGemini(
        api_key="clave_de_prueba",
        modelo="gemini-embedding-001",
        dimension=DIMENSION,
        batch_size=batch_size,
        max_reintentos=max_reintentos,
        backoff_base_s=backoff_base_s,
        backoff_max_s=backoff_max_s,
        timeout_s=10.0,
    )


class _Transporte:
    """Transporte simulado con respuesta/s en cola, de modo que cada llamada
    HTTP devuelve el siguiente elemento de ``respuestas`` (encerrando)."""

    def __init__(self, respuestas: list[httpx.Response]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.respuestas:
            raise AssertionError("se hicieron mas llamadas HTTP de las previstas")
        self.llamadas.append(request)
        siguiente = self.respuestas.pop(0)
        if callable(siguiente) and not isinstance(siguiente, httpx.Response):
            return siguiente(request)
        return siguiente


@pytest.fixture
def sans_dormir(monkeypatch):
    """Sustituye asyncio.sleep por un registro instantaneo (sin esperar)."""
    dormidos: list[float] = []

    async def _dormir(segundos):  # noqa: ARG001
        dormidos.append(float(segundos))

    import app.services.embeddings as modulo

    monkeypatch.setattr(modulo.asyncio, "sleep", _dormir)
    return dormidos


async def _generar(transporte, textos, cliente):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transporte), timeout=5
    ) as http:
        return await cliente.generar(textos, cliente=http)


# --- forma del payload por lotes ---------------------------------------------


async def test_payload_usa_modelo_y_dimension_correctos():
    transporte = _Transporte([_exito])
    cliente = _cliente_api()
    textos = ["uno", "dos", "tres"]

    await _generar(transporte, textos, cliente)

    assert len(transporte.llamadas) == 1, "varios textos == UNA llamada (lote)"
    payload = json.loads(transporte.llamadas[0].content)
    peticiones = payload["requests"]
    assert len(peticiones) == len(textos)
    for peticion in peticiones:
        # El endpoint exige el prefijo `models/`.
        assert peticion["model"] == "models/gemini-embedding-001"
        assert peticion["outputDimensionality"] == 768
        assert peticion["content"]["parts"][0]["text"] in textos


async def test_se_respeta_el_tamano_de_lote():
    textos = [f"texto {i}" for i in range(250)]
    transporte = _Transporte(
        [_exito for _ in range(3)]
    )
    cliente = _cliente_api(batch_size=100)

    vectores = await _generar(transporte, textos, cliente)

    assert len(transporte.llamadas) == 3, "250 textos / lotes de 100 = 3 llamadas"
    calculados = [json.loads(req.content)["requests"] for req in transporte.llamadas]
    assert [len(l) for l in calculados] == [100, 100, 50]
    assert len(vectores) == 250


# --- 429 / RESOURCE_EXHAUSTED ------------------------------------------------


async def test_429_reintenta_y_exitoso(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(429, text="RESOURCE_EXHAUSTED"),
            _exito,
        ]
    )
    cliente = _cliente_api(max_reintentos=3)

    vectores = await _generar(transporte, ["hola"], cliente)

    assert len(transporte.llamadas) == 2, "429 -> reintento -> 200"
    assert sans_dormir, "debe haber esperado con backoff"
    assert len(vectores) == 1
    assert len(vectores[0]) == 768


async def test_429_usar_backoff_progresivo(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(429, text="RESOURCE_EXHAUSTED"),
            httpx.Response(429, text="RESOURCE_EXHAUSTED"),
            httpx.Response(429, text="RESOURCE_EXHAUSTED"),
            _exito,
        ]
    )
    cliente = _cliente_api(max_reintentos=5, backoff_base_s=1.0, backoff_max_s=30.0)

    await _generar(transporte, ["hola"], cliente)

    # base=1 -> 1s, 2s, 4s (más el jitter): creciente y acotado.
    assert len(sans_dormir) == 3
    assert sans_dormir[0] < sans_dormir[1] < sans_dormir[2]
    assert all(d <= 30.0 for d in sans_dormir)


async def test_429_respeta_retry_after(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(429, headers={"retry-after": "2"}, text="too many"),
            _exito,
        ]
    )
    cliente = _cliente_api(max_reintentos=3, backoff_max_s=60.0)

    await _generar(transporte, ["hola"], cliente)

    assert sans_dormir == [2.0], "debe respetar Retry-After pese a cerrar tras 1 intento"


async def test_429_agotado_lanza_error_finito(sans_dormir):
    transporte = _Transporte(
        [httpx.Response(429, text="RESOURCE_EXHAUSTED") for _ in range(10)]
    )
    cliente = _cliente_api(max_reintentos=3)

    with pytest.raises(ErrorEmbeddings, match="3 intentos"):
        await _generar(transporte, ["hola"], cliente)

    # Solo 3 intentos: sin bucles infinitos, independientemente de las
    # respuestas que queden en la cola.
    assert len(transporte.llamadas) == 3


async def test_resource_exhausted_en_cuerpo_sin_codigo_429(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(200, json={"error": {"code": 429, "message": "RESOURCE_EXHAUSTED"}}),
            _exito,
        ]
    )
    cliente = _cliente_api(max_reintentos=3)

    vectores = await _generar(transporte, ["hola"], cliente)
    assert len(transporte.llamadas) == 2
    assert len(vectores) == 1


# --- errores definitivos -----------------------------------------------------


async def test_error_http_definitivo_no_reintenta(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(400, text="no es retriable"),
            _exito,
        ]
    )
    cliente = _cliente_api(max_reintentos=5)

    with pytest.raises(ErrorEmbeddings, match="400"):
        await _generar(transporte, ["hola"], cliente)
    assert len(transporte.llamadas) == 1
    assert sans_dormir == [], "no debe haber esperado: no es transitorio"


async def test_dimension_incorrecta_se_detiene():
    cliente = _cliente_api()

    async def _mal(request):  # noqa: ARG001
        return httpx.Response(
            200, json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mal), timeout=5) as http:
        with pytest.raises(ErrorEmbeddings, match="dimension"):
            await cliente.generar(["hola"], cliente=http)


async def test_devuelve_menos_embeddings_que_textos_se_detiene():
    cliente = _cliente_api()

    async def _mal(request):  # noqa: ARG001
        return httpx.Response(
            200, json={"embeddings": [{"values": vector_n()}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mal), timeout=5) as http:
        with pytest.raises(ErrorEmbeddings, match="textos"):
            await cliente.generar(["a", "b"], cliente=http)


async def test_sin_clave_lanza_sin_hacer_red():
    cliente = ClienteEmbeddingsGemini(api_key=None)

    async def _no_llamar(request):  # noqa: ARG001
        raise AssertionError("no debe llamarse a la red sin clave")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_llamar), timeout=5) as http:
        with pytest.raises(ErrorEmbeddings, match="GEMINI_API_KEY"):
            await cliente.generar(["hola"], cliente=http)


async def test_lista_vacia_devuelve_vacio():
    cliente = _cliente_api()
    assert await cliente.generar([], cliente=httpx.AsyncClient()) == []