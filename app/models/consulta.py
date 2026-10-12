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
    """Un fragmento recuperado por la RPC, ya depurado al dominio de la PC."""

    id: int
    coleccion_id: str
    origen_url: str | None = None
    similitud: float
    contenido: str


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