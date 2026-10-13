"""Aplicacion FastAPI.

FASE 1: `GET /` y `GET /health`.
FASE 2: diagnostico de colecciones (`/api/colecciones`).
FASE 3: ingesta web concurrente (`POST /api/ingestar-web`) con scraping,
limpieza HTML, chunking, embeddings Gemini por lotes e insercion vectorial.
FASE 4: consulta RAG (`POST /api/consultar`) con aislamiento vectorial por
`coleccion_id`, evidencia recuperada via RPC y generacion con temperatura 0.0.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api.v1.routes import api_router
from app.core.config import settings
from app.database.session import dispose_motor

# Directorio de archivos estaticos servidos por la aplicacion. Se resuelve de
# forma robusta a partir de la ubicacion real de este modulo para que funcione
# tanto en local como dentro del contenedor (donde el codigo vive en /app/app).
STATIC_DIR = Path(__file__).resolve().parent / "static"

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
    "- `GET /chat` (interfaz web de chat)\n"
    "- `GET /api/colecciones`\n"
    "- `GET /api/colecciones/{coleccion_id}` (diagnostico de solo lectura)\n"
    "- `POST /api/ingestar-web` (ingesta web, FASE 3)\n"
    "- `POST /api/consultar` (consulta RAG, FASE 4)\n"
    "- `POST /api/consultar-auto` (chat conversacional con deteccion de dominio)\n\n"
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


@app.get("/chat", tags=["interfaz"], include_in_schema=False)
def chat() -> FileResponse:
    """Sirve la interfaz web de chat RAG multi-dominio.

    La ruta del archivo se resuelve de forma robusta a partir de la ubicacion
    del modulo, por lo que no depende del directorio de trabajo del proceso.
    """
    ruta = STATIC_DIR / "chat.html"
    if not ruta.is_file():
        raise HTTPException(
            status_code=404, detail="La interfaz de chat no esta disponible."
        )
    return FileResponse(ruta, media_type="text/html")