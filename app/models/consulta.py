"""Modelos de la consulta RAG (FASE 4).

Cubren el contrato del endpoint ``POST /api/consultar``:

* ``PeticionConsulta``: cuerpo de la solicitud, validado por Pydantic y con
  chequeos de dominio (``validar_coleccion_id``) tal y como hace FASE 3 con
  las peticiones de ingesta.
* ``EvidenciaConsulta``: un fragmento de normativa recuperado por el RPC
  ``match_normativa_coleccion`` junto con su metadato (similitud, origen).
* ``RespuestaConsulta``: la respuesta del LLM + la evidencia usada.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.models.normativa import validar_coleccion_id

_settings = get_settings()

TOP_K_POR_DEFECTO = _settings.top_k_por_defecto
TOP_K_MAX = _settings.top_k_max
THRESHOLD_POR_DEFECTO = _settings.similarity_threshold_por_defecto
PREGUNTA_MAX_CARACTERES = _settings.consulta_pregunta_max_caracteres


class PeticionConsulta(BaseModel):
    """Cuerpo validado de ``POST /api/consultar``."""

    coleccion_id: Annotated[str, Field(min_length=1, max_length=50)]
    pregunta: str
    top_k: Annotated[int, Field(ge=1, le=TOP_K_MAX)] = TOP_K_POR_DEFECTO
    similarity_threshold: Annotated[float, Field(ge=-1.0, le=1.0)] = (
        THRESHOLD_POR_DEFECTO
    )
    # Si es False, se recupera la evidencia pero NO se invoca al LLM. Permite
    # mostrar fuentes aunque el proveedor de generacion no este disponible.
    generar: bool = True

    @field_validator("pregunta")
    @classmethod
    def _pregunta_no_vacia(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError(
                "pregunta no puede estar vacia ni ser solo espacios en blanco"
            )
        if len(valor) > PREGUNTA_MAX_CARACTERES:
            raise ValueError(
                f"pregunta supera el maximo de {PREGUNTA_MAX_CARACTERES} "
                "caracteres"
            )
        return valor

    def validar(self) -> None:
        """Chequeos de dominio fuera del alcance de Pydantic (REQUISITO 1)."""
        validar_coleccion_id(self.coleccion_id)


class EvidenciaConsulta(BaseModel):
    """Un fragmento recuperado, ya depurado al dominio de la PC.

    Incluye metadatos de recuperacion. ``similitud`` es la similitud coseno de
    la busqueda vectorial; es ``None`` cuando el fragmento se recupero solo por
    via lexica (en ese caso no existe un vector que comparar, no es un dato
    inventado). ``puntuacion_rrf`` es la puntuacion de fusion por rangos, que NO
    es similitud coseno.
    """

    id: int
    coleccion_id: str
    origen_url: str | None = None
    similitud: float | None = None
    contenido: str
    # True si el fragmento procede de datos de prueba (``seed://``). Nunca debe
    # presentarse como normativa real.
    es_datos_prueba: bool = False
    # --- Metadatos de la mejora de recuperacion ---
    # "vectorial", "lexica" o "hibrida" (coincidio por ambas vias).
    tipo_busqueda: str = "vectorial"
    # Puntuacion de Reciprocal Rank Fusion. NO es similitud coseno.
    puntuacion_rrf: float | None = None
    # Relevancia textual de PostgreSQL (ts_rank). NO es similitud coseno.
    puntuacion_lexica: float | None = None
    # True si la frase exacta de la consulta (p.ej. "138/2026") aparece literal.
    coincidencia_exacta: bool = False
    # Numero de terminos de la consulta presentes en el fragmento.
    terminos_coincidentes: int = 0
    # Pagina DETECTADA en el fragmento (marcador "[página N]"), no metadato
    # confirmado del documento. None si no aparece.
    pagina: int | None = None
    # Referencia de articulo DETECTADA en el fragmento (encabezado "Artículo N").
    referencia: str | None = None


class RespuestaConsulta(BaseModel):
    """Respuesta estructurada de ``POST /api/consultar``.

    Cuando no hay coincidencias, ``total_evidencias`` es ``0`` y ``respuesta``
    es un mensaje controlado (el LLM no se invoca, REQUISITO 6).
    """

    coleccion_id: str
    pregunta: str
    respuesta: str
    evidencias: list[EvidenciaConsulta] = Field(default_factory=list)
    total_evidencias: int = 0
    # True si se recupero evidencia pero NO se genero respuesta (por pedirlo el
    # cliente con ``generar=False``): el LLM no se invoco.
    solo_recuperacion: bool = False


class PeticionConsultaAuto(BaseModel):
    """Cuerpo de ``POST /api/consultar-auto`` (chat conversacional).

    El usuario puede no conocer las colecciones: basta la ``pregunta``. El
    dominio se detecta de forma determinista; tambien se admite forzarlo
    (``dominio``) o fijar una coleccion concreta (``coleccion_id``).
    """

    pregunta: str
    dominio: Annotated[str | None, Field(default=None, pattern="^(financiero|tributario)$")] = None
    coleccion_id: Annotated[str | None, Field(default=None, min_length=1, max_length=50)] = None
    top_k: Annotated[int, Field(ge=1, le=TOP_K_MAX)] = TOP_K_POR_DEFECTO
    similarity_threshold: Annotated[float, Field(ge=-1.0, le=1.0)] = (
        THRESHOLD_POR_DEFECTO
    )
    # Si es False, se recupera la evidencia pero NO se invoca al LLM.
    generar: bool = True

    @field_validator("pregunta")
    @classmethod
    def _pregunta_no_vacia(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError(
                "pregunta no puede estar vacia ni ser solo espacios en blanco"
            )
        if len(valor) > PREGUNTA_MAX_CARACTERES:
            raise ValueError(
                f"pregunta supera el maximo de {PREGUNTA_MAX_CARACTERES} "
                "caracteres"
            )
        return valor

    def validar(self) -> None:
        """Chequeos de dominio fuera del alcance de Pydantic."""
        if self.coleccion_id:
            validar_coleccion_id(self.coleccion_id)


class RespuestaConsultaAuto(BaseModel):
    """Respuesta de ``POST /api/consultar-auto``.

    Anade a la respuesta RAG la procedencia de la decision (dominio,
    coleccion), el estado de ambiguedad y advertencias (por ejemplo, si la
    evidencia procede de datos de prueba ``seed://``).
    """

    pregunta: str
    dominio: str | None = None
    coleccion_id: str | None = None
    respuesta: str
    evidencias: list[EvidenciaConsulta] = Field(default_factory=list)
    total_evidencias: int = 0
    ambigua: bool = False
    aclaracion: str | None = None
    advertencia: str | None = None
    # True si se recupero evidencia pero NO se genero respuesta (``generar=False``).
    solo_recuperacion: bool = False