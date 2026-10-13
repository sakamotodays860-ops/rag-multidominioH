"""Pruebas HTTP de ``POST /api/consultar-auto`` (fase H).

Aísla el enrutado de errores: se parchea el servicio para provocar cada caso.
No hay red, ni Gemini, ni base de datos.
"""

from fastapi.testclient import TestClient

from app.api.v1 import chat as api_chat
from app.main import app
from app.models.consulta import RespuestaConsultaAuto
from app.services.embeddings import ErrorEmbeddings
from app.services.generacion import ErrorProveedorNoDisponible

cliente = TestClient(app)


def test_pregunta_vacia_devuelve_422():
    respuesta = cliente.post("/api/consultar-auto", json={"pregunta": "   "})
    assert respuesta.status_code == 422


def test_dominio_invalido_devuelve_422():
    respuesta = cliente.post(
        "/api/consultar-auto", json={"pregunta": "hola", "dominio": "otro"}
    )
    assert respuesta.status_code == 422


def test_value_error_devuelve_400(monkeypatch):
    async def falso(_peticion, **_kwargs):
        raise ValueError("dominio no soportado")

    monkeypatch.setattr(api_chat, "ejecutar_consulta_auto", falso)
    respuesta = cliente.post("/api/consultar-auto", json={"pregunta": "hola"})
    assert respuesta.status_code == 400
    assert "no valida" in respuesta.json()["detail"]


def test_error_de_proveedor_devuelve_502(monkeypatch):
    async def falso(_peticion, **_kwargs):
        raise ErrorEmbeddings("cuota agotada")

    monkeypatch.setattr(api_chat, "ejecutar_consulta_auto", falso)
    respuesta = cliente.post("/api/consultar-auto", json={"pregunta": "hola"})
    assert respuesta.status_code == 502


def test_proveedor_no_disponible_devuelve_503(monkeypatch):
    """Un 5xx persistente de Gemini se reporta como 503 (proveedor caido)."""

    async def falso(_peticion, **_kwargs):
        raise ErrorProveedorNoDisponible(
            "El proveedor de generacion (Gemini) no esta disponible "
            "temporalmente (HTTP 503) tras 5 intentos."
        )

    monkeypatch.setattr(api_chat, "ejecutar_consulta_auto", falso)
    respuesta = cliente.post("/api/consultar-auto", json={"pregunta": "hola"})
    assert respuesta.status_code == 503
    assert "no disponible" in respuesta.json()["detail"]


def test_endpoint_reenvia_recuperacion_y_generar(monkeypatch):
    """El endpoint activa la busqueda hibrida desde config y respeta generar."""
    capturado: dict = {}

    async def falso(peticion, **kwargs):
        capturado["peticion"] = peticion
        capturado["kwargs"] = kwargs
        return RespuestaConsultaAuto(
            pregunta=peticion.pregunta,
            dominio="financiero",
            coleccion_id="financiero_bolivia_2026",
            respuesta="(solo recuperacion)",
            evidencias=[],
            total_evidencias=0,
            solo_recuperacion=True,
        )

    monkeypatch.setattr(api_chat, "ejecutar_consulta_auto", falso)
    respuesta = cliente.post(
        "/api/consultar-auto",
        json={"pregunta": "Que establece el Reglamento de Posicion de Cambios?", "generar": False},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["solo_recuperacion"] is True
    assert capturado["peticion"].generar is False
    assert capturado["kwargs"]["usar_lexico"] is True
    assert capturado["kwargs"]["permitir_lexico_sin_embedding"] is True

