"""Generación de respuestas con el modelo de chat de Gemini (FASE 4).

Reutiliza deliberadamente las decisiones de ``app.services.embeddings``
(FASE 3), que ya se validó: HTTP plano con ``httpx`` (sin SDK pesado),
reintentos FINITOS con backoff exponencial + jitter ante 429 /
``RESOURCE_EXHAUSTED``, y frontera de errores definitiva en
:class:`ErrorGeneracion`.

Se reutilizan, importados de ``embeddings.py`` sin modificar ese módulo:
``_RETRIABLES``, ``_ReintentarReintentable``, ``_es_limitacion_respuesta`` y
``_retry_after``.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import httpx

from app.services.embeddings import (
    _ReintentarReintentable,
    _RETRIABLES,
    _es_limitacion_respuesta,
    _retry_after,
)

_URL_GENERAR = (
    "https://generativelanguage.googleapis.com/"
    "v1beta/models/{modelo}:generateContent"
)


class ErrorGeneracion(RuntimeError):
    """Fallo definitivo de la generacion de la respuesta (sin mas reintentos)."""


@dataclass
class ClienteGeneracionGemini:
    """Genera texto contra la API REST ``generateContent`` de Gemini.

    La ``temperatura`` es fija (REQUISITO 3: ``0.0``) para respuestas
    deterministas. El ``modelo`` por defecto procede de
    ``Settings.gemini_model`` (``GEMINI_MODEL``), el mismo ya configurado en
    FASE 3/4; los tests lo inyectan sin tocar variables de entorno.
    """

    api_key: str | None
    modelo: str = "gemini-2.0-flash"
    temperatura: float = 0.0
    max_reintentos: int = 5
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0
    timeout_s: float = 60.0

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    async def generar(
        self, prompt: str, cliente: httpx.AsyncClient | None = None
    ) -> str:
        """Devuelve el texto completo de la respuesta para un prompt.

        Si ``cliente`` es ``None`` se crea uno temporal; pasar un cliente
        permite a los tests inyectar un transporte simulado.
        """
        if not self.api_key:
            raise ErrorGeneracion(
                "GEMINI_API_KEY no configurada; la consulta no puede generar "
                "respuestas."
            )
        propio = cliente is None
        cliente = cliente or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            return await self._generar_con_reintentos(cliente, prompt)
        finally:
            if propio:
                await cliente.aclose()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _generar_con_reintentos(
        self, cliente: httpx.AsyncClient, prompt: str
    ) -> str:
        """Reintenta la llamada hasta agotar ``max_reintentos``."""
        ultimo_error: BaseException | None = None
        for intento in range(1, self.max_reintentos + 1):
            try:
                return await self._generar_intento(cliente, prompt)
            except _ReintentarReintentable as error:
                ultimo_error = error
                if intento >= self.max_reintentos:
                    break
                espera = self._calcular_espera(error, intento)
                await asyncio.sleep(espera)
        url_referencia = _URL_GENERAR.format(modelo=self.modelo)
        raise ErrorGeneracion(
            f"Gemini no respondio tras {self.max_reintentos} intentos "
            f"({url_referencia}): {ultimo_error}"
        ) from ultimo_error

    def _calcular_espera(
        self, error: "_ReintentarReintentable", intento: int
    ) -> float:
        """Backoff exponencial con jitter y limite superior (misma logica FASE 3)."""
        if error.retry_after_s is not None:
            return min(float(error.retry_after_s), self.backoff_max_s)
        exponencial = self.backoff_base_s * (2 ** (intento - 1))
        jitter = random.uniform(0.0, exponencial * 0.5)
        return min(exponencial + jitter, self.backoff_max_s)

    async def _generar_intento(
        self, cliente: httpx.AsyncClient, prompt: str
    ) -> str:
        """Una sola llamada a ``generateContent`` (sin reintentos)."""
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperatura},
        }
        respuesta = await cliente.post(
            _URL_GENERAR.format(modelo=self.modelo),
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )
        cuerpo = respuesta.text

        if respuesta.status_code in _RETRIABLES or _es_limitacion_respuesta(cuerpo):
            raise _ReintentarReintentable(
                f"HTTP {respuesta.status_code}: {cuerpo[:200]}",
                _retry_after(respuesta),
            )
        if respuesta.status_code != 200:
            raise ErrorGeneracion(
                f"Gemini devolvio HTTP {respuesta.status_code}: {cuerpo[:300]}"
            )

        datos = respuesta.json()
        candidatos = datos.get("candidates") or []
        if not candidatos:
            raise ErrorGeneracion(
                "Gemini devolvio una respuesta sin candidatos."
            )
        razon = (candidatos[0].get("finishReason") or "").strip()
        partes = (candidatos[0].get("content") or {}).get("parts") or []
        texto = "".join(parte.get("text", "") for parte in partes).strip()
        if not texto:
            raise ErrorGeneracion(
                "Gemini devolvio una respuesta vacia"
                + (f" (finishReason: {razon})." if razon else ".")
            )
        return texto