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
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.models.ingesta import ErrorPorUrl
from app.utils.html_cleaner import limpiar_html

_URL_MAX_LEN = 2048


@dataclass
class ResultadoDescarga:
    """Resultado de descargar y limpiar una lista de URLs."""

    textos: list[tuple[str, str]] = field(default_factory=list)
    errores: list[ErrorPorUrl] = field(default_factory=list)

    def registrar_ok(self, url: str, texto: str) -> None:
        self.textos.append((url, texto))

    def registrar_error(self, url: str, motivo: str) -> None:
        self.errores.append(ErrorPorUrl(url=url, motivo=motivo))


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


async def _leer_con_limite(respuesta: httpx.Response, max_bytes: int) -> str:
    """Lee el cuerpo en streaming y aborta si supera ``max_bytes``."""
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
    return b"".join(piezas).decode("utf-8", errors="replace")


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
                html = await _leer_con_limite(respuesta, max_bytes)
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

        texto = limpiar_html(html)
        if not texto.strip():
            resultado.registrar_error(url, "contenido vacio o sin texto util")
            return
        resultado.registrar_ok(url, texto)


async def descargar_y_limpiar(
    urls: list[str],
    *,
    concurrency: int,
    timeout_s: float,
    user_agent: str,
    max_bytes: int,
) -> ResultadoDescarga:
    """Descarga ``urls`` concurrentemente y devuelve su texto limpio.

    Aparece una unica cola de logica pero se ejecuta con ``concurrency``
    workers: ``asyncio.gather`` programa todas las tareas y el semaforo
    reparte el trabajo respetando el limite.
    """
    resultado = ResultadoDescarga()
    semaforo = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=timeout_s, read=timeout_s * 2),
        follow_redirects=True,
        headers={"User-Agent": user_agent},
        limits=httpx.Limits(max_connections=max(1, concurrency)),
    ) as cliente:
        tareas = [
            _descargar_una(semaforo, cliente, url, max_bytes, resultado)
            for url in urls
        ]
        await asyncio.gather(*tareas)
    return resultado