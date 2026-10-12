"""Embeddings con el modelo `models/gemini-embedding-001` (FASE 3).

Requisito 5 del enunciado:

* modelo exacto ``gemini-embedding-001``, solicitando ``outputDimensionality``
  para emitir vectores de 768 dimensiones (compatibles con ``VECTOR(768)``);
* embeddings por LOTES vía ``:batchEmbedContents`` (maximo 100 solicitudes por
  llamada) en lugar de una llamada por chunk;
* manejo de 429 / ``RESOURCE_EXHAUSTED`` con un numero FINITO de reintentos,
  backoff exponencial progresivo con jitter y limite maximo;
* errores definitivos registrados como :class:`ErrorEmbeddings`.

Se usa HTTP plano con ``httpx`` en lugar del SDK ``google-genai``: solo se
necesita un POST a un endpoint REST, con lo que la imagen no gana dependencias
pesadas. La clase se inyecta con un cliente, de modo que los tests mockean el
transporte sin tocar red real.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import httpx

_URL_EMBED = (
    "https://generativelanguage.googleapis.com/"
    "v1beta/models/{modelo}:batchEmbedContents"
)

# Codigos de estado que merecen reintento.
_RETRIABLES = frozenset({429, 500, 502, 503, 504})


class ErrorEmbeddings(RuntimeError):
    """Fallo definitivo de la generacion de embeddings (sin mas reintentos)."""


@dataclass
class ClienteEmbeddingsGemini:
    """Genera embeddings por lotes contra la API REST de Gemini.

    Los parametros se inyectan por constructura (configuracion procedente de
    ``Settings`` en ``app.services.ingesta``); los tests construyen la clase
    con valores propios sin depender de las variables de entorno.
    """

    api_key: str | None
    modelo: str = "gemini-embedding-001"
    dimension: int = 768
    batch_size: int = 100
    max_reintentos: int = 5
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0
    timeout_s: float = 60.0
    # Observacion SOLO para benchmark (FASE 5): registra cada llamada HTTP a
    # `batchEmbedContents` como ``(status_code, fue_limitacion)``. No altera
    # ningun comportamiento del cliente ni de las fases 3/4.
    historico_llamadas: list[tuple[int, bool]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    async def generar(
        self, textos: list[str], cliente: httpx.AsyncClient | None = None
    ) -> list[list[float]]:
        """Devuelve un embedding de ``dimension`` floats por cada texto.

        Los lotes se procesan secuencialmente: es una operacion de
        *rate-limiting* deliberada (el endpoint efectivo es susceptible de
        429) y mantiene el servidor dentro de los margenes de cuota.

        Si ``cliente`` es ``None`` se crea uno temporal. Pasar un cliente
        permite a los tests inyectar un transporte simulado.
        """
        if not self.api_key:
            raise ErrorEmbeddings(
                "GEMINI_API_KEY no configurada; la ingesta no puede generar "
                "embeddings."
            )
        if not textos:
            return []

        propio = cliente is None
        cliente = cliente or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            resultados: list[list[float]] = []
            for inicio in range(0, len(textos), self.batch_size):
                lote = textos[inicio : inicio + self.batch_size]
                vectores = await self._embed_lote(cliente, lote)
                resultados.extend(vectores)
            return resultados
        finally:
            if propio:
                await cliente.aclose()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _embed_lote(
        self, cliente: httpx.AsyncClient, lote: list[str]
    ) -> list[list[float]]:
        """Reintenta un lote completo hasta agotar ``max_reintentos``."""
        ultimo_error: BaseException | None = None
        for intento in range(1, self.max_reintentos + 1):
            try:
                return await self._embed_intento(cliente, lote)
            except _ReintentarReintentable as error:
                ultimo_error = error
                if intento >= self.max_reintentos:
                    break
                espera = self._calcular_espera(error, intento)
                await asyncio.sleep(espera)
        url_referencia = _URL_EMBED.format(modelo=self.modelo)
        raise ErrorEmbeddings(
            f"Gemini no respondio tras {self.max_reintentos} intentos "
            f"({url_referencia}): {ultimo_error}"
        ) from ultimo_error

    def _calcular_espera(self, error: "_ReintentarReintentable", intento: int) -> float:
        """Backoff exponencial con jitter y limite superior.

        Si el servidor indica ``Retry-After``, se respeta (capturado con cap
        en ``backoff_max_s``); en caso contrario: base * 2^(intento-1) + jitter.
        Ningun valor puede superar ``backoff_max_s``.
        """
        if error.retry_after_s is not None:
            return min(float(error.retry_after_s), self.backoff_max_s)
        exponencial = self.backoff_base_s * (2 ** (intento - 1))
        jitter = random.uniform(0.0, exponencial * 0.5)
        return min(exponencial + jitter, self.backoff_max_s)

    async def _embed_intento(
        self, cliente: httpx.AsyncClient, lote: list[str]
    ) -> list[list[float]]:
        """Una sola llamada a ``batchEmbedContents`` (sin reintentos)."""
        payload = {
            "requests": [
                {
                    "model": f"models/{self.modelo}",
                    "content": {"parts": [{"text": texto}]},
                    "outputDimensionality": self.dimension,
                }
                for texto in lote
            ]
        }
        respuesta = await cliente.post(
            _URL_EMBED.format(modelo=self.modelo),
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )
        cuerpo = respuesta.text
        self.historico_llamadas.append(
            (respuesta.status_code, _es_limitacion_respuesta(cuerpo))
        )

        if respuesta.status_code in _RETRIABLES or _es_limitacion_respuesta(cuerpo):
            raise _ReintentarReintentable(
                f"HTTP {respuesta.status_code}: {cuerpo[:200]}",
                _retry_after(respuesta),
            )
        if respuesta.status_code != 200:
            raise ErrorEmbeddings(
                f"Gemini devolvio HTTP {respuesta.status_code}: {cuerpo[:300]}"
            )

        datos = respuesta.json()
        filas = datos.get("embeddings") or []
        if len(filas) != len(lote):
            raise ErrorEmbeddings(
                f"Gemini devolvio {len(filas)} embeddings para "
                f"{len(lote)} textos."
            )
        vectores: list[list[float]] = []
        for fila in filas:
            valores = fila.get("values")
            if not valores:
                raise ErrorEmbeddings("Gemini devolvio un embedding vacio.")
            vector = [float(valor) for valor in valores]
            if len(vector) != self.dimension:
                raise ErrorEmbeddings(
                    f"Embedding de {len(vector)} dimensiones; se esperaban "
                    f"{self.dimension} (incompatible con VECTOR(768))."
                )
            vectores.append(vector)
        return vectores


class _ReintentarReintentable(Exception):
    """Error transitorio que puede desaparecer tras esperar."""

    def __init__(self, mensaje: str, retry_after_s: float | None = None) -> None:
        super().__init__(mensaje)
        self.retry_after_s = retry_after_s

    def __str__(self) -> str:
        base = super().__str__()
        if self.retry_after_s is not None:
            return f"{base} (Retry-After: {self.retry_after_s}s)"
        return base


def _es_limitacion_respuesta(cuerpo: str) -> bool:
    """Detecta limitacion de cuota aunque el codigo HTTP no sea 429."""
    return "RESOURCE_EXHAUSTED" in cuerpo or "quota exceeded" in cuerpo.lower() or (
        "rate limit" in cuerpo.lower() and "429" in cuerpo
    )


def _retry_after(respuesta: httpx.Response) -> float | None:
    """Lee la cabecera ``Retry-After`` si existe y es un numero."""
    crudo = respuesta.headers.get("retry-after")
    if not crudo:
        return None
    try:
        # El estandar permite fechas; aqui solo se interpretan segundos que es
        # lo que la API de Gemini devuelve y lo que necesitamos para dormir.
        return max(0.0, float(crudo))
    except ValueError:
        return None