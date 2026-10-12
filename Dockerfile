# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Imagen base del backend: Python 3.12 sobre slim.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ---------------------------------------------------------------------------
# Dependencias del sistema: se necesitan las herramientas de compilacion para
# posibles wheels sin binario precompilado y curl para el HEALTHCHECK.
# Se eliminan al terminar para no engordar la imagen final.
# ---------------------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Dependencias de Python (capa cacheada).
# ---------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Codigo de la aplicacion.
# ---------------------------------------------------------------------------
COPY app ./app

# ---------------------------------------------------------------------------
# Scripts CLI y dataset del benchmark (FASE 5): se copian en la imagen para
# poder ejecutarlos dentro del contenedor con
#   docker compose exec -T backend python scripts/benchmark_concurrencia.py ...
# y graficar con:
#   docker compose exec -T backend python scripts/graficar_benchmark.py
# ---------------------------------------------------------------------------
COPY scripts ./scripts
COPY benchmarks ./benchmarks

# ---------------------------------------------------------------------------
# Pruebas de la FASE 2: entran en la imagen para poder ejecutarlas con
#   docker compose exec -T backend python -m pytest -q
# Las dependencias de pruebas NO se instalan aqui (se mantienen fuera de la
# imagen de ejecucion); se cargan en tiempo de test con
#   python -m pip install -r requirements-dev.txt
# ---------------------------------------------------------------------------
COPY requirements-dev.txt ./
COPY pytest.ini ./
COPY tests ./tests

# ---------------------------------------------------------------------------
# Usuario no root para ejecutar la aplicacion.
# ---------------------------------------------------------------------------
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

# Comprobacion de vida usando el endpoint /health del propio servicio.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]