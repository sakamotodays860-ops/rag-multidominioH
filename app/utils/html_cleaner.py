"""Limpieza de HTML: extrae unicamente el texto util de una pagina.

FASE 3, requisito B.

Se usa `html.parser` de la biblioteca estandar en lugar de beautifulsoup4:
la practica solo pide conservar texto plano y descartar ruido de presentacion
(`script`, `style`, `nav`, `header`...), y con el parser propio se consigue sin
añadir dependencias nativas ni peso a la imagen. Ademas, al ser puro codigo
Python, las pruebas de esta funcion corren sin red y sin instalar nada.

Decision de diseno: los elementos bloqueados se controlan con una pila de
etiquetas abiertas en lugar de un contador simple, porque el HTML real esta
mal formado con frecuencia y un contador se desincroniza con etiquetas sin
cerrar.
"""

from __future__ import annotations

import unicodedata
from html.parser import HTMLParser

# Etiquetas cuyo contenido se descarta por completo.
IGNORAR: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "math",
        "iframe",
        "object",
        "embed",
        "form",
        "button",
        "input",
        "select",
        "option",
        "textarea",
        "nav",
        "header",
        "footer",
        "aside",
        "dialog",
        "canvas",
        "audio",
        "video",
        "meta",
        "link",
    }
)

# Elementos sin contenido propio: no se apilan porque nunca tendran su
# etiqueta de cierre. Es CRITICO que todos los void elements de HTML esten
# aqui: si se apilaran, permanecerian en la pila para siempre y cualquier
# etiqueta de cierre posterior desequilibraria la lectura del documento.
VACIOS: frozenset[str] = frozenset(
    {
        "br",
        "img",
        "hr",
        "wbr",
        "source",
        "track",
        "col",
        "colgroup",
        "area",
        "base",
        "param",
        "input",
        "meta",
        "link",
        "embed",
        "keygen",
    }
)

# Etiquetas de bloque: entre una y otra se inserta un separador de parrafos,
# de modo que el chunking posterior no fusione ideas de distintos bloques.
SEPARADORES: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "main",
        "blockquote",
        "pre",
        "table",
        "tr",
        "td",
        "th",
        "caption",
        "figure",
        "figcaption",
        "address",
        "details",
        "summary",
        "blockquote",
        "hgroup",
        "center",
        "head",
        "body",
        "title",
    }
)

_SEPARADOR = "\n\n"


class _ExtractorTexto(HTMLParser):
    """Recoge el texto plano descartando el ruido de presentacion.

    Cada pieza de texto se anota con si procede del interior de un enlace
    (``<a>``). Esa marca permite despues descartar menus de navegacion, que en
    los portales reales suelen ser listas de enlaces cortos consecutivos aunque
    no vengan envueltos en ``<nav>``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pila: list[str] = []
        # Cada pieza es (texto, es_enlace).
        self._piezas: list[tuple[str, bool]] = []

    def _dentro_de_ignorada(self) -> bool:
        return any(etiqueta in IGNORAR for etiqueta in self._pila)

    def _dentro_de_enlace(self) -> bool:
        return "a" in self._pila

    def _separar(self) -> None:
        if not self._piezas:
            return
        if not self._piezas[-1][0].endswith("\n"):
            self._piezas.append((_SEPARADOR, False))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VACIOS:
            # Los elementos void no se apilan. Solo `br` aporta estructura.
            if tag == "br" and not self._dentro_de_ignorada():
                self._separar()
            return
        self._pila.append(tag)
        if not self._dentro_de_ignorada() and tag in SEPARADORES:
            self._separar()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag == "br" and not self._dentro_de_ignorada():
            self._separar()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._pila:
            while self._pila:
                ultimo = self._pila.pop()
                if ultimo == tag:
                    break
        if not self._dentro_de_ignorada() and tag in SEPARADORES:
            self._separar()

    def handle_data(self, data: str) -> None:
        if self._dentro_de_ignorada():
            return
        if data:
            self._piezas.append((data, self._dentro_de_enlace()))

    def handle_comment(self, data: str) -> None:
        # Los comentarios HTML nunca son contenido util.
        return

    def bloques(self) -> list[tuple[str, bool]]:
        """Agrupa las piezas en ``(texto_normalizado, solo_enlaces)``.

        Un bloque es el texto entre separadores de parrafo. ``solo_enlaces`` es
        verdadero si todo el texto del bloque proviene de enlaces.
        """
        bloques: list[tuple[str, bool]] = []
        actual: list[str] = []
        marcas: list[bool] = []

        def cerrar() -> None:
            partes = [" ".join(parte.split()) for parte in actual]
            partes = [parte for parte in partes if parte]
            if partes:
                bloques.append((" ".join(partes), all(marcas)))
            actual.clear()
            marcas.clear()

        for texto, es_enlace in self._piezas:
            if texto == _SEPARADOR:
                cerrar()
            else:
                actual.append(texto)
                marcas.append(es_enlace)
        cerrar()
        return bloques


# Frases de "chrome" del portal que nunca son normativa. Se comparan sin
# acentos, en minusculas y por subcadena.
_BOILERPLATE: tuple[str, ...] = (
    "pasar al contenido principal",
    "saltar al contenido",
    "saltar a la navegacion",
    "ir al contenido principal",
    "uso de .gob.bo",
    "uso de https",
    "los portales oficiales",
    "emplean el dominio .gob.bo",
    "conectado a un dominio",
    "esto significa que estas conectado",
    "sitio oficial del estado plurinacional",
    "se sugiere compartir informacion sensible",
)

# Un bloque de enlaces se considera navegacion si es corto y va acompañado de
# otros enlaces cortos seguidos (un menu). Un enlace aislado se conserva.
_MAX_ENLACE_NAV = 60
_MIN_RUN_NAV = 2


def _sin_acentos(texto: str) -> str:
    """Quita los diacriticos para comparar frases de forma robusta."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(caracter for caracter in descompuesto if not unicodedata.combining(caracter))


def _es_boilerplate(texto: str) -> bool:
    normalizado = _sin_acentos(texto.casefold())
    return any(frase in normalizado for frase in _BOILERPLATE)


def _quitar_runs_de_enlaces(
    bloques: list[tuple[str, bool]],
    *,
    maximo: int = _MAX_ENLACE_NAV,
    minimo: int = _MIN_RUN_NAV,
) -> list[tuple[str, bool]]:
    """Descarta secuencias de enlaces cortos (menus) pero conserva enlaces aislados."""
    salida: list[tuple[str, bool]] = []
    indice = 0
    total = len(bloques)
    while indice < total:
        texto, es_enlace = bloques[indice]
        if es_enlace and len(texto) <= maximo:
            fin = indice
            while (
                fin < total
                and bloques[fin][1]
                and len(bloques[fin][0]) <= maximo
            ):
                fin += 1
            if fin - indice >= minimo:
                indice = fin
                continue
        salida.append(bloques[indice])
        indice += 1
    return salida


def limpiar_html(html: str) -> str:
    """Devuelve el texto util de un documento HTML, o una cadena vacia.

    - descarta `script`, `style`, `nav`, `header`, `footer`, `aside`,
      formularios y cualquier otro elemento de `IGNORAR`;
    - descarta los comentarios HTML;
    - elimina frases de "chrome" del portal (saltar al contenido, avisos del
      dominio `.gob.bo`) y menus de navegacion formados por enlaces cortos
      consecutivos;
    - separa los bloques con una linea en blanco para que el chunking corte
      en limites de parrafo;
    - normaliza espacios y lineas en blanco.
    """
    if not html:
        return ""
    extractor = _ExtractorTexto()
    # `feed` no lanza sobre HTML mal formado de forma normal; aun asi, un
    # documento irrescatable no debe tumbar toda la ingesta.
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        return ""
    bloques = [bloque for bloque in extractor.bloques() if not _es_boilerplate(bloque[0])]
    bloques = _quitar_runs_de_enlaces(bloques)
    return "\n\n".join(texto for texto, _ in bloques)