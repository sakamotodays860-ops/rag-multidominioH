"""Aplicacion FastAPI.

FASE 1: `GET /` y `GET /health`.
FASE 2: diagnostico de colecciones (`/api/colecciones`).
FASE 3: ingesta web concurrente (`POST /api/ingestar-web`) con scraping,
limpieza HTML, chunking, embeddings Gemini por lotes e insercion vectorial.
FASE 4: consulta RAG (`POST /api/consultar`) con aislamiento vectorial por
`coleccion_id`, evidencia recuperada via RPC y generacion con temperatura 0.0.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.routes import api_router
from app.core.config import settings
from app.database.session import dispose_motor

DESCRIPTION = (
    "Pipeline RAG Multi-Dominio con Scraping Concurrente y Aislamiento Vectorial.\n\n"
    "**Estado actual:** FASE 4 - Consulta RAG multi-dominio con aislamiento "
    "vectorial implementada.\n"
    "La ingesta descarga multiples URLs de forma concurrente, limpia el HTML, "
    "trocea el texto en fragmentos solapados y los almacena con embeddings de "
    "`models/gemini-embedding-001` (768 dims, por lotes) en la coleccion "
    "indicada. La consulta embehde la pregunta con el mismo modelo y recupera "
    "evidencia **solo** de la coleccion pedida via la RPC "
    "`match_normativa_coleccion`, verificando el aislamiento antes de llamar "
    "al LLM (temperatura 0.0).\n\n"
    "Endpoints disponibles:\n"
    "- `GET /api/colecciones`\n"
    "- `GET /api/colecciones/{coleccion_id}` (diagnostico de solo lectura)\n"
    "- `POST /api/ingestar-web` (ingesta web, FASE 3)\n"
    "- `POST /api/consultar` (consulta RAG, FASE 4)\n\n"
    "Endpoints pendientes (fases posteriores):\n"
    "- `POST /api/benchmark` (FASE 5)"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Libera el pool de conexiones al apagar el servidor."""
    yield
    await dispose_motor()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=DESCRIPTION,
    lifespan=lifespan,
)

app.include_router(api_router)


@app.get("/health", tags=["salud"])
def health() -> dict[str, str]:
    """Comprobacion de vida del backend."""
    return {"status": "online", "mensaje": "Backend activo"}