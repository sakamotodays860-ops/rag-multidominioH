"""Metadatos textuales derivados del contenido de un fragmento.

Estos metadatos NO son columnas del esquema: se extraen del propio texto del
fragmento. En concreto:

* el numero de pagina procede del marcador ``[página N]`` que inserta el
  extractor de PDF en la FASE 3;
* la referencia de articulo procede de un encabezado ``Artículo N`` presente
  en el propio fragmento.

Se presentan siempre como informacion DETECTADA EN EL FRAGMENTO, nunca como
metadatos confirmados del documento (regla: no inventar paginas ni articulos).
Si el patron no aparece, devuelven ``None`` en lugar de fabricar un valor.

Tambien se extrae la "frase exacta" de una consulta (numero de resolucion o
referencia a articulo) para priorizar coincidencias literales en la busqueda
lexica.
"""

from __future__ import annotations

import re

# Marcador de pagina insertado por el extractor de PDF (FASE 3).
_PAGINA_RE = re.compile(r"\[\s*p[aá]gina\s+(\d+)\s*\]", re.IGNORECASE)

# Encabezado de articulo al inicio de una linea (estilo "Artículo 2." o
# "Articulo 5.-"). Se exige que abra linea para no confundirlo con una
# mencion incidental dentro de una oracion.
_ARTICULO_RE = re.compile(
    r"(?:^|\n)\s*art[ií]culos?\s+n?[°º]?\s*(\d+)",
    re.IGNORECASE,
)

# Numero de resolucion tipo "138/2026" (con posibles espacios alrededor de "/").
_RESOLUCION_RE = re.compile(r"\b\d{1,4}\s*/\s*\d{4}\b")

# Referencia a articulo en cualquier posicion (para frase exacta de consulta).
_ARTICULO_CONSULTA_RE = re.compile(
    r"\bart[ií]culos?\s+n?[°º]?\s*\d+", re.IGNORECASE
)


def extraer_pagina(contenido: str | None) -> int | None:
    """Primer numero de pagina detectado en el fragmento, o ``None``."""
    if not contenido:
        return None
    coincidencia = _PAGINA_RE.search(contenido)
    if not coincidencia:
        return None
    try:
        return int(coincidencia.group(1))
    except ValueError:  # pragma: no cover - el grupo es \d+
        return None


def extraer_referencia(contenido: str | None) -> str | None:
    """Primer encabezado ``Artículo N`` detectado en el fragmento, o ``None``."""
    if not contenido:
        return None
    coincidencia = _ARTICULO_RE.search(contenido)
    if not coincidencia:
        return None
    return f"Artículo {coincidencia.group(1)}"


def extraer_frase_exacta(consulta: str | None) -> str:
    """Frase literal mas especifica de la consulta, para coincidencia exacta.

    Prioriza un numero de resolucion (``138/2026``); si no, una referencia a
    articulo (``artículo 1``). Devuelve cadena vacia si no hay ninguna.
    """
    if not consulta:
        return ""
    resolucion = _RESOLUCION_RE.search(consulta)
    if resolucion:
        return re.sub(r"\s*/\s*", "/", resolucion.group(0))
    articulo = _ARTICULO_CONSULTA_RE.search(consulta)
    if articulo:
        return articulo.group(0)
    return ""
