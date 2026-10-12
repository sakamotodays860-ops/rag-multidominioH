"""Configuracion compartida de las pruebas de la FASE 2.

PRINCIPIO: las pruebas son SOLO LECTURA. No insertan, no borran y no
modifican `normativa_bancaria`. Cuando una comprobacion necesita datos, exige
antes que los registros de demostracion existan con el tamano esperado; si no
es asi, la prueba falla en lugar de crear datos nuevos (y duplicarlos).

Las constantes y constructores de vectores estan en `tests/helpers.py`.
"""

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

# URL de la base de datos para las pruebas.
#   - Dentro del contenedor: se usa DATABASE_URL tal cual (host `postgres`).
#   - Fuera del contenedor: exportar TEST_DATABASE_URL apuntando a localhost,
#     por ejemplo:
#     TEST_DATABASE_URL=postgresql+asyncpg://rag_user:<clave>@localhost:5432/rag_multidominio
DB_URL = os.environ.get("TEST_DATABASE_URL") or settings.database_url


def base_url() -> str:
    """URL base del backend para las pruebas HTTP (stdlib, sin httpx)."""
    return os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture(scope="session")
def db_url() -> str:
    """URL de conexion a la base de datos usada por las pruebas."""
    if not DB_URL:
        pytest.skip("DATABASE_URL no esta definida")
    return DB_URL


@pytest.fixture
async def motor(db_url: str):
    """Motor de SQLAlchemy de solo lectura, destruido tras cada prueba."""
    engine = create_async_engine(db_url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sesion(motor):
    """Sesion abierta contra la base de datos de pruebas."""
    factoria = async_sessionmaker(motor, expire_on_commit=False)
    async with factoria() as sesion:
        yield sesion


@pytest.fixture(scope="session")
def url_base() -> str:
    """URL del backend para las pruebas HTTP."""
    return base_url()