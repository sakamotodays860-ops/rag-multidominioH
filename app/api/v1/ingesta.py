"""Endpoint `POST /api/ingestar-web` (FASE 3).

Recibe un lote de URLs y un ``coleccion_id``, ejecuta el pipeline de ingesta e
informa de:
  * URLs procesadas / con error (los fallos individuales no abortan);
  * fragmentos y embeddings generados (por lotes, modelo
    ``models/gemini-embedding-001``, 768 dims);
  * filas insertadas en ``normativa_bancaria`` sin contaminar otras
    colecciones;
  * tiempos por fase (scraping, chunking, embeddings, insercion) y total.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.ingesta import PeticionIngesta, RespuestaIngesta
from app.services.embeddings import ErrorEmbeddings
from app.services.ingesta import ejecutar_ingesta
from app.utils.chunking import ConfiguracionChunkingError

router = APIRouter(prefix="/api", tags=["ingesta"])


@router.post(
    "/ingestar-web",
    response_model=RespuestaIngesta,
    status_code=200,
    summary="Ingesta web concurrente (scraping + chunking + embeddings)",
)
async def ingestar_web(peticion: PeticionIngesta) -> RespuestaIngesta:
    """Ingiere documentos web en la coleccion indicada.

    - **coleccion_id**: identificador de la coleccion (max. 50 caracteres).
    - **urls**: 1..50 URLs http/https.
    - **chunk_size**: > 0 (por defecto 1200).
    - **chunk_overlap**: >= 0 y < chunk_size (por defecto 200).
    - **concurrency_workers**: 1..64 (por defecto 8).

    Los fallos de cada URL (timeout, HTTP, conexion, contenido vacio) se
    registran en ``errores`` y no detienen la ingesta del resto.
    """
    try:
        resultado = await ejecutar_ingesta(peticion)
        return resultado.respuesta
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ConfiguracionChunkingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ErrorEmbeddings as error:
        raise HTTPException(status_code=502, detail=str(error)) from error