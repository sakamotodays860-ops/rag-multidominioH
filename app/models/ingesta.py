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
    # Gate de contenido minimo (opt-in). 0 = desactivado. Un documento cuyo
    # texto limpio tenga menos caracteres que este umbral se rechaza como error
    # individual en lugar de generar fragmentos de baja calidad.
    min_caracteres: Annotated[int, Field(ge=0)] = 0

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


class DocumentoIngestado(BaseModel):
    """Metadatos de trazabilidad de un documento aceptado (fase D).

    Reflejan lo que se extrajo realmente. El esquema actual de
    ``normativa_bancaria`` solo persiste ``contenido`` y ``origen_url``; el
    titulo, el MIME y el numero de paginas se conservan aqui (respuesta de la
    ingesta) sin alterar el esquema. Ver
    ``docs/validacion_datos_reales.md`` para la limitacion y la migracion
    aditiva propuesta.
    """

    url: str
    titulo: str | None = None
    mime: str | None = None
    paginas: int | None = None
    fragmentos: int = 0


class RespuestaIngesta(BaseModel):
    """Informe de una ingesta terminada (requisito F).

    Incluye la trazabilidad temporal de las cuatro fases (requisito K).

    Idempotencia: ``registros_insertados`` son las filas nuevas y
    ``registros_omitidos`` los fragmentos que ya existian en la coleccion (mismo
    contenido normalizado) mas los duplicados detectados dentro de la propia
    peticion. ``duplicados`` lista las URLs cuyo contenido coincidia con otra en
    la misma peticion.
    """

    coleccion_id: str
    urls_recibidas: int
    urls_procesadas: int
    urls_con_error: int
    errores: list[ErrorPorUrl] = Field(default_factory=list)
    documentos: list[DocumentoIngestado] = Field(default_factory=list)
    duplicados: list[str] = Field(default_factory=list)

    fragmentos_generados: int
    embeddings_generados: int
    registros_insertados: int
    registros_omitidos: int = 0

    tiempo_scraping_s: float
    tiempo_chunking_s: float
    tiempo_embeddings_s: float
    tiempo_insercion_s: float
    tiempo_total_s: float


class RespuestaPrevisualizacion(BaseModel):
    """Informe de una previsualizacion de ingesta SIN Gemini ni base de datos.

    Ejecuta solo scraping + limpieza + chunking + deduplicacion y devuelve los
    conteos y una muestra de fragmentos para revisar la calidad de la extraccion
    antes de autorizar una ingesta real (que consume cuota de embeddings).
    """

    coleccion_id: str
    urls_recibidas: int
    urls_procesadas: int
    urls_con_error: int
    errores: list[ErrorPorUrl] = Field(default_factory=list)
    documentos: list[DocumentoIngestado] = Field(default_factory=list)

    fragmentos_generados: int
    # URLs cuyo contenido era identico al de otra ya considerada.
    duplicados: list[str] = Field(default_factory=list)
    # Primeros fragmentos extraidos (recortados) para inspeccion manual.
    muestra_fragmentos: list[str] = Field(default_factory=list)

    tiempo_scraping_s: float
    tiempo_chunking_s: float
    tiempo_total_s: float