"""Pruebas de la previsualizacion de ingesta (FASE 4).

Sin red, sin Gemini y sin base de datos: se sustituye la descarga por un
resultado falso y se comprueba que la previsualizacion:

* cuenta documentos y fragmentos sin generar embeddings ni insertar filas;
* aplica el gate ``min_caracteres`` como error individual;
* reporta los documentos duplicados.
"""

from app.models.ingesta import PeticionIngesta
from app.services import ingesta as ingesta_mod
from app.services.downloader import ResultadoDescarga, _deduplicar


def _resultado(pares: list[tuple[str, str]]) -> ResultadoDescarga:
    resultado = ResultadoDescarga()
    for url, texto in pares:
        resultado.registrar_ok(url, texto, mime="text/html")
    _deduplicar(resultado)
    return resultado


def _fake_descarga(resultado: ResultadoDescarga):
    async def falso(urls, **kwargs):  # noqa: ANN001, ANN003
        return resultado

    return falso


def _prohibir_embeddings(monkeypatch) -> None:
    class _NoLlamar:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            raise AssertionError("la previsualizacion no debe llamar a Gemini")

    monkeypatch.setattr(ingesta_mod, "ClienteEmbeddingsGemini", _NoLlamar)


async def test_previsualizar_cuenta_fragmentos_sin_gemini(monkeypatch):
    texto = "Articulo 1. La presente Ley regula el sistema financiero. " * 40
    monkeypatch.setattr(
        ingesta_mod, "descargar_y_limpiar", _fake_descarga(_resultado([("https://x.org/ley", texto)]))
    )
    _prohibir_embeddings(monkeypatch)

    peticion = PeticionIngesta(coleccion_id="prueba_fase4", urls=["https://x.org/ley"])
    respuesta = await ingesta_mod.previsualizar_ingesta(peticion)

    assert respuesta.urls_procesadas == 1
    assert respuesta.fragmentos_generados > 0
    assert 1 <= len(respuesta.muestra_fragmentos) <= 5
    assert respuesta.errores == []


async def test_previsualizar_aplica_gate_min_caracteres(monkeypatch):
    resultado = _resultado(
        [("https://x.org/corto", "hola"), ("https://x.org/largo", "contenido " * 100)]
    )
    monkeypatch.setattr(ingesta_mod, "descargar_y_limpiar", _fake_descarga(resultado))
    _prohibir_embeddings(monkeypatch)

    peticion = PeticionIngesta(
        coleccion_id="prueba_fase4",
        urls=["https://x.org/corto", "https://x.org/largo"],
        min_caracteres=100,
    )
    respuesta = await ingesta_mod.previsualizar_ingesta(peticion)

    assert respuesta.urls_procesadas == 1
    assert any("contenido insuficiente" in error.motivo for error in respuesta.errores)


async def test_previsualizar_reporta_duplicados(monkeypatch):
    texto = "Texto normativo identico publicado en dos rutas."
    resultado = _resultado([("https://x.org/a", texto), ("https://x.org/b", texto)])
    monkeypatch.setattr(ingesta_mod, "descargar_y_limpiar", _fake_descarga(resultado))
    _prohibir_embeddings(monkeypatch)

    peticion = PeticionIngesta(coleccion_id="prueba_fase4", urls=["https://x.org/a", "https://x.org/b"])
    respuesta = await ingesta_mod.previsualizar_ingesta(peticion)

    assert respuesta.duplicados == ["https://x.org/b"]
    assert respuesta.urls_procesadas == 1
