"""Router raiz de la API.

- FASE 1: `GET /`, que confirma que la API esta activa.
- FASE 2: `GET /api/colecciones` y `GET /api/colecciones/{coleccion_id}`,
  diagnostico de solo lectura para validar `coleccion_id`.
- FASE 3: `POST /api/ingestar-web`, ingesta web concurrente (scraping,
  limpieza HTML, chunking, embeddings Gemini por lotes, inserción vectorial).
- FASE 4: `POST /api/consultar`, consulta RAG multi-dominio con aislamiento
  estricto por `coleccion_id` (RPC `match_normativa_coleccion` + verificacion
  antes del LLM).
"""

from fastapi import APIRouter

from app.api.v1.consulta import router as consulta_router
from app.api.v1.ingesta import router as ingesta_router
from app.api.v1.normativa import router as normativa_router

api_router = APIRouter()


@api_router.get("/", tags=["salud"])
def raiz() -> dict[str, str]:
    """Estado de la API."""
    return {
        "status": "online",
        "mensaje": "Pipeline RAG Multi-Dominio activo",
    }


api_router.include_router(normativa_router)
api_router.include_router(ingesta_router)
api_router.include_router(consulta_router)