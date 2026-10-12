"""Modelos de dominio (Pydantic) compartidos por las capas de la aplicacion.

FASE 2: `app/models/normativa.py` define la validacion de `coleccion_id`, los
esquemas de salida de `match_normativa_coleccion` y del endpoint de
diagnostico.

FASE 3: `app/models/ingesta.py` define la peticion/respuesta de la ingesta web
(`POST /api/ingestar-web`).
"""

from app.models.normativa import (
    COLECCION_ID_MAX_LEN,
    ColeccionInfo,
    Embedding,
    NormativaMatch,
    embedding_a_literal_pgvector,
    validar_coleccion_id,
)

__all__ = [
    "COLECCION_ID_MAX_LEN",
    "ColeccionInfo",
    "Embedding",
    "NormativaMatch",
    "embedding_a_literal_pgvector",
    "validar_coleccion_id",
]