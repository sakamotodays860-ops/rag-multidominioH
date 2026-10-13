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
import logging
import random
from dataclasses import dataclass

import httpx

from app.services.embeddings import (
    _ReintentarReintentable,
    _RETRIABLES,
    _es_cuota_diaria,
    _es_limitacion_respuesta,
    _retry_after,
)

logger = logging.getLogger(__name__)

_URL_GENERAR = (
    "https://generativelanguage.googleapis.com/"
    "v1beta/models/{modelo}:generateContent"
)

# Errores de servidor (proveedor) que, si persisten tras los reintentos, se
# reportan como "proveedor no disponible" en lugar de un fallo generico.
_CODIGOS_NO_DISPONIBLE = frozenset({500, 502, 503, 504})


class ErrorGeneracion(RuntimeError):
    """Fallo definitivo de la generacion de la respuesta (sin mas reintentos)."""


class ErrorProveedorNoDisponible(ErrorGeneracion):
    """El proveedor de generacion (Gemini) no esta disponible (5xx persistente).

    Subclase de :class:`ErrorGeneracion` para que las capas que solo distinguen
    "fallo de generacion" sigan funcionando; la capa HTTP la traduce a un
    ``503 Service Unavailable`` con un mensaje claro de reintento.
    """


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
    # Modelo alternativo OPCIONAL. Si se configura y el principal agota sus
    # reintentos con un 5xx (``ErrorProveedorNoDisponible``), se intenta UNA vez
    # contra este modelo, siguiendo la recomendacion oficial de Gemini para
    # errores 503 ("cambiar temporalmente a otro modelo"). Por defecto vacio:
    # no cambia el comportamiento actual ni consume cuota extra.
    modelo_respaldo: str | None = None

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
            try:
                return await self._generar_con_reintentos(cliente, prompt)
            except ErrorProveedorNoDisponible as error:
                # Recomendacion oficial de Gemini para 503: intentar con otro
                # modelo. Solo si hay respaldo configurado y distinto del actual.
                if not self.modelo_respaldo or self.modelo_respaldo == self.modelo:
                    raise
                logger.warning(
                    "Modelo %s no disponible; se reintenta una vez con el "
                    "modelo de respaldo %s. Causa: %s",
                    self.modelo,
                    self.modelo_respaldo,
                    error,
                )
                return await self._generar_con_reintentos(
                    cliente, prompt, modelo=self.modelo_respaldo
                )
        finally:
            if propio:
                await cliente.aclose()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _generar_con_reintentos(
        self, cliente: httpx.AsyncClient, prompt: str, modelo: str | None = None
    ) -> str:
        """Reintenta la llamada hasta agotar ``max_reintentos``."""
        modelo_efectivo = modelo or self.modelo
        ultimo_error: BaseException | None = None
        for intento in range(1, self.max_reintentos + 1):
            try:
                return await self._generar_intento(cliente, prompt, modelo_efectivo)
            except _ReintentarReintentable as error:
                ultimo_error = error
                if intento >= self.max_reintentos:
                    break
                espera = self._calcular_espera(error, intento)
                await asyncio.sleep(espera)
        url_referencia = _URL_GENERAR.format(modelo=modelo_efectivo)
        # 5xx persistente: el proveedor no esta disponible -> error propio y
        # claro (la capa HTTP lo traduce a 503). No se oculta la causa original.
        if (
            isinstance(ultimo_error, _ReintentarReintentable)
            and ultimo_error.status_code in _CODIGOS_NO_DISPONIBLE
        ):
            # Se registra el cuerpo real informado por Gemini: sin esto no era
            # posible saber si el 503 era sobrecarga, cuota o proyecto.
            logger.warning(
                "Proveedor de generacion no disponible (HTTP %s) con el modelo "
                "%s tras %d intentos. Respuesta del proveedor: %s",
                ultimo_error.status_code,
                modelo_efectivo,
                self.max_reintentos,
                str(ultimo_error)[:500],
            )
            raise ErrorProveedorNoDisponible(
                "El proveedor de generacion (Gemini) no esta disponible "
                f"temporalmente (HTTP {ultimo_error.status_code}) tras "
                f"{self.max_reintentos} intentos con el modelo {modelo_efectivo}. "
                "Intenta de nuevo mas tarde."
            ) from ultimo_error
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
        self, cliente: httpx.AsyncClient, prompt: str, modelo: str | None = None
    ) -> str:
        """Una sola llamada a ``generateContent`` (sin reintentos)."""
        modelo_efectivo = modelo or self.modelo
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperatura},
        }
        respuesta = await cliente.post(
            _URL_GENERAR.format(modelo=modelo_efectivo),
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )
        cuerpo = respuesta.text

        # Cuota diaria agotada: reintentar no ayuda y consume mas cuota. Se
        # falla de inmediato con un error definitivo y comprensible.
        if _es_cuota_diaria(cuerpo):
            raise ErrorGeneracion(
                "La cuota diaria de Gemini esta agotada; no se reintenta. "
                f"Detalle: {cuerpo[:200]}"
            )

        if respuesta.status_code in _RETRIABLES or _es_limitacion_respuesta(cuerpo):
            raise _ReintentarReintentable(
                f"HTTP {respuesta.status_code}: {cuerpo[:200]}",
                _retry_after(respuesta),
                respuesta.status_code,
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