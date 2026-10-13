"""Pruebas HTTP de la API de la FASE 2.

Usan `urllib` de la biblioteca estandar: NO se instala httpx ni aiohttp, que
quedan reservados para la fase de scraping.

Comprueban dos cosas:
  1. la FASE 1 sigue intacta (`GET /` y `GET /health` responden igual);
  2. la API valida `coleccion_id` y refleja el aislamiento entre colecciones.

Son pruebas de solo lectura: no insertan ni modifican registros.
"""

import json
import urllib.error
import urllib.request

import pytest

from helpers import (
    COLECCION_FINANCIERO,
    COLECCION_TRIBUTARIO,
    FRAGMENTOS_ESPERADOS,
)


@pytest.fixture(scope="session")
def api(api_disponible):
    """Cliente minimo sobre urllib contra el backend en marcha."""
    return api_disponible


def obtener(base: str, ruta: str) -> tuple[int, dict]:
    """Devuelve ``(status_code, cuerpo_json)`` de una peticion GET."""
    url = f"{base}{ruta}"
    try:
        with urllib.request.urlopen(url, timeout=10) as respuesta:
            return respuesta.status, json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        cuerpo = exc.read().decode("utf-8")
        return exc.code, json.loads(cuerpo) if cuerpo else {}


@pytest.fixture(scope="session")
def api_disponible(url_base):
    """Salta el bloque si el backend no esta levantado."""
    try:
        with urllib.request.urlopen(f"{url_base}/health", timeout=5):
            pass
    except Exception as exc:  # pragma: no cover - depende del entorno
        pytest.skip(f"backend no disponible en {url_base}: {exc}")
    return url_base


# --- FASE 1: no debe romperse -------------------------------------------------


def test_fase1_raiz_intacta(api):
    codigo, cuerpo = obtener(api, "/")
    assert codigo == 200
    assert cuerpo == {
        "status": "online",
        "mensaje": "Pipeline RAG Multi-Dominio activo",
    }


def test_fase1_health_intacto(api):
    codigo, cuerpo = obtener(api, "/health")
    assert codigo == 200
    assert cuerpo == {"status": "online", "mensaje": "Backend activo"}


def test_endpoints_registrados_en_openapi(api):
    codigo, cuerpo = obtener(api, "/openapi.json")
    assert codigo == 200
    rutas = set(cuerpo["paths"])
    rutas_esperadas = {
        "/",
        "/health",
        "/api/colecciones",
        "/api/colecciones/{coleccion_id}",
        "/api/ingestar-web",  # FASE 3
        "/api/previsualizar-ingesta",  # FASE 4: preview sin Gemini
        "/api/consultar",  # FASE 4
        "/api/consultar-auto",  # FASE H: chat con deteccion de dominio
    }
    assert rutas == rutas_esperadas
    # La ingesta solo permite POST (FASE 3).
    assert "post" in cuerpo["paths"]["/api/ingestar-web"]
    assert "get" not in cuerpo["paths"]["/api/ingestar-web"]
    # La previsualizacion tambien es de solo POST (FASE 4).
    assert "post" in cuerpo["paths"]["/api/previsualizar-ingesta"]
    assert "get" not in cuerpo["paths"]["/api/previsualizar-ingesta"]
    # La consulta RAG tambien es de solo POST (FASE 4).
    assert "post" in cuerpo["paths"]["/api/consultar"]
    assert "get" not in cuerpo["paths"]["/api/consultar"]
    # El chat automatico tambien es de solo POST (FASE H).
    assert "post" in cuerpo["paths"]["/api/consultar-auto"]
    assert "get" not in cuerpo["paths"]["/api/consultar-auto"]


# --- FASE 2: diagnostico y validacion ----------------------------------------


def test_listado_de_colecciones(api):
    codigo, cuerpo = obtener(api, "/api/colecciones")
    assert codigo == 200

    resumen = {fila["coleccion_id"]: fila["fragmentos"] for fila in cuerpo}
    # Las colecciones de demostracion deben aparecer con al menos su tamano
    # semilla; desde la FASE 6 pueden contener ademas documentos reales, y
    # pueden coexistir otras colecciones (p. ej. `benchmark_2026`).
    assert resumen.get(COLECCION_FINANCIERO, 0) >= FRAGMENTOS_ESPERADOS
    assert resumen.get(COLECCION_TRIBUTARIO, 0) >= FRAGMENTOS_ESPERADOS


@pytest.mark.parametrize(
    "coleccion", [COLECCION_FINANCIERO, COLECCION_TRIBUTARIO]
)
def test_coleccion_valida_devuelve_su_tamano(api, coleccion):
    codigo, cuerpo = obtener(api, f"/api/colecciones/{coleccion}")
    assert codigo == 200
    assert cuerpo["coleccion_id"] == coleccion
    assert cuerpo["existe"] is True
    # El total puede crecer con documentos reales (FASE 6).
    assert cuerpo["fragmentos"] >= FRAGMENTOS_ESPERADOS


def test_coleccion_valida_inexistente(api):
    codigo, cuerpo = obtener(api, "/api/colecciones/coleccion_que_no_existe")
    assert codigo == 200
    assert cuerpo == {
        "coleccion_id": "coleccion_que_no_existe",
        "existe": False,
        "fragmentos": 0,
    }


@pytest.mark.parametrize(
    "ruta, motivo",
    [
        ("/api/colecciones/" + "a" * 51, "mas de 50 caracteres"),
        ("/api/colecciones/con%20espacio", "espacio"),
        ("/api/colecciones/coleccion%23mala", "caracter especial"),
        ("/api/colecciones/%20", "solo espacios"),
        ("/api/colecciones/correo%40dominio", "arroba"),
    ],
)
def test_api_rechaza_coleccion_id_invalido(api, ruta, motivo):
    codigo, cuerpo = obtener(api, ruta)
    assert codigo == 400, f"({motivo}) se esperaba 400 y llego {codigo}: {cuerpo}"
    assert "detail" in cuerpo
    assert cuerpo["detail"]


def test_no_existe_endpoint_de_get_consultar(api):
    """El endpoint RAG (`/api/consultar`) existe pero solo admite POST (FASE 4)."""
    url = f"{api}/api/consultar"
    try:
        with urllib.request.urlopen(url, timeout=10) as respuesta:
            codigo = respuesta.status
    except urllib.error.HTTPError as exc:
        codigo = exc.code
    except urllib.error.URLError as exc:  # pragma: no cover
        pytest.skip(f"no se pudo probar: {exc}")
    assert codigo == 405


def test_ingestar_web_solo_admite_post(api):
    """La ingesta solo responde a POST; GET no esta definida para esa ruta."""
    url = f"{api}/api/ingestar-web"
    try:
        with urllib.request.urlopen(url, timeout=10) as respuesta:
            codigo = respuesta.status
    except urllib.error.HTTPError as exc:
        codigo = exc.code
    except urllib.error.URLError as exc:  # pragma: no cover
        pytest.skip(f"no se pudo probar: {exc}")
    assert codigo == 405


def test_ingestar_web_rechaza_request_invalido_antes_de_llamar_a_red(api):
    """La validacion 400/422 ocurre sin contacto con la red remota.

    Un `coleccion_id` con formato rechazable o con un solape invalido debe
    fallar en la capa de validacion, nunca en Gemini.
    """
    import urllib.request

    casos = [
        ({"coleccion_id": "", "urls": ["https://a.com"]}, 422),
        ({"coleccion_id": "x", "urls": []}, 422),
        ({"coleccion_id": "x", "urls": ["https://a.com"], "chunk_size": -1}, 422),
        (
            {
                "coleccion_id": "fase_3_mal_formato",
                "urls": ["https://a.com"],
                "chunk_overlap": 100,
                "chunk_size": 100,
            },
            400,
        ),
        ({"coleccion_id": "nombre!malo", "urls": ["https://a.com"]}, 400),
    ]
    for cuerpo, esperado in casos:
        peticion = urllib.request.Request(
            f"{api}/api/ingestar-web",
            data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=10) as respuesta:
                codigo = respuesta.status
        except urllib.error.HTTPError as exc:
            codigo = exc.code
        assert codigo == esperado, (
            f"con {cuerpo} se esperaba {esperado} y llego {codigo}"
        )