"""Endpoint ``POST /api/consultar`` (FASE 4).

Capa HTTP del flujo RAG: valida la peticion (Pydantic 422 + dominio 400),
execution de la query y traduccion de errores de servicio a codigos HTTP.
"""

from fastapi import APIRouter, HTTPException

import httpx

from app.core.config import get_settings
from app.models.consulta import PeticionConsulta, RespuestaConsulta
from app.services.consulta import (
    ErrorBaseDatos,
    ErrorContaminacion,
    ejecutar_consulta,
)
from app.services.embeddings import ErrorEmbeddings
from app.services.generacion import ErrorGeneracion, ErrorProveedorNoDisponible

router = APIRouter(prefix="/api", tags=["consulta"])

# Errores de proveedor/no disponibles: el problema es de la configuracion o del
# servicio externo (Gemini), no del cliente -> 502.
_UPSTREAM = (
    ErrorEmbeddings,
    ErrorGeneracion,
    ErrorBaseDatos,
    ErrorContaminacion,
    httpx.TimeoutException,
    httpx.RequestError,
)


@router.post(
    "/consultar",
    response_model=RespuestaConsulta,
    status_code=200,
    summary="Consulta RAG aislada por coleccion_id",
    description=(
        "Responde una pregunta usando SOLO la normativa de la coleccion "
        "indicada. El embedding pregunta usa gemini-embedding-001 (768 dims); "
        "la generacion usa el modelo configurado con temperatura 0.0. "
        "Si no hay evidencia, no se invoca al LLM (total_evidencias=0)."
    ),
)
async def consultar(peticion: PeticionConsulta) -> RespuestaConsulta:
    config = get_settings()
    try:
        return await ejecutar_consulta(
            peticion,
            usar_lexico=config.recuperacion_lexica,
            permitir_lexico_sin_embedding=config.recuperacion_lexica_sin_embedding,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail=f"Solicitud de consulta no valida: {error}"
        ) from error
    except ErrorProveedorNoDisponible as error:
        # El proveedor de generacion no responde (5xx persistente): se informa
        # de forma explicita y se sugiere reintentar mas tarde (503).
        raise HTTPException(
            status_code=503,
            detail=f"Proveedor de generacion no disponible: {error}",
        ) from error
    except _UPSTREAM as error:
        raise HTTPException(
            status_code=502,
            detail=f"Proveedor de embeddings/generacion o base de datos no "
            f"disponible: {error}",
        ) from error