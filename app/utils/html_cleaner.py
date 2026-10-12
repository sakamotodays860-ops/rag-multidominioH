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
    """Recoge el texto plano descartando el ruido de presentacion."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pila: list[str] = []
        self._piezas: list[str] = []

    def _dentro_de_ignorada(self) -> bool:
        return any(etiqueta in IGNORAR for etiqueta in self._pila)

    def _separar(self) -> None:
        if not self._piezas:
            return
        if not self._piezas[-1].endswith("\n"):
            self._piezas.append(_SEPARADOR)

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
            self._piezas.append(data)

    def handle_comment(self, data: str) -> None:
        # Los comentarios HTML nunca son contenido util.
        return


def _normalizar(texto: str) -> str:
    """Colapsa espacios y deja como maximo un parrafo en blanco."""
    salida: list[str] = []
    for linea in texto.split("\n"):
        linea = " ".join(linea.split())
        if linea:
            salida.append(linea)
        elif salida and salida[-1] != "":
            salida.append("")
    while salida and salida[-1] == "":
        salida.pop()
    return "\n".join(salida)


def limpiar_html(html: str) -> str:
    """Devuelve el texto util de un documento HTML, o una cadena vacia.

    - descarta `script`, `style`, `nav`, `header`, `footer`, `aside`,
      formularios y cualquier otro elemento de `IGNORAR`;
    - descarta los comentarios HTML;
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
    return _normalizar("".join(extractor._piezas))