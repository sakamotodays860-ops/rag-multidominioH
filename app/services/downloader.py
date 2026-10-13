"""Descarga concurrente de paginas web y limpieza de su contenido (FASE 3).

Requisito A: `httpx` como cliente HTTP asincrono.

Un `asyncio.Semaphore` limita la concurrencia real a ``concurrency_workers``
peticiones simultaneas, independientemente del numero de URLs recibidas.

Tratamiento de errores (requisito A): cada URL se gestiona de forma aislada.
Las URLs invalidas, los timeouts, los errores HTTP y el contenido vacio se
registran como fallos individuales y NO detienen la ingesta del resto.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.models.ingesta import ErrorPorUrl
from app.services.pdf_extractor import (
    PdfInvalido,
    PdfSinTexto,
    detectar_pdf,
    extraer_texto_pdf,
    parece_binario,
)
from app.utils.dedup import huella_contenido
from app.utils.html_cleaner import limpiar_html

_URL_MAX_LEN = 2048

# Content-Types que se tratan como HTML/texto explicito.
_MIMES_TEXTO = ("text/html", "application/xhtml", "text/plain", "text/xml", "application/xml")


@dataclass
class DocumentoDescargado:
    """Texto extraido de una URL + metadatos de trazabilidad (fase D)."""

    url: str
    texto: str
    titulo: str | None = None
    mime: str | None = None
    paginas: int | None = None


@dataclass
class ResultadoDescarga:
    """Resultado de descargar y limpiar una lista de URLs."""

    textos: list[tuple[str, str]] = field(default_factory=list)
    errores: list[ErrorPorUrl] = field(default_factory=list)
    documentos: list[DocumentoDescargado] = field(default_factory=list)
    # URLs cuyo contenido era identico a otro ya conservado (deduplicacion).
    duplicados: list[str] = field(default_factory=list)

    def registrar_ok(
        self,
        url: str,
        texto: str,
        *,
        titulo: str | None = None,
        mime: str | None = None,
        paginas: int | None = None,
    ) -> None:
        self.textos.append((url, texto))
        self.documentos.append(
            DocumentoDescargado(
                url=url, texto=texto, titulo=titulo, mime=mime, paginas=paginas
            )
        )

    def registrar_error(self, url: str, motivo: str) -> None:
        self.errores.append(ErrorPorUrl(url=url, motivo=motivo))


def extraer_titulo_html(html: str) -> str | None:
    """Devuelve el contenido de ``<title>`` si existe (best effort)."""
    coincidencia = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not coincidencia:
        return None
    titulo = " ".join(coincidencia.group(1).split())
    return titulo or None


def es_url_valida(url: str) -> bool:
    """Comprueba que ``url`` sea una URL http/https bien formada.

    Se descartan esquemas peligrosos (`file`, `ftp`, `data`...), URLs sin
    host, URLs con espacios y URLs absurdamente largas.
    """
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url or len(url) > _URL_MAX_LEN:
        return False
    if any(caracter.isspace() for caracter in url):
        return False
    try:
        partes = urlsplit(url)
    except ValueError:
        return False
    return partes.scheme.lower() in {"http", "https"} and bool(partes.netloc)


class _ContenidoExcesivo(Exception):
    """La pagina supera ``max_bytes``: proteger la memoria del servidor."""


async def _leer_bytes_con_limite(respuesta: httpx.Response, max_bytes: int) -> bytes:
    """Lee el cuerpo en streaming y aborta si supera ``max_bytes``.

    Se mantiene en bytes (no se decodifica) porque el contenido puede ser un
    PDF binario; la decision HTML/PDF se toma despues.
    """
    if respuesta.headers.get("content-length"):
        try:
            declarado = int(respuesta.headers["content-length"])
            if declarado > max_bytes:
                raise _ContenidoExcesivo()
        except ValueError:
            pass

    piezas: list[bytes] = []
    total = 0
    async for trozo in respuesta.aiter_bytes():
        piezas.append(trozo)
        total += len(trozo)
        if total > max_bytes:
            raise _ContenidoExcesivo()
    return b"".join(piezas)


async def _descargar_una(
    semaforo: asyncio.Semaphore,
    cliente: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    resultado: ResultadoDescarga,
) -> None:
    """Descarga ``url`` y vierte el texto limpio en ``resultado``.

    Cualquier fallo individual queda registrado en ``resultado.errores``.
    """
    async with semaforo:
        if not es_url_valida(url):
            resultado.registrar_error(url, "URL invalida")
            return
        try:
            async with cliente.stream("GET", url) as respuesta:
                if respuesta.status_code < 200 or respuesta.status_code >= 300:
                    raise httpx.HTTPStatusError(
                        f"HTTP {respuesta.status_code}",
                        request=respuesta.request,
                        response=respuesta,
                    )
                datos = await _leer_bytes_con_limite(respuesta, max_bytes)
                content_type = respuesta.headers.get("content-type")
        except _ContenidoExcesivo:
            resultado.registrar_error(
                url, f"contenido excede el limite de {max_bytes} bytes"
            )
            return
        except httpx.TimeoutException:
            resultado.registrar_error(url, "timeout")
            return
        except httpx.HTTPStatusError as error:
            resultado.registrar_error(url, f"HTTP {error.response.status_code}")
            return
        except httpx.RequestError as error:
            resultado.registrar_error(url, f"error de conexion: {type(error).__name__}")
            return

        if detectar_pdf(datos, content_type):
            _registrar_pdf(resultado, url, datos, content_type)
            return

        if parece_binario(datos, content_type):
            resultado.registrar_error(
                url, "contenido binario no soportado (no es HTML ni PDF)"
            )
            return

        html = datos.decode("utf-8", errors="replace")
        texto = limpiar_html(html)
        if not texto.strip():
            resultado.registrar_error(url, "contenido vacio o sin texto util")
            return
        resultado.registrar_ok(
            url,
            texto,
            titulo=extraer_titulo_html(html),
            mime=(content_type or "text/html").split(";")[0].strip(),
        )


def _registrar_pdf(
    resultado: ResultadoDescarga,
    url: str,
    datos: bytes,
    content_type: str | None,
) -> None:
    """Extrae el texto de un PDF y lo registra o reporta el fallo concreto."""
    try:
        texto, paginas = extraer_texto_pdf(datos)
    except PdfSinTexto as error:
        resultado.registrar_error(url, str(error))
        return
    except PdfInvalido as error:
        resultado.registrar_error(url, f"PDF invalido: {error}")
        return
    resultado.registrar_ok(
        url,
        texto,
        mime=(content_type or "application/pdf").split(";")[0].strip(),
        paginas=paginas,
    )


def _huella_texto(texto: str) -> str:
    """Huella estable del contenido normalizado (para deduplicar).

    Delega en ``app.utils.dedup`` para compartir la MISMA normalizacion que el
    deduplicado frente a la base de datos (evita discrepancias).
    """
    return huella_contenido(texto)


def _deduplicar(resultado: ResultadoDescarga) -> None:
    """Conserva el primer documento de cada contenido y anota los repetidos.

    Dos URLs distintas pueden devolver el mismo texto (por ejemplo, la misma
    ley publicada en dos rutas). Se conserva la primera aparicion para no
    duplicar fragmentos ni gastar cuota de embeddings en contenido repetido.
    """
    vistos: set[str] = set()
    textos: list[tuple[str, str]] = []
    documentos: list[DocumentoDescargado] = []
    for documento in resultado.documentos:
        huella = huella_contenido(documento.texto)
        if huella in vistos:
            resultado.duplicados.append(documento.url)
            continue
        vistos.add(huella)
        documentos.append(documento)
        textos.append((documento.url, documento.texto))
    resultado.textos = textos
    resultado.documentos = documentos


async def descargar_y_limpiar(
    urls: list[str],
    *,
    concurrency: int,
    timeout_s: float,
    user_agent: str,
    max_bytes: int,
    transporte: httpx.AsyncBaseTransport | None = None,
) -> ResultadoDescarga:
    """Descarga ``urls`` concurrentemente y devuelve su texto limpio.

    Aparece una unica cola de logica pero se ejecuta con ``concurrency``
    workers: ``asyncio.gather`` programa todas las tareas y el semaforo
    reparte el trabajo respetando el limite.

    ``transporte`` es solo para pruebas (``httpx.MockTransport``); en
    produccion queda en ``None`` y se usa la red real.
    """
    resultado = ResultadoDescarga()
    semaforo = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=timeout_s, read=timeout_s * 2),
        follow_redirects=True,
        headers={"User-Agent": user_agent},
        limits=httpx.Limits(max_connections=max(1, concurrency)),
        transport=transporte,
    ) as cliente:
        tareas = [
            _descargar_una(semaforo, cliente, url, max_bytes, resultado)
            for url in urls
        ]
        await asyncio.gather(*tareas)
    _deduplicar(resultado)
    return resultado