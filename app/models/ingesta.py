"""Modelos de la ingesta web (FASE 3).

Contiene los esquemas de peticion y respuesta de `POST /api/ingestar-web` y la
validacion de dominio de los parametros (requisitos A y C).

Mismo patron que FASE 2: la validacion de `coleccion_id` se delega en
``app.models.normativa`` para no duplicar reglas.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.normativa import validar_coleccion_id

_settings = get_settings()

URLS_POR_PETICION_MAX: int = _settings.urls_por_peticion_max
CONCURRENCIA_MIN: int = 1
CONCURRENCIA_MAX: int = 64


def validar_col_peticion(coleccion_id: str) -> str:
    """Valida el ``coleccion_id`` de una peticion de ingesta.

    Reutiliza la validacion canonica de FASE 2; el patron de identificador se
    obtiene de la misma fuente que la base de datos.
    """
    return validar_coleccion_id(coleccion_id)


class PeticionIngesta(BaseModel):
    """Cuerpo de `POST /api/ingestar-web`."""

    coleccion_id: Annotated[str, Field(min_length=1, max_length=50)]
    urls: Annotated[list[str], Field(min_length=1, max_length=50)]

    chunk_size: Annotated[int, Field(ge=1)] = _settings.chunk_size_por_defecto
    chunk_overlap: Annotated[int, Field(ge=0)] = (
        _settings.chunk_overlap_por_defecto
    )
    concurrency_workers: Annotated[int, Field(ge=1, le=64)] = (
        min(_settings.http_max_concurrencia, CONCURRENCIA_MAX)
    )

    def validar(self) -> None:
        """Valida el dominio: sobre ``chunk_size``/``chunk_overlap``.

        La validacion de coleccion y URLs individuales se hace en la capa de
        servicio para poder reportar URLs invalidas sin abortar la ingesta
        (requisito A).
        """
        validar_col_peticion(self.coleccion_id)
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                "chunk_overlap debe ser estrictamente menor que chunk_size "
                f"(recibido: chunk_size={self.chunk_size}, "
                f"chunk_overlap={self.chunk_overlap})."
            )


class ErrorPorUrl(BaseModel):
    """Fallo individual de una URL (no aborta la ingesta)."""

    url: str
    motivo: str


class RespuestaIngesta(BaseModel):
    """Informe de una ingesta terminada (requisito F).

    Incluye la trazabilidad temporal de las cuatro fases (requisito K).
    """

    coleccion_id: str
    urls_recibidas: int
    urls_procesadas: int
    urls_con_error: int
    errores: list[ErrorPorUrl] = Field(default_factory=list)

    fragmentos_generados: int
    embeddings_generados: int
    registros_insertados: int

    tiempo_scraping_s: float
    tiempo_chunking_s: float
    tiempo_embeddings_s: float
    tiempo_insercion_s: float
    tiempo_total_s: float