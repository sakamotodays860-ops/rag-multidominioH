"""Endpoint ``POST /api/consultar-auto`` (fase H: chat conversacional).

Capa HTTP fina sobre :func:`app.services.chat.ejecutar_consulta_auto`:
valida la peticion (Pydantic 422 + dominio 400) y traduce los fallos de
servicio a codigos HTTP, igual que ``POST /api/consultar``.
"""

from fastapi import APIRouter, HTTPException

import httpx

from app.core.config import get_settings
from app.models.consulta import PeticionConsultaAuto, RespuestaConsultaAuto
from app.services.chat import ejecutar_consulta_auto
from app.services.consulta import (
    ErrorBaseDatos,
    ErrorContaminacion,
)
from app.services.embeddings import ErrorEmbeddings
from app.services.generacion import ErrorGeneracion, ErrorProveedorNoDisponible

router = APIRouter(prefix="/api", tags=["chat"])

_UPSTREAM = (
    ErrorEmbeddings,
    ErrorGeneracion,
    ErrorBaseDatos,
    ErrorContaminacion,
    httpx.TimeoutException,
    httpx.RequestError,
)


@router.post(
    "/consultar-auto",
    response_model=RespuestaConsultaAuto,
    status_code=200,
    summary="Consulta conversacional con deteccion automatica de dominio",
    description=(
        "Recibe una pregunta en lenguaje natural y selecciona la coleccion "
        "(financiera o tributaria) segun su contenido. Si la pregunta es "
        "ambigua o ajena a esos dominios, pide aclaracion o informa de la "
        "especializacion, sin consultar a ciegas. Nunca redacta normativa a "
        "partir de datos de prueba (seed://)."
    ),
)
async def consultar_auto(peticion: PeticionConsultaAuto) -> RespuestaConsultaAuto:
    config = get_settings()
    try:
        return await ejecutar_consulta_auto(
            peticion,
            usar_lexico=config.recuperacion_lexica,
            permitir_lexico_sin_embedding=config.recuperacion_lexica_sin_embedding,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail=f"Solicitud de consulta no valida: {error}"
        ) from error
    except ErrorProveedorNoDisponible as error:
        # 5xx persistente del proveedor de generacion: 503 con aviso claro.
        raise HTTPException(
            status_code=503,
            detail=f"Proveedor de generacion no disponible: {error}",
        ) from error
    except _UPSTREAM as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Proveedor de embeddings/generacion o base de datos no "
                f"disponible: {error}"
            ),
        ) from error
