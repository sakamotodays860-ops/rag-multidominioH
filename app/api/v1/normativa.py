"""Endpoints de diagnostico de colecciones (FASE 2).

IMPORTANTE: este modulo NO implementa la consulta RAG
(`POST /api/consultar`) ni la ingesta (`POST /api/ingestar-web`).

Unicamente sirve para dos cosas, ambas de solo lectura:

1. validar `coleccion_id` (formato, longitud y caracteres admitidos);
2. comprobar si una coleccion existe y cuantos fragmentos tiene, lo que
   permite verificar visualmente el aislamiento entre dominios.
"""

from fastapi import APIRouter, HTTPException

from app.database.repository import info_coleccion, listar_colecciones
from app.database.session import abrir_sesion
from app.models.normativa import ColeccionInfo, validar_coleccion_id

router = APIRouter()


def _validar(coleccion_id: str) -> str:
    """Valida el identificador o devuelve un error 400 al cliente."""
    try:
        return validar_coleccion_id(coleccion_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get(
    "/api/colecciones",
    tags=["normativa"],
    summary="Lista las colecciones existentes",
)
async def colecciones() -> list[dict]:
    """Devuelve cada ``coleccion_id`` con su numero de fragmentos."""
    async with abrir_sesion() as session:
        filas = await listar_colecciones(session)
    return [{"coleccion_id": cid, "fragmentos": total} for cid, total in filas]


@router.get(
    "/api/colecciones/{coleccion_id}",
    tags=["normativa"],
    summary="Diagnostico de una coleccion",
    responses={400: {"description": "coleccion_id invalido"}},
)
async def detalle_coleccion(coleccion_id: str) -> ColeccionInfo:
    """Valida ``coleccion_id`` y reporta su existencia y su tamano.

    No realiza busqueda vectorial: es una comprobacion de estado, no la
    consulta RAG.
    """
    _validar(coleccion_id)

    async with abrir_sesion() as session:
        try:
            return await info_coleccion(session, coleccion_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None