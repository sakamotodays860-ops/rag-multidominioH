"""Acceso a datos.

Exponen dos piezas:

- `session`: motor asincrono (SQLAlchemy 2.x) y factoria de sesiones sobre
  `DATABASE_URL`.
- `repository`: consultas sobre `normativa_bancaria`. La busqueda vectorial
  pasa siempre por la funcion RPC `match_normativa_coleccion`, que filtra por
  `coleccion_id`.
"""

from app.database.repository import (
    buscar_por_coleccion,
    contar_coleccion,
    info_coleccion,
    listar_colecciones,
)
from app.database.session import (
    abrir_sesion,
    dispose_motor,
    get_factoria,
    get_motor,
    url_desde_host,
)

__all__ = [
    "abrir_sesion",
    "buscar_por_coleccion",
    "contar_coleccion",
    "dispose_motor",
    "get_factoria",
    "get_motor",
    "info_coleccion",
    "listar_colecciones",
    "url_desde_host",
]