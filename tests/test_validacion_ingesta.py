"""Pruebas de validacion de la peticion de ingesta (FASE 3, requisito 1).

Cubren el modelo `PeticionIngesta` (validacion de esquema Pydantic) y el
validador de URL del downloader (requisito 2: URL invalida registrada como
error, sin abortar).
"""

import pytest
from pydantic import ValidationError

from app.models.ingesta import PeticionIngesta, validar_col_peticion
from app.services.downloader import es_url_valida


def _peticion(**cambios):
    base = {
        "coleccion_id": "prueba_fase_3",
        "urls": ["https://example.com/uno"],
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "concurrency_workers": 4,
    }
    base.update(cambios)
    return base


def test_peticion_minima_con_defaults():
    peticion = PeticionIngesta(
        coleccion_id="bolivia_financiera",
        urls=["https://example.com/x"],
    )
    assert peticion.chunk_size > 0
    assert peticion.chunk_overlap >= 0
    assert peticion.chunk_overlap < peticion.chunk_size
    assert 1 <= peticion.concurrency_workers <= 64


@pytest.mark.parametrize(
    "coleccion_id",
    ["", "   ", "a" * 51, "nombre con espacios", "nombre#raro", "@rroba"],
)
def test_coleccion_id_invalido_rechazado(coleccion_id):
    # Solo en blanco o demasiado largo -> rechazo de esquema (422).
    # Formato inapropiado -> rechazo de dominio (400) via validar().
    datos = _peticion(coleccion_id=coleccion_id)
    if coleccion_id == "" or len(coleccion_id) > 50:
        with pytest.raises(ValidationError):
            PeticionIngesta(**datos)
        return
    peticion = PeticionIngesta(**datos)
    with pytest.raises(ValueError):
        peticion.validar()
    with pytest.raises(ValueError):
        validar_col_peticion(coleccion_id)


@pytest.mark.parametrize("urls", [[], ["https://a.com", "https://b.com"]])
def test_lista_de_urls_vacia_rechazada(urls):
    if urls == []:
        with pytest.raises(ValidationError):
            PeticionIngesta(**(_peticion(urls=urls)))
    else:
        peticion = PeticionIngesta(**(_peticion(urls=urls)))
        peticion.validar()


@pytest.mark.parametrize(
    "chunk_size, chunk_overlap",
    [
        (0, 0),
        (1, -1),
        (100, 100),
        (10, 20),
        (1000, 1001),
    ],
)
def test_configuracion_de_chunking_invalida_rechazada(chunk_size, chunk_overlap):
    datos = _peticion(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if chunk_size <= 0 or chunk_overlap < 0:
        # Pydantic Field(re>=) lo rechaza en el esquema antes del dominio.
        with pytest.raises(ValidationError):
            PeticionIngesta(**datos)
        return
    peticion = PeticionIngesta(**datos)
    with pytest.raises(ValueError, match="chunk_overlap"):
        peticion.validar()


def test_concurrencia_fuera_de_rango_rechazada():
    for valor in (0, -3, 65, 1000):
        with pytest.raises(ValidationError):
            PeticionIngesta(**(_peticion(concurrency_workers=valor)))


def test_peticion_valida_acepta():
    peticion = PeticionIngesta(**_peticion())
    peticion.validar()
    assert peticion.concurrency_workers == 4


# --- Validador de URL (requisito A: URLs invalidas como error individual) ----


@pytest.mark.parametrize(
    "url, valida",
    [
        ("https://example.com", True),
        ("http://ejemplo.com/pagina", True),
        ("https://a.b.c/path?q=1#seg", True),
        ("  https://example.com  ", True),  # se toleran espacios externos
        ("example.com", False),
        ("ftp://example.com/x", False),
        ("file:///etc/passwd", False),
        ("data:text/html,x", False),
        ("javascript:alert(1)", False),
        ("https://", False),
        ("", False),
        ("https://exa mple.com", False),
        (12345, False),
        ("x" * 5000, False),
    ],
)
def test_validador_de_url(url, valida):
    assert es_url_valida(url) is valida


def test_url_con_exceso_de_espacios_interna():
    # Los espacios INTERNOS se rechazan; los externos se recortan.
    assert not es_url_valida("https://ejemplo.com/v1 a")
    assert es_url_valida(" https://ejemplo.com ") is True