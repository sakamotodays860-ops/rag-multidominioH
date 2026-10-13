"""Pruebas de la interfaz web de chat (GET /chat).

Son pruebas 100% aisladas: usan el ``TestClient`` de FastAPI contra la app en
memoria. No levantan el servidor, no tocan PostgreSQL y NO llaman a Gemini.

Comprueban:
  1. ``GET /chat`` responde HTTP 200 y sirve HTML con el titulo esperado;
  2. los endpoints de las fases anteriores siguen registrados;
  3. el archivo estatico existe en la ruta resuelta por la aplicacion.
"""

from fastapi.testclient import TestClient

from app.main import STATIC_DIR, app

cliente = TestClient(app)


def test_chat_responde_200_y_es_html() -> None:
    respuesta = cliente.get("/chat")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    assert "Chat RAG Multi-Dominio" in respuesta.text


def test_chat_referencia_endpoints_esperados() -> None:
    """La interfaz debe apuntar a los endpoints reales del backend."""
    html = cliente.get("/chat").text
    assert "/api/colecciones" in html
    assert "/api/consultar" in html
    assert "/api/consultar-auto" in html


def test_chat_tiene_selector_de_dominio() -> None:
    """El chat debe permitir deteccion automatica o forzar el dominio."""
    html = cliente.get("/chat").text
    assert "Detecci" in html  # "Deteccion automatica"
    assert "selector-dominio" in html
    assert "financiero" in html and "tributario" in html


def test_endpoints_de_fases_anteriores_registrados() -> None:
    rutas = {ruta.path for ruta in app.routes}
    for esperado in (
        "/",
        "/health",
        "/chat",
        "/api/colecciones",
        "/api/colecciones/{coleccion_id}",
        "/api/ingestar-web",
        "/api/consultar",
        "/api/consultar-auto",
    ):
        assert esperado in rutas, f"falta la ruta {esperado}"


def test_archivo_estatico_existe() -> None:
    assert (STATIC_DIR / "chat.html").is_file()


def test_chat_maneja_error_de_proveedor_no_disponible() -> None:
    """La interfaz debe informar de un 503 (proveedor de IA no disponible)."""
    html = cliente.get("/chat").text
    assert "HTTP 503" in html
    assert "proveedor de IA" in html


def test_chat_muestra_metadatos_de_busqueda() -> None:
    """La interfaz debe presentar el tipo de busqueda y los metadatos derivados."""
    html = cliente.get("/chat").text
    for esperado in (
        "tipo_busqueda",
        "puntuacion_lexica",
        "coincidencia_exacta",
        "pagina detectada",
        "referencia detectada",
        "relevancia lexical",
    ):
        assert esperado in html, f"falta el metadato {esperado}"


def test_chat_renderiza_sin_html_inseguro() -> None:
    """El contenido de las evidencias se pinta con textContent, nunca como HTML."""
    html = cliente.get("/chat").text
    assert "contenido.textContent" in html
    # innerHTML solo se usa para vaciar el selector de colecciones.
    assert "origen.innerHTML" not in html
    assert "contenido.innerHTML" not in html


def test_chat_admite_recuperacion_sin_generacion() -> None:
    """La interfaz debe poder pedir solo-recuperacion si el proveedor falla."""
    html = cliente.get("/chat").text
    assert "solo_recuperaci" in html
    assert "conGenerar(cuerpoPeticion, false)" in html
    assert "solo_recuperacion" in html

