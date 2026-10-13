"""Pruebas del cliente de generacion Gemini (chat / FASE 4).

TODAS usan ``httpx.MockTransport``: nunca hay una llamada real a Gemini.
Se verifican las reglas de gestion de errores:

* HTTP 429 limite de cuota: no se reintenta indefinidamente (bounded).
* HTTP 503 persistente: se reintenta con backoff y, al agotar los intentos,
  se eleva :class:`ErrorProveedorNoDisponible` (la capa HTTP lo traduce a 503).
* HTTP 503 transitorio: se recupera con un solo reintento.
* HTTP 400: error definitivo, sin reintentos.
* Cuota diaria agotada: no se reintenta.
"""

import json

import httpx
import pytest

from app.services.generacion import (
    ClienteGeneracionGemini,
    ErrorGeneracion,
    ErrorProveedorNoDisponible,
)


def _exito(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": "respuesta"}]}}]},
    )


def _cliente(
    max_reintentos: int = 3,
    modelo: str = "gemini-3.8-flash",
    modelo_respaldo: str | None = None,
) -> ClienteGeneracionGemini:
    return ClienteGeneracionGemini(
        api_key="clave_de_prueba",
        modelo=modelo,
        temperatura=0.0,
        max_reintentos=max_reintentos,
        backoff_base_s=0.001,
        backoff_max_s=0.05,
        timeout_s=10.0,
        modelo_respaldo=modelo_respaldo,
    )


class _Transporte:
    """Transporte simulado: cada llamada consume la siguiente respuesta."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, request):
        if not self.respuestas:
            raise AssertionError("se hicieron mas llamadas HTTP de las previstas")
        self.llamadas.append(request)
        siguiente = self.respuestas.pop(0)
        if callable(siguiente) and not isinstance(siguiente, httpx.Response):
            return siguiente(request)
        return siguiente


@pytest.fixture
def sans_dormir(monkeypatch):
    dormidos = []

    async def _dormir(segundos):
        dormidos.append(float(segundos))

    import app.services.generacion as modulo

    monkeypatch.setattr(modulo.asyncio, "sleep", _dormir)
    return dormidos


async def _generar(transporte, cliente):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transporte), timeout=5
    ) as http:
        return await cliente.generar("prompt", cliente=http)


async def test_payload_usa_temperatura_0_y_modelo():
    transporte = _Transporte([_exito])
    await _generar(transporte, _cliente())
    payload = json.loads(transporte.llamadas[0].content)
    assert payload["generationConfig"]["temperature"] == 0.0
    assert "gemini-3.8-flash" in str(transporte.llamadas[0].url)


async def test_503_transitorio_se_recupera(sans_dormir):
    transporte = _Transporte([httpx.Response(503, text="unavailable"), _exito])
    texto = await _generar(transporte, _cliente(max_reintentos=3))
    assert texto == "respuesta"
    assert len(transporte.llamadas) == 2
    assert len(sans_dormir) == 1, "debe esperar con backoff antes de reintentar"


async def test_503_persistente_lanza_proveedor_no_disponible(sans_dormir):
    transporte = _Transporte(
        [httpx.Response(503, text="unavailable") for _ in range(10)]
    )
    with pytest.raises(ErrorProveedorNoDisponible, match="503"):
        await _generar(transporte, _cliente(max_reintentos=3))
    assert len(transporte.llamadas) == 3, "reintentos finitos"
    assert len(sans_dormir) == 2


async def test_429_no_reintenta_indefinidamente(sans_dormir):
    """Un 429 sostenido se detiene tras los reintentos finitos configurados."""
    transporte = _Transporte(
        [httpx.Response(429, text="RESOURCE_EXHAUSTED") for _ in range(10)]
    )
    with pytest.raises(ErrorGeneracion):
        await _generar(transporte, _cliente(max_reintentos=3))
    assert len(transporte.llamadas) == 3, "sin bucle infinito"


async def test_400_es_definitivo_sin_reintentos(sans_dormir):
    transporte = _Transporte([httpx.Response(400, text="bad request"), _exito])
    with pytest.raises(ErrorGeneracion, match="400"):
        await _generar(transporte, _cliente(max_reintentos=5))
    assert len(transporte.llamadas) == 1
    assert sans_dormir == []


async def test_cuota_diaria_no_reintenta(sans_dormir):
    transporte = _Transporte(
        [
            httpx.Response(
                429,
                text="Quota exceeded ... requests per day per project",
            ),
            _exito,
        ]
    )
    with pytest.raises(ErrorGeneracion, match="cuota diaria"):
        await _generar(transporte, _cliente(max_reintentos=5))
    assert len(transporte.llamadas) == 1
    assert sans_dormir == []


async def test_sin_clave_no_hace_red():
    cliente = ClienteGeneracionGemini(api_key=None)

    async def _no_llamar(request):
        raise AssertionError("no debe llamarse a la red sin clave")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_no_llamar), timeout=5
    ) as http:
        with pytest.raises(ErrorGeneracion, match="GEMINI_API_KEY"):
            await cliente.generar("prompt", cliente=http)


# --- modelo de respaldo (remedio oficial ante 503) --------------------------


async def test_fallback_deshabilitado_repropaga_el_503(sans_dormir):
    """Sin respaldo configurado, un 503 persistente sigue siendo un error."""
    transporte = _Transporte(
        [httpx.Response(503, text="high demand") for _ in range(10)]
    )
    with pytest.raises(ErrorProveedorNoDisponible):
        await _generar(transporte, _cliente(max_reintentos=2))
    assert len(transporte.llamadas) == 2
    assert all(
        "gemini-3.8-flash" in str(call.url) for call in transporte.llamadas
    )


async def test_fallback_habilitado_se_recupera(sans_dormir):
    """Con respaldo, tras agotar el principal se intenta UNA vez el alternativo."""
    transporte = _Transporte(
        [
            httpx.Response(503, text="unavailable"),  # principal intento 1
            httpx.Response(503, text="unavailable"),  # principal intento 2
            _exito,  # respaldo intento 1
        ]
    )
    texto = await _generar(
        transporte,
        _cliente(max_reintentos=2, modelo_respaldo="gemini-3.6-flash"),
    )
    assert texto == "respuesta"
    assert len(transporte.llamadas) == 3
    assert "gemini-3.8-flash" in str(transporte.llamadas[0].url)
    assert "gemini-3.8-flash" in str(transporte.llamadas[1].url)
    assert "gemini-3.6-flash" in str(transporte.llamadas[2].url), (
        "el tercer intento debe usar el modelo de respaldo"
    )


async def test_fallback_igual_al_principal_no_duplica(sans_dormir):
    """Si el respaldo coincide con el principal, no se reintenta de nuevo."""
    transporte = _Transporte(
        [httpx.Response(503, text="unavailable") for _ in range(10)]
    )
    with pytest.raises(ErrorProveedorNoDisponible):
        await _generar(
            transporte,
            _cliente(max_reintentos=2, modelo_respaldo="gemini-3.8-flash"),
        )
    assert len(transporte.llamadas) == 2


async def test_fallback_tambien_caido_repropaga(sans_dormir):
    """Si el respaldo tambien da 5xx, el error final sigue siendo claro."""
    transporte = _Transporte(
        [httpx.Response(503, text="unavailable") for _ in range(10)]
    )
    with pytest.raises(ErrorProveedorNoDisponible):
        await _generar(
            transporte,
            _cliente(max_reintentos=2, modelo_respaldo="gemini-3.6-flash"),
        )
    # 2 intentos del principal + 2 del respaldo.
    assert len(transporte.llamadas) == 4
    assert "gemini-3.6-flash" in str(transporte.llamadas[-1].url)


async def test_503_registra_el_cuerpo_del_proveedor(sans_dormir, caplog):
    """El cuerpo real de Gemini debe quedar en el log para poder diagnosticar."""
    cuerpo = "This model is currently experiencing high demand."
    transporte = _Transporte([httpx.Response(503, text=cuerpo) for _ in range(10)])
    with caplog.at_level("WARNING", logger="app.services.generacion"):
        with pytest.raises(ErrorProveedorNoDisponible):
            await _generar(transporte, _cliente(max_reintentos=1))
    registros = [r.getMessage() for r in caplog.records]
    assert any("high demand" in mensaje for mensaje in registros)
    assert any("503" in mensaje for mensaje in registros)
