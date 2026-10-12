"""Motor asincrono de acceso a datos (SQLAlchemy 2.x + asyncpg).

`DATABASE_URL` apunta al host `postgres`, que solo resuelve dentro de la red de
Docker Compose. Cuando los tests o las herramientas de verificacion corren en
el host, se reescribe el host por `localhost`: el puerto 5432 ya esta publicado
en el contenedor `postgres`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_motor: AsyncEngine | None = None
_factoria: async_sessionmaker[AsyncSession] | None = None


def url_desde_host(url: str | None = None) -> str | None:
    """Devuelve una URL de conexion utilizable desde el host.

    Sustituye `@postgres:` por `@localhost:`. Si la URL no usa ese host (por
    ejemplo, `postgresql+asyncpg://...@localhost:5432/...`) se devuelve igual.
    """
    origen = url if url is not None else settings.database_url
    if not origen:
        return None
    return origen.replace("@postgres:", "@localhost:", 1)


def crear_motor(url: str | None = None) -> AsyncEngine:
    """Crea un motor asincrono apuntando a `url` (o a `DATABASE_URL`)."""
    destino = url if url is not None else settings.database_url
    if not destino:
        raise RuntimeError("DATABASE_URL no esta definida")
    return create_async_engine(destino, pool_pre_ping=True)


def get_motor() -> AsyncEngine:
    """Devuelve el motor unico de la aplicacion."""
    global _motor
    if _motor is None:
        _motor = crear_motor()
    return _motor


def get_factoria() -> async_sessionmaker[AsyncSession]:
    """Devuelve la factoria de sesiones asociada al motor unico."""
    global _factoria
    if _factoria is None:
        _factoria = async_sessionmaker(
            get_motor(), class_=AsyncSession, expire_on_commit=False
        )
    return _factoria


@asynccontextmanager
async def abrir_sesion() -> AsyncIterator[AsyncSession]:
    """Abre una sesion y la cierra al terminar el bloque.

    Uso tipico en los endpoints::

        async with abrir_sesion() as sesion:
            await buscar_por_coleccion(sesion, ...)

    Nota: `async_sessionmaker` en si NO soporta el protocolo de contexto
    asincrono; hay que invocarlo para obtener la sesion. Este helper envuelve
    ese detalle para que los consumidores no tengan que recordarlo.
    """
    async with get_factoria()() as sesion:
        yield sesion


async def dispose_motor() -> None:
    """Cierra el pool de conexiones. Se invoca al apagar la aplicacion."""
    global _motor, _factoria
    if _motor is not None:
        await _motor.dispose()
        _motor = None
        _factoria = None