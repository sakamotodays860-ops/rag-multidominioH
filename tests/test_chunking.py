"""Pruebas del chunking (FASE 3, requisito 4).

Cubren la validacion de parametros y las invariantes del troceado:

* ningun fragmento supera `chunk_size`;
* el solape efectivo es exacto sobre texto sin espacios;
* con solape > 0 no se pierde ninguna palabra completa del texto;
* el troceado siempre termina, incluso con overlap 0 (ventana sin solape).
"""

import pytest

from app.utils.chunking import (
    ConfiguracionChunkingError,
    dividir_en_fragmentos,
    validar_configuracion,
)

PALABRAS = [f"palabra{str(i) * 4}" for i in range(60)]
TEXTO = " ".join(PALABRAS)


def _palabras_de(fragmentos: list[str]) -> set[str]:
    return set(" ".join(fragmentos).split())


@pytest.mark.parametrize(
    "chunk_size, chunk_overlap",
    [
        (100, 25),
        (50, 0),
        (200, 40),
        (120, 30),
        (500, 100),
        (64, 10),
    ],
)
def test_ningun_fragmento_supera_chunk_size(chunk_size, chunk_overlap):
    fragmentos = dividir_en_fragmentos(TEXTO, chunk_size, chunk_overlap)
    assert fragmentos
    for fragmento in fragmentos:
        assert 0 < len(fragmento) <= chunk_size


@pytest.mark.parametrize("chunk_size, overlap", [(64, 10), (100, 25), (250, 25)])
def test_solape_exacto_sobre_texto_sin_espacios(chunk_size, overlap):
    texto = "0123456789" * 200
    fragmentos = dividir_en_fragmentos(texto, chunk_size, overlap)
    for anterior, siguiente in zip(fragmentos, fragmentos[1:]):
        # Sin recorte de palabra (no hay espacios), el solape es exacto.
        assert anterior[-overlap:] == siguiente[:overlap]


@pytest.mark.parametrize("chunk_size, overlap", [(100, 25), (200, 40), (120, 30)])
def test_con_solape_no_se_pierde_ninguna_palabra(chunk_size, overlap):
    fragmentos = dividir_en_fragmentos(TEXTO, chunk_size, overlap)
    presentes = _palabras_de(fragmentos)
    # La ventana puede empezar en medio de una palabra (tokens parciales
    # como "bra6666"); lo importante es que la palabra COMPLETA nunca se
    # pierde, por eso se compara con subconjunto y no con igualdad.
    assert set(PALABRAS) <= presentes


def test_con_overlap_cero_toda_letra_sigue_cubierta():
    # Con overlap 0 los cortes caen en medio de palabras, pero CADA caracter
    # del texto original debe seguir presente en algun fragmento.
    fragmentos = dividir_en_fragmentos(TEXTO, 50, 0)
    letras_texto = "".join(ch for ch in TEXTO if ch != " ")
    letras_fragmentos = "".join(ch for ch in "".join(fragmentos) if ch != " ")
    assert len(letras_fragmentos) >= len(letras_texto)
    assert letras_texto in "".join(fragmentos).replace(" ", "")  # cobertura


def test_texto_vacio_da_lista_vacia():
    assert dividir_en_fragmentos("", 100, 10) == []
    assert dividir_en_fragmentos("   \n\t ", 100, 10) == []


def test_texto_menor_que_chunk_da_un_solo_fragmento():
    assert dividir_en_fragmentos("hola mundo", 1000, 100) == ["hola mundo"]


def test_siempre_termina_con_overlap_cero():
    texto = "x" * 500
    fragmentos = dividir_en_fragmentos(texto, 100, 0)
    assert len(fragmentos) == 5


# --- Validacion de parametros (requisito C) ----------------------------------


@pytest.mark.parametrize(
    "chunk_size, chunk_overlap",
    [
        (0, 0),
        (1, -1),
        (10, 10),
        (5, 6),
        (100, 1000),
        (-5, 0),
    ],
)
def test_configuracion_invalida_lanza_error(chunk_size, chunk_overlap):
    with pytest.raises(ConfiguracionChunkingError):
        validar_configuracion(chunk_size, chunk_overlap)
    with pytest.raises(ConfiguracionChunkingError):
        dividir_en_fragmentos("abc", chunk_size, chunk_overlap)


def test_configuracion_valida_acepta():
    validar_configuracion(1, 0)
    validar_configuracion(1000, 150)
    validar_configuracion(100, 99)


def test_los_mensajes_de_error_son_claros():
    with pytest.raises(ConfiguracionChunkingError, match="chunk_size"):
        validar_configuracion(0, 0)
    with pytest.raises(ConfiguracionChunkingError, match="chunk_overlap"):
        validar_configuracion(10, 10)