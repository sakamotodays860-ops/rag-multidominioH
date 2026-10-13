"""Pruebas de la extraccion de metadatos textuales (FASE 6).

Son pruebas puras: no tocan red ni base de datos. Comprueban que los metadatos
derivados del texto (pagina, referencia de articulo y frase exacta) se extraen
SOLO cuando el patron aparece realmente, sin inventar valores.
"""

from app.utils.referencias import (
    extraer_frase_exacta,
    extraer_pagina,
    extraer_referencia,
)


def test_extraer_pagina_con_marcador():
    assert extraer_pagina("[página 3]\nDIRECTORIO") == 3
    assert extraer_pagina("texto\n[PAGINA 12]\nmas") == 12
    assert extraer_pagina("[ pagina  7 ]") == 7


def test_extraer_pagina_sin_marcador_devuelve_none():
    assert extraer_pagina("sin marcador de pagina") is None
    assert extraer_pagina(None) is None
    assert extraer_pagina("") is None


def test_extraer_referencia_encabezado():
    assert extraer_referencia("Artículo 2. (Términos y Abreviaturas)\n...") == (
        "Artículo 2"
    )
    assert extraer_referencia("texto\narticulo 5.- contenido") == "Artículo 5"
    assert extraer_referencia("ARTÍCULO 10 (Vigencia)") == "Artículo 10"


def test_extraer_referencia_no_confunde_mencion_incidental():
    # No abre linea: es una mencion dentro de la oracion, no un encabezado.
    assert extraer_referencia("según lo dispuesto en el artículo 3 del reglamento") is None
    assert extraer_referencia("sin articulos") is None
    assert extraer_referencia(None) is None


def test_extraer_frase_exacta_prioriza_resolucion():
    assert extraer_frase_exacta("R.D. N° 138/2026 del BCB") == "138/2026"
    assert extraer_frase_exacta("resolucion 138 / 2026") == "138/2026"


def test_extraer_frase_exacta_articulo():
    assert extraer_frase_exacta("Que dice el artículo 1?") == "artículo 1"
    assert extraer_frase_exacta("ARTICULO 22") == "ARTICULO 22"


def test_extraer_frase_exacta_vacia_sin_patron():
    assert extraer_frase_exacta("una pregunta general") == ""
    assert extraer_frase_exacta(None) == ""
