"""Extraccion de texto de documentos PDF (fase C: extraccion robusta).

Se usa ``pypdf`` (Python puro, compatible con Python 3.12) en lugar de
herramientas nativas: no requiere binarios del sistema ni modifica la imagen
base. La dependencia se importa de forma perezosa para que el resto del
pipeline (y las pruebas de HTML) siga funcionando aunque ``pypdf`` no este
instalado.

Responsabilidades:

* ``detectar_pdf``: decide si unos bytes son un PDF por firma binaria
  (``%PDF-``, tolerando espacios iniciales) **o** por ``Content-Type``.
* ``parece_binario``: heuristica para rechazar contenido no textual que no sea
  PDF (no se debe interpretar binario como HTML).
* ``extraer_texto_pdf``: extrae texto pagina por pagina, conserva el numero de
  paginas y falla de forma explicita si el PDF no tiene texto extraible.
"""

from __future__ import annotations

import io

PDF_MAGIC = b"%PDF-"

# Un PDF puede empezar con espacios/bytes en blanco antes de la firma (el
# estandar permite un desplazamiento inicial). Se toleran hasta 1 KiB.
_MAX_DESPLAZAMIENTO = 1024


class PdfInvalido(RuntimeError):
    """El documento dice ser PDF pero no se pudo interpretar (corrupto, cifrado)."""


class PdfSinTexto(RuntimeError):
    """El PDF es valido pero no contiene texto extraible (posible OCR)."""


class PdfRequiereDependencia(PdfInvalido):
    """El PDF usa cifrado AES y falta la dependencia opcional ``cryptography``.

    Hereda de :class:`PdfInvalido` para que los llamadores que ya tratan ese
    caso sigan funcionando, pero expone un motivo accionable en lugar de un
    opaco ``DependencyError``.
    """


# Mensaje unico para el fallo por backend de cifrado ausente.
_MENSAJE_DEPENDENCIA = (
    "PDF cifrado con AES: se requiere la dependencia 'cryptography' (>=3.1) "
    "para extraer su texto; instale 'cryptography'."
)


def detectar_pdf(datos: bytes, content_type: str | None = None) -> bool:
    """True si ``datos`` es un PDF por firma binaria o por ``Content-Type``.

    Se inspecciona la firma antes que el MIME: hay servidores que entregan un
    PDF con ``Content-Type: text/html`` o ``application/octet-stream``.
    """
    if content_type and "application/pdf" in content_type.lower():
        return True
    if not datos:
        return False
    inicio = datos[:_MAX_DESPLAZAMIENTO]
    indice = inicio.find(PDF_MAGIC)
    return indice != -1


def parece_binario(datos: bytes, content_type: str | None = None) -> bool:
    """Heuristica: contenido binario no textual (distinto de PDF).

    Se considera binario si el ``Content-Type`` es de imagen/video/audio/zip o
    si los primeros bytes contienen un byte NUL (impropio de HTML/texto).
    """
    if content_type:
        tipo = content_type.lower()
        if any(
            marca in tipo
            for marca in (
                "application/octet-stream",
                "application/zip",
                "application/x-gzip",
                "image/",
                "audio/",
                "video/",
                "application/x-msdownload",
            )
        ):
            return True
    return b"\x00" in datos[:1024]


def _marcador_pagina(numero: int) -> str:
    """Marcador de pagina para conservar la trazabilidad al unir paginas."""
    return f"[página {numero}]"


def extraer_paginas_pdf(datos: bytes) -> tuple[list[tuple[int, str]], int]:
    """Devuelve ``([(numero_de_pagina, texto), ...], total_de_paginas)``.

    Los numeros son 1-based. Solo se devuelven las paginas con texto, en el
    orden del documento, de modo que las citas puedan referenciar la pagina
    original. ``total_de_paginas`` incluye las paginas vacias.

    Lanza :class:`PdfRequiereDependencia` si el PDF esta cifrado con AES y falta
    ``cryptography``, :class:`PdfInvalido` si no se puede interpretar y
    :class:`PdfSinTexto` si ninguna pagina aporta texto (documento escaneado).
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import DependencyError, PdfReadError
    except ImportError as error:  # pragma: no cover - depende del entorno
        raise PdfInvalido(
            "La extraccion de PDF requiere la dependencia 'pypdf'."
        ) from error

    try:
        lector = PdfReader(io.BytesIO(datos), strict=False)
        if lector.is_encrypted:
            try:
                # Muchos PDF oficiales usan contrasena vacia: se intenta
                # descifrar y, si no basta, se reporta como invalido en vez de
                # fingir OCR.
                lector.decrypt("")
            except DependencyError as error:
                raise PdfRequiereDependencia(_MENSAJE_DEPENDENCIA) from error
            except Exception as error:  # noqa: BLE001
                raise PdfInvalido("PDF cifrado sin contrasena valida.") from error
        # `len(lector.pages)` puede descifrar objetos y fallar por dependencia.
        total = len(lector.pages)
        paginas: list[tuple[int, str]] = []
        for numero, pagina in enumerate(lector.pages, start=1):
            try:
                texto_pagina = (pagina.extract_text() or "").strip()
            except DependencyError as error:
                raise PdfRequiereDependencia(_MENSAJE_DEPENDENCIA) from error
            except Exception:  # noqa: BLE001 - una pagina ilegible no aborta el resto
                texto_pagina = ""
            if texto_pagina:
                paginas.append((numero, texto_pagina))
    except PdfInvalido:
        raise
    except DependencyError as error:
        raise PdfRequiereDependencia(_MENSAJE_DEPENDENCIA) from error
    except PdfReadError as error:
        raise PdfInvalido(f"PDF no interpretable: {error}") from error
    except Exception as error:  # noqa: BLE001 - frontera del parser
        raise PdfInvalido(f"PDF no interpretable: {type(error).__name__}") from error

    if not paginas:
        raise PdfSinTexto(
            "El PDF no contiene texto extraible (posible documento escaneado "
            "que requeriria OCR)."
        )
    return paginas, total


def extraer_texto_pdf(datos: bytes) -> tuple[str, int]:
    """Devuelve ``(texto, numero_de_paginas)`` de un PDF.

    El texto conserva los numeros de pagina: cuando el documento tiene mas de
    una pagina, cada pagina se precede de un marcador ``[página N]`` para que
    las citas de la respuesta puedan referenciarla. En PDF de una sola pagina no
    se inserta marcador para no ensuciar el texto.

    Lanza :class:`PdfRequiereDependencia`, :class:`PdfInvalido` o
    :class:`PdfSinTexto` segun el caso.
    """
    paginas, total = extraer_paginas_pdf(datos)
    if total > 1:
        texto = "\n\n".join(
            f"{_marcador_pagina(numero)}\n{contenido}"
            for numero, contenido in paginas
        )
    else:
        texto = "\n\n".join(contenido for _, contenido in paginas)
    return texto, total
