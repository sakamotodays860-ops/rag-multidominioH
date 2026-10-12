# Pipeline RAG Multi-Dominio con Scraping Concurrente y Aislamiento Vectorial

> **Estado:** FASE 5 — Benchmark de concurrencia implementado. `POST
> /api/ingestar-web` descarga múltiples URLs con `asyncio` + `httpx`, limpia el
> HTML, trocea el texto en fragmentos solapados y los almacena con embeddings de
> `models/gemini-embedding-001` (768 dimensiones, por lotes) en PostgreSQL +
> pgvector. `POST /api/consultar` embehde la pregunta con el mismo modelo,
> recupera evidencia **solo** de la colección pedida vía la RPC
> `match_normativa_coleccion` (verificando el aislamiento antes del LLM) y
> genera la respuesta con el modelo configurado a temperatura 0.0. El benchmark
> reproduce el pipeline con p = 1, 2, 4, 8 workers sobre una carga idéntica y
> calcula T1, T_p, S_p = T1/T_p y E_p = S_p/p, con graficas y lecturas según la
> Ley de Amdahl.

---

## 1. Nombre del proyecto

**rag-multidominio**

## 2. Objetivo

Construir un sistema RAG (*Retrieval Augmented Generation*) capaz de:

1. Recibir una lista de URLs y un identificador de colección (`coleccion_id`).
2. Descargar y procesar las páginas web **concurrentemente**.
3. Dividir el contenido en *chunks*.
4. Generar *embeddings* mediante la API de **Gemini**.
5. Almacenar los embeddings en **PostgreSQL + pgvector**.
6. Mantener **aislamiento estricto entre colecciones** mediante `coleccion_id`.
7. Responder consultas RAG restringidas **exclusivamente** a una colección.
8. Gestionar límites de la API y errores `429` / `RESOURCE_EXHAUSTED`.
9. Comparar procesamiento **secuencial** frente a **concurrente**.
10. Calcular las métricas **T1**, **Tp**, **Speedup** y **Efficiency**.

El desarrollo se realiza **por fases**. Esta entrega cubre las FASES 1 a 5
(infraestructura, aislamiento vectorial, ingesta web, consulta RAG y benchmark
de concurrencia).

## 3. Arquitectura prevista

```
 Cliente (futuro Streamlit)
            |
            |  HTTP / JSON
            v
  +---------------------------+
  |  backend  (FastAPI)       |
  |                           |
  |  api/      -> routers     |  POST /api/ingestar-web
  |  services/ -> orquestacion|  POST /api/consultar
  |  models/   -> esquemas    |
  |  database/ -> acceso a    |
  |               datos       |
  |  core/     -> config      |
  |  utils/    -> helpers     |
  +------------+--------------+
               |  SQL (asyncpg)
               v
  +---------------------------+
  |  postgres 16 + pgvector   |
  |  volumen: pgdata          |
  +---------------------------+
```

Decisiones de diseño relevantes:

- **Aislamiento vectorial:** todas las consultas se filtrarán por
  `coleccion_id`; el índice y la función de búsqueda se diseñarán para que ese
  filtro sea siempre obligatorio.
- **Modularidad:** las capas `api → services → database` permiten que los
  endpoints futuros no mezclen lógica de negocio con acceso a datos.
- **Versionado de la API:** los routers viven en `app/api/v1/` para poder
  evolucionar sin romper clientes.
- **Concurrencia:** la comparación secuencial/concurrente se implementará
  sobre los *services*, de forma que la lógica de negocio no dependa del
  mecanismo de ejecución.

## 4. Tecnologías

| Capa | Tecnología | Estado en FASE 4 |
| --- | --- | --- |
| SO | Ubuntu Linux | ✔ |
| Contenedores | Docker / Docker Compose | ✔ Configurado |
| Lenguaje | Python 3.12 | ✔ Configurado |
| API | FastAPI + Uvicorn | ✔ Funcional (`GET /`, `/api/colecciones`, `POST /api/ingestar-web`, `POST /api/consultar`) |
| Base de datos | PostgreSQL 16 | ✔ Funcional (16.15) |
| ORM | SQLAlchemy 2.x + asyncpg | ✔ Funcional (FASE 2) |
| Vectores | pgvector | ✔ 0.8.7 instalada + RPC (FASE 2) |
| Aislamiento | `match_normativa_coleccion` | ✔ Implementado (FASE 2) |
| Descarga | httpx / asyncio | ✔ Implementado (FASE 3: scraping concurrente) |
| Limpieza HTML | `html.parser` (stdlib) | ✔ Implementado (FASE 3) |
| Chunking | ventana deslizante + overlap | ✔ Implementado (FASE 3) |
| Embeddings | Gemini `gemini-embedding-001` (768d, por lotes) | ✔ Implementado (FASE 3) |
| Generación | Gemini `gemini-2.0-flash` a temperatura 0.0 | ✔ Implementado (FASE 4) |
| Interfaz | Streamlit | ⏳ Fase posterior |

## 5. Estructura del proyecto

```
rag-multidominio/
├── app/
│   ├── __init__.py            # Paquete de la aplicación
│   ├── main.py                # Instancia FastAPI + /health + lifespan
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── routes.py      # GET /  (router raíz) + registra routers
│   │       ├── normativa.py   # GET /api/colecciones  (FASE 2)
│   │       ├── ingesta.py     # POST /api/ingestar-web  (FASE 3)
│   │       └── consulta.py    # POST /api/consultar  (FASE 4)
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py          # Settings (pydantic-settings), incl. FASE 3/4
│   ├── database/
│   │   ├── __init__.py        # Exporta la capa de datos
│   │   ├── session.py         # Motor y sesiones async (FASE 2)
│   │   └── repository.py      # RPC (FASE 2) + insertar_fragmentos (FASE 3)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── normativa.py       # Validación de coleccion_id (FASE 2)
│   │   ├── ingesta.py         # Petición/respuesta de la ingesta (FASE 3)
│   │   └── consulta.py        # Petición/respuesta de la consulta (FASE 4)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── downloader.py      # Scraping concurrente con httpx (FASE 3)
│   │   ├── embeddings.py      # Gemini batch + reintentos 429 (FASE 3)
│   │   ├── generacion.py      # Gemini generateContent a temp. 0.0 (FASE 4)
│   │   ├── ingesta.py         # Orquestación + métricas por fase (FASE 3)
│   │   ├── consulta.py        # Orquestación RAG + aislamiento (FASE 4)
│   │   ├── benchmark.py       # Benchmark de concurrencia (FASE 5)
│   │   └── graficas.py        # Series y PNG del benchmark (FASE 5)
│   └── utils/
│       ├── __init__.py
│       ├── html_cleaner.py    # Limpieza de HTML (FASE 3)
│       ├── chunking.py        # Troceado con overlap (FASE 3)
│       └── metricas.py        # Tiempos por fase (FASE 3)
├── config/                    # Archivos de configuración por ambiente
├── docker/
│   └── postgres/
│       └── init/              # Init de volumen NUEVO (solo primera vez)
├── migrations/                # Migraciones SQL versionadas (FASE 2)
│   ├── 001_pgvector.sql
│   ├── 002_tabla_normativa_bancaria.sql
│   └── 003_match_normativa_coleccion.sql
├── seeds/
│   └── aislamiento_demo.sql   # Datos de prueba (FASE 2)
├── scripts/
│   ├── check_pgvector.sql     # Comprobación de pgvector (solo lectura)
│   ├── migrate.sh             # Aplica migraciones pendientes (FASE 2)
│   ├── seed.sh                # Carga datos de prueba (FASE 2)
│   ├── verify_phase1.sh       # Verificación de la FASE 1
│   ├── verify_phase2.sh       # Verificación de la FASE 2
│   ├── benchmark_concurrencia.py  # CLI del benchmark (FASE 5)
│   └── graficar_benchmark.py      # Graficas T/S/E del benchmark (FASE 5)
├── benchmarks/
│   ├── urls_benchmark.json    # Dataset real verificada (5+5 URLs, FASE 5)
│   └── results/               # benchmark_resultados.json + resumen.csv (FASE 5)
├── tests/
│   ├── conftest.py            # Fixtures de solo lectura (FASE 2)
│   ├── helpers.py             # Colecciones y vectores de prueba (FASE 2)
│   ├── test_esquema.py        # Esquema, índice y RPC
│   ├── test_aislamiento.py    # Aislamiento entre colecciones
│   ├── test_validacion_coleccion.py
│   ├── test_api_coleccion.py  # Validación en la API (urllib, sin httpx)
│   ├── test_html_cleaner.py   # Limpieza de HTML (FASE 3, sin red)
│   ├── test_chunking.py       # Troceado y solape (FASE 3, sin red)
│   ├── test_validacion_ingesta.py  # Petición de ingesta y URL (FASE 3)
│   ├── test_embeddings.py     # Cliente Gemini con mocks (FASE 3)
│   ├── test_ingesta_aislamiento.py  # Inserción aislada vía rollback (FASE 3)
│   ├── test_consulta.py       # Consulta RAG con mocks puros (FASE 4)
│   ├── test_aislamiento_consulta.py  # Consulta contra RPC real (FASE 4)
│   └── test_benchmark.py      # Benchmark con mocks puros (FASE 5)
├── .env.example               # Plantilla de variables de entorno
├── .gitignore
├── Dockerfile                 # Python 3.12-slim
├── docker-compose.yml         # Servicios: backend + postgres
├── pytest.ini                 # Configuración de pytest (FASE 2)
├── requirements.txt
├── requirements-dev.txt       # Dependencias de pruebas (FASE 2)
└── README.md
```

## 6. Puesta en marcha

### 6.1 Requisitos

- Docker Engine
- Docker Compose v2

### 6.2 Preparar el entorno

```bash
cd rag-multidominio
cp .env.example .env
```

Edite `.env` y ajuste `POSTGRES_USER`, `POSTGRES_PASSWORD` y `DATABASE_URL`.
El archivo `.env` está en `.gitignore`: **no se versiona**.

> `DATABASE_URL` usa el host `postgres` porque es el nombre del servicio dentro
> de la red de Docker Compose. Fuera de Docker usaría `localhost`.

### 6.3 Iniciar

```bash
docker compose up --build -d
```

### 6.4 Detener

```bash
docker compose down          # conserva el volumen de datos
docker compose down -v       # elimina también el volumen (destructivo)
```

## 7. Comprobar FastAPI

Con los servicios levantados:

```bash
# Endpoint raíz
curl http://localhost:8000/

# Healthcheck
curl http://localhost:8000/health

# Documentación interactiva
# navegador -> http://localhost:8000/docs
```

Respuesta esperada de `GET /`:

```json
{
  "status": "online",
  "mensaje": "Pipeline RAG Multi-Dominio activo"
}
```

## 8. Comprobar PostgreSQL y pgvector

```bash
# Estado del servidor
docker compose exec postgres pg_isready -U rag_user -d rag_multidominio

# Versión de PostgreSQL
docker compose exec postgres psql -U rag_user -d rag_multidominio -c "SELECT version();"

# pgvector disponible en el servidor
docker compose exec postgres psql -U rag_user -d rag_multidominio \
  -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';"
```

También existe el script de comprobación:

```bash
docker compose exec -T postgres psql -U rag_user -d rag_multidominio < scripts/check_pgvector.sql
```

## 9. Verificación rápida de la FASE 1

```bash
./scripts/verify_phase1.sh
```

Ejecuta las comprobaciones de las secciones 7 y 8 en un solo paso.

## 9.1 Resultados de la verificación de la FASE 1

Verificación ejecutada sobre Docker 29.8.0 / Docker Compose v5.5.1 y
Python 3.12 dentro del contenedor.

| # | Prueba | Comando | Resultado |
| --- | --- | --- | --- |
| 1 | Configuración válida de Compose | `docker compose config --quiet` | ✔ Correcto (servicios: `postgres`, `backend`) |
| 2 | Construcción de la imagen del backend | `docker compose build backend` | ✔ Correcto (`rag-multidominio-backend:0.1.0`) |
| 3 | Arranque de ambos servicios | `docker compose up -d` | ✔ Ambos en estado `healthy` |
| 4 | FastAPI responde `GET /` | `curl http://localhost:8000/` | ✔ `HTTP 200` + JSON esperado |
| 5 | FastAPI responde `GET /health` | `curl http://localhost:8000/health` | ✔ `HTTP 200` |
| 6 | Rutas registradas | `curl http://localhost:8000/openapi.json` | ✔ Solo `/` y `/health` (aún sin endpoints de negocio) |
| 7 | PostgreSQL acepta conexiones | `docker compose exec postgres pg_isready -U rag_user -d rag_multidominio` | ✔ `accepting connections` |
| 8 | Versión de PostgreSQL | `docker compose exec postgres psql ... -c "SELECT version();"` | ✔ PostgreSQL 16.15 |
| 9 | pgvector disponible | `scripts/check_pgvector.sql` | ✔ `vector 0.8.7` listada en `pg_available_extensions` |
| 10 | Persistencia del volumen | `docker compose down` + `docker compose up -d` | ✔ Servicios unhealthy→healthy, datos conservados |

Sobre la prueba 9: en la base `rag_multidominio` la extensión **no está
instalada todavía** (`installed_version` vacío), tal y como corresponde a la
FASE 1. Para confirmar de forma concluyente que pgvector opera, se creó una
base de datos temporal, se instaló la extensión y se ejecutó una búsqueda por
distancia (`<->`), obteniendo los resultados esperados; esa base temporal se
eliminó después. `rag_multidominio` permanece sin tablas.

## 10. FASE 2 — aislamiento vectorial multi-dominio

### 10.1 Qué implementa esta fase

| Elemento | Detalle |
| --- | --- |
| Tabla | `normativa_bancaria` con `coleccion_id VARCHAR(50) NOT NULL` |
| Índice | B-tree `idx_normativa_bancaria_coleccion_id` sobre `coleccion_id` |
| Restricción | `normativa_bancaria_coleccion_id_no_vacia` (no admite cadenas en blanco) |
| Embeddings | `embedding VECTOR(768) NOT NULL`, compatible con `PGVECTOR_DIMENSION` |
| RPC | `match_normativa_coleccion(p_coleccion_id, p_query_embedding, p_match_threshold, p_match_count)` |
| API | `GET /api/colecciones` y `GET /api/colecciones/{coleccion_id}` (diagnóstico) |

**Por qué `p_coleccion_id` va primero y sin valor por defecto:** PostgreSQL
exige que todo parámetro situado después de uno con `DEFAULT` tenga también
`DEFAULT`. Además, al no tenerlo, la función *no puede invocarse sin
indicar la colección*: un default (por ejemplo `NULL`) permitiría una llamada
incompleta que devolvería cero filas en silencio.

**Cómo está garantizado el aislamiento.** La función filtra por
`coleccion_id` en el `WHERE`, es decir **antes** del `ORDER BY` y del
`LIMIT`:

```sql
FROM normativa_bancaria AS nb
WHERE nb.coleccion_id = p_coleccion_id          -- aislamiento obligatorio
  AND 1 - (nb.embedding <=> p_query_embedding) >= p_match_threshold
ORDER BY nb.embedding <=> p_query_embedding
LIMIT p_match_count;
```

Por eso `financiero_bolivia_2026` jamás devuelve registros de
`tributario_bolivia_2026`, ni siquiera con un umbral tan permisivo que
aceptaría todas las filas de la base de datos. El índice B-tree sobre
`coleccion_id` es lo que permite descartar el resto de colecciones antes de
calcular distancias.

**Semántica del umbral.** `p_match_threshold` se expresa como *similitud*
mayor es mejor: `similitud = 1 - distancia_coseno`. El filtro
`similitud >= umbral` equivale exactamente a `distancia <= 1 - umbral`.

### 10.2 Migraciones

> Los scripts de `docker/postgres/init/` **solo se ejecutan cuando Docker
> crea un volumen nuevo**. El volumen `rag_multidominio_pgdata` ya existe, así
> que añadir ahí un script no aplicaría nada —y `docker compose down -v` lo
> borraría. Por eso el esquema vive en `migrations/` y se aplica con un runner.

```bash
# Aplica las migraciones pendientes (idempotente, sin tocar el volumen)
./scripts/migrate.sh

# Solo muestra el estado, sin modificar nada
./scripts/migrate.sh --status
```

Cada migración queda registrada en la tabla `schema_migrations` dentro de una
transacción junto con su registro: si el SQL falla, la versión no se registra
y puede reintentarse. Volver a ejecutar el script no produce cambios.

Las migraciones aplicadas son:

| Versión | Contenido |
| --- | --- |
| `001_pgvector` | `CREATE EXTENSION IF NOT EXISTS vector` |
| `002_tabla_normativa_bancaria` | Tabla, índice B-tree y restricción de no vacío |
| `003_match_normativa_coleccion` | Función RPC de búsqueda vectorial |

### 10.3 Datos de prueba

```bash
# Ya están cargados. Solo volvería a ejecutarse si faltaran datos.
./scripts/seed.sh
```

Crea 4 fragmentos en `financiero_bolivia_2026` y 4 en
`tributario_bolivia_2026`, situados en direcciones distintas del espacio
vectorial (componente 1 y componente 2). Es idempotente: **no** inserta nada si
la colección ya tiene filas.

### 10.4 Pruebas automatizadas

Las pruebas corren **dentro del contenedor del backend** y son de solo
lectura: no insertan ni modifican registros, y exigen además que los datos de
demostración sigan con el tamaño esperado (4 y 4).

```bash
# Opción recomendada: todo en un paso
./scripts/verify_phase2.sh

# Opción manual
docker compose exec -T backend python -m pip install -q -r requirements-dev.txt
docker compose exec -T backend python -m pytest -q
```

| Fichero | Comprueba |
| --- | --- |
| `tests/test_esquema.py` | PostgreSQL, pgvector, `coleccion_id`, índice B-tree, firma de la RPC |
| `tests/test_aislamiento.py` | Ninguna consulta cruza de colección; no hay contaminación |
| `tests/test_validacion_coleccion.py` | `coleccion_id` se valida antes de tocar la base de datos |
| `tests/test_api_coleccion.py` | La API valida `coleccion_id` y la FASE 1 sigue intacta |

Para ejecutarlas fuera del contenedor hay que exportar `TEST_DATABASE_URL`
apuntando a `localhost` (el puerto 5432 ya está publicado):

```bash
TEST_DATABASE_URL="postgresql+asyncpg://rag_user:<clave>@localhost:5432/rag_multidominio" \
    python -m pytest -q
```

### 10.5 Verificación de la FASE 2

```bash
./scripts/verify_phase2.sh
```

Comprueba en orden: PostgreSQL, pgvector, `coleccion_id`, índice B-tree,
`match_normativa_coleccion`, aislamiento entre colecciones, que los datos de
demostración siguen intactos (4 y 4) y que todas las pruebas de pytest pasan
(183 en la FASE 5).

Si el binario `docker` no está en el `PATH`, se puede indicar con
`DOCKER_CMD=/ruta/a/docker ./scripts/verify_phase2.sh`.

### 10.6 Endpoints disponibles

```bash
curl http://localhost:8000/api/colecciones
# [{"coleccion_id":"financiero_bolivia_2026","fragmentos":4},
#  {"coleccion_id":"tributario_bolivia_2026","fragmentos":4}]

curl http://localhost:8000/api/colecciones/financiero_bolivia_2026
# {"coleccion_id":"financiero_bolivia_2026","existe":true,"fragmentos":4}

curl -i http://localhost:8000/api/colecciones/con%20espacio
# HTTP/1.1 400  {"detail":"coleccion_id solo admite letras, digitos, '_', '-' y '.'"}
```

Un `coleccion_id` válido se rechaza con **400** cuando supera los 50
caracteres, lleva espacios inicial/final, interiores o caracteres fuera de
`[A-Za-z0-9_.-]`. Una colección válida pero inexistente devuelve
`"existe": false, "fragmentos": 0`.

> Estos endpoints son **solo diagnóstico**. La consulta RAG
> (`POST /api/consultar`) y la ingesta (`POST /api/ingestar-web`) se
> implementaron en las fases 3 y 4, respectivamente.

## 11. Desarrollo por fases

> **Nota sobre la numeración:** el aislamiento vectorial estaba previsto
> originalmente como Fase 4, pero se ha adelantado y entregado como **Fase 2**
> porque es la base sobre la que se apoyan las consultas RAG. El resto de
> fases desplazan su número en consecuencia.

| Fase | Contenido | Estado |
| --- | --- | --- |
| **1** | Infraestructura base: FastAPI, Docker, PostgreSQL + pgvector | **Completada** |
| **2** | **Aislamiento vectorial: `normativa_bancaria`, índice B-tree, RPC `match_normativa_coleccion`, validación de `coleccion_id` y pruebas** | **Completada** |
| **3** | **Ingesta web completa: scraping concurrente (asyncio/httpx), limpieza HTML, *chunking* con overlap, embeddings Gemini por lotes con manejo de `429`, e `POST /api/ingestar-web`** | **Completada** |
| 4 | **Endpoint `POST /api/consultar` con aislamiento por colección** | **Completada** |
| 5 | **Comparación secuencial/concurrente y cálculo de T1, Tp, Speedup, Efficiency (benchmark reproducible + graficas + Amdahl)** | **Completada** |
| 6 | Interfaz Streamlit | Pendiente |

En la FASE 5 **no** se implementa Streamlit (FASE 6). El benchmark es **real y
reproducible** sobre una carga idéntica: las corridas afectadas por límites de
Gemini (`429`) se marcan `RATE_LIMITED` y nunca se presentan como métricas.

## 12. FASE 3 — ingesta web concurrente

### 12.1 Qué implementa esta fase

`POST /api/ingestar-web` completa el pipeline de ingesta:

| Paso | Módulo | Detalle |
| --- | --- | --- |
| Descarga concurrente | `app/services/downloader.py` | `asyncio` + `httpx.AsyncClient` con `Semaphore`; timeouts, saltos de red, URLs inválidas y contenido vacío tratados *por URL*, sin abortar la ingesta |
| Limpieza HTML | `app/utils/html_cleaner.py` | `html.parser` de la stdlib: elimina `script`, `style`, `noscript`, `nav`, `header`, `footer`, formularios y comentarios; normaliza espacios |
| *Chunking* | `app/utils/chunking.py` | ventana deslizante con `chunk_size` / `chunk_overlap`; validación (`size>0`, `overlap>=0`, `overlap<size`); evita fragmentos vacíos |
| Embeddings | `app/services/embeddings.py` | `models/gemini-embedding-001` vía `:batchEmbedContents`, `outputDimensionality=768`, lotes de hasta 100, reintentos finitos ante 429 / `RESOURCE_EXHAUSTED` con backoff exponencial + jitter y tope |
| Inserción | `app/database/repository.insertar_fragmentos` | estampa `coleccion_id` una sola vez (aislamiento por construcción); sin commit para permitir transacciones controladas |
| Orquestación | `app/services/ingesta.py` | encadena las 4 fases y cronometra cada etapa con `time.perf_counter` |

### 12.2 Cuerpo de la petición

```json
{
  "coleccion_id": "financiero_bolivia_2026",
  "urls": ["https://ejemplo.com/norma"],
  "chunk_size": 1000,
  "chunk_overlap": 150,
  "concurrency_workers": 4
}
```

Reglas: `coleccion_id` no vacío, ≤ 50 caracteres y caracteres ASCII; `urls`
1..50 elementos http/https; `chunk_size` > 0; `chunk_overlap` ≥ 0 y <
`chunk_size`; `concurrency_workers` 1..64.

### 12.3 Respuesta

Incluye `coleccion_id`, `urls_recibidas`, `urls_procesadas`, `urls_con_error`,
`errores`, `fragmentos_generados`, `embeddings_generados`,
`registros_insertados` y los tiempos por fase (`tiempo_scraping_s`,
`tiempo_chunking_s`, `tiempo_embeddings_s`, `tiempo_insercion_s`) más el
`tiempo_total_s`.

### 12.4 Configuración (`config.py` + `.env.example`)

`GEMINI_API_KEY` (sin clave real en el repositorio), `GEMINI_EMBEDDING_MODEL`
(`gemini-embedding-001`), `GEMINI_EMBEDDING_DIMENSION` (768),
`GEMINI_BATCH_SIZE`, `GEMINI_MAX_REINTENTOS`, `GEMINI_BACKOFF_BASE_S`,
`GEMINI_BACKOFF_MAX_S`, `GEMINI_TIMEOUT_S`, `HTTP_TIMEOUT_S`,
`HTTP_MAX_CONCURRENCIA`, `HTTP_MAX_BYTES`, `CHUNK_SIZE_POR_DEFECTO`,
`CHUNK_OVERLAP_POR_DEFECTO` y `URLS_POR_PETICION_MAX`.

### 12.5 Pruebas

Unitarias sin red (`test_html_cleaner`, `test_chunking`, `test_validacion_ingesta`) y
de red simulada con `httpx.MockTransport` (`test_embeddings`: lote, `429`,
backoff, errores definitivos). `test_ingesta_aislamiento` verifica que la
inserción estampa `coleccion_id` y que, al hacer rollback, las 8 filas de la
FASE 2 permanecen intactas (4 + 4).

## 13. FASE 4 — consulta RAG multi-dominio

### 13.1 Qué implementa esta fase

`POST /api/consultar` ejecuta la consulta RAG manteniendo el aislamiento por
`coleccion_id`:

| Paso | Módulo | Detalle |
| --- | --- | --- |
| Validación | `app/models/consulta.py` | `coleccion_id` (1..50, ASCII, Pydantic + dominio), `pregunta` no vacía (≤ 2000 caracteres), `top_k` (1..50), `similarity_threshold` ([-1, 1]) |
| Embedding de la pregunta | `app/services/embeddings.py` (reutilizado) | mismo `ClienteEmbeddingsGemini` de FASE 3: `gemini-embedding-001` a 768 dims |
| Recuperación | `app/database/repository.buscar_por_coleccion` | RPC `match_normativa_coleccion` con `coleccion_id`, `query_embedding`, `match_threshold` y `match_count` — ninguna consulta basada en distancia fuera de la RPC |
| Aislamiento estricto | `app/services/consulta.py` | si el RPC devolviera UNA fila ajena a la colección pedida, se aborta con `ErrorContaminacion` sin llegar nunca al LLM |
| Generación | `app/services/generacion.py` | `ClienteGeneracionGemini`: `v1beta/models/{modelo}:generateContent`, `generationConfig.temperature = 0.0`, reintentos finitos 429 / `RESOURCE_EXHAUSTED` igual que FASE 3 |
| Orquestación | `app/services/consulta.py` | encadena y ensambla la `RespuestaConsulta` |

### 13.2 Cuerpo de la petición

```json
{
  "coleccion_id": "financiero_bolivia_2026",
  "pregunta": "¿Qué entidad regula el sistema financiero boliviano?",
  "top_k": 4,
  "similarity_threshold": 0.5
}
```

`top_k` y `similarity_threshold` son opcionales (por defecto 4 y 0.5).

### 13.3 Respuesta

```json
{
  "coleccion_id": "financiero_bolivia_2026",
  "pregunta": "¿Qué entidad regula el sistema financiero boliviano?",
  "respuesta": "...",
  "evidencias": [
    {
      "id": 1,
      "coleccion_id": "financiero_bolivia_2026",
      "origen_url": "https://...",
      "similitud": 0.96,
      "contenido": "..."
    }
  ],
  "total_evidencias": 4
}
```

Si no hay evidencia sobre el umbral, el LLM **no** se invoca: `respuesta` es un
mensaje controlado y `total_evidencias = 0`.

### 13.4 Configuración (`config.py` + `.env.example`)

`GEMINI_MODEL` (`gemini-2.0-flash`, modelo de generación),
`GEMINI_TEMPERATURA` (`0.0`), `TOP_K_POR_DEFECTO` (4), `TOP_K_MAX` (50),
`SIMILARITY_THRESHOLD_POR_DEFECTO` (0.5) y
`CONSULTA_PREGUNTA_MAX_CARACTERES` (2000).

### 13.5 Pruebas

`test_consulta.py` cubre el flujo con mocks puros (validación, args exactos de
la RPC, contaminación rechazada, sin evidencia sin LLM, temperatura 0.0 y
errores → 502). `test_aislamiento_consulta.py` ejecuta la consulta contra la
**RPC real** con los vectores de los seeds (financiero/tributario) y comprueba
que cada pregunta devuelve solo su colección, dejando los 8 registros (4 + 4)
intactos.

## 14. FASE 5 — benchmark de concurrencia (T1, T_p, Speedup, Efficiency)

### 14.1 Qué implementa esta fase

Reproduce el pipeline real de la FASE 3 (descarga → chunking → embeddings
Gemini → inserción) variando **solo** la concurrencia `p`, sobre una **carga
idéntica** (mismas URLs, mismo `chunk_size`/`chunk_overlap`, misma colección,
mismo `batch_size`), y mide cada tramo con `time.perf_counter` (reloj
monotónico).

| Pieza | Módulo | Detalle |
| --- | --- | --- |
| Núcleo | `app/services/benchmark.py` | `ejecutar_benchmark` con dependencias inyectables (`descargar`, `emb_client`, `abrir`) para pruebas sin red/Gemini/BD |
| Hook observacional | `app/services/embeddings.py` | `historico_llamadas`: cada llamada `batchEmbedContents` queda como `(status, fue_limitación)`; no altera la FASE 3 |
| Dataset | `benchmarks/urls_benchmark.json` | 10 URLs reales de organismos bolivianos (5 `financiero`, 5 `tributario`), verificadas HTTP 200 |
| CLI | `scripts/benchmark_concurrencia.py` | ejecuta el benchmark y escribe `benchmark_resultados.json` + `benchmark_resumen.csv` |
| Graficas | `app/services/graficas.py` + `scripts/graficar_benchmark.py` | PNG de tiempo, speedup y eficiencia (matplotlib, solo datos COMPLETED) |

### 14.2 Estados de cada ejecución

| Estado | Significado |
| --- | --- |
| `COMPLETED` | Lote ingerido sin fallos **ni** 429: carga idéntica, entra en T/S/E |
| `PARTIAL` | Hubo URL(s) fallida(s): la carga ya NO es comparable, no entra en métricas |
| `FAILED` | Fallo definitivo no relacionado con límite de Gemini |
| `RATE_LIMITED` | Afectada por `429`/`RESOURCE_EXHAUSTED` (modo estricto). Aunque Gemini recuperara la llamada, la corrida **no** cuenta como válida y **no** inserta nada |

Solo las ejecuciones `COMPLETED` alimentan `T_p`, `S_p` y `E_p`. Los eventos
429 **nunca se ocultan**: quedan en `eventos_429`, `reintentos` y en la marca
`afectado_429` del resumen. `--modo-429 permitir` existe solo para diagnóstico
(completa insertando pero conserva el contador de eventos).

### 14.3 Ejecución (dentro del contenedor)

```bash
# 1. El benchmark está en la imagen del backend (COPY en el Dockerfile).
docker compose exec -T backend python scripts/benchmark_concurrencia.py \
    --coleccion-id financiero_bolivia_2026 \
    --urls-file benchmarks/urls_benchmark.json \
    --categoria todas \
    --workers 1 2 4 8 \
    --repeticiones 1 \
    --chunk-size 1200 --chunk-overlap 200
```

Escribirá (por defecto) en `benchmarks/results/` dentro del contenedor:

- `benchmark_resultados.json` — ejecuciones completas (URLs, counters,
  tiempos por fase, porcentajes por etapa) + resumen agregado.
- `benchmark_resumen.csv` — `workers | tiempo_total_s | speedup | eficiencia |
  estado | afectado_429 | T1_base_s`.

Gráficas (tras instalar dev deps; matplotlib se usa solo para analizar):

```bash
docker compose exec -T backend python -m pip install -q -r requirements-dev.txt
docker compose exec -T backend python scripts/graficar_benchmark.py
```

Genera `grafica_tiempo_workers.png`, `grafica_speedup_workers.png` y
`grafica_eficiencia_workers.png` con **solo** las ejecuciones `COMPLETED`
(si falta `T1` o una `T_p`, la serie se omite y se avisa en pantalla).

> La corrida real inserta los fragmentos en la `coleccion_id` elegida y hace
> commit: medir esa inserción es el objetivo. Se recomienda usar una colección
> dedicada (p. ej. `benchmark_2026`) para no ensuciar los datos de demo
> (4 + 4 de la FASE 2).

### 14.4 Métricas y Ley de Amdahl

Para cada nivel de concurrencia `p` con carga idéntica:

```
T_p   = tiempo medio de las corridas COMPLETED con p workers
S_p   = T1 / T_p                 (speedup)
E_p   = S_p / p                  (eficiencia, ideal = 1.0)
```

`T1` es la media de las corridas COMPLETED con `workers=1`. Si no existe,
**no** se calculan `S_p` ni `E_p` para ningún nivel (nunca números engañosos).

La **Ley de Amdahl** explica el límite esperado del speedup:

```
S_max = 1 / ( (1 - f_paralela) + f_paralela / p )
```

donde `f_paralela` es la fracción del tiempo que se acelera con la
concurrencia. En este pipeline:

- **Scraping**: paralelizable (Semaphore → más workers = menores `tiempo_scraping_s`).
- **Embeddings Gemini**: se serializa impuesto por la cuota de la API; un
  `429` invalida la corrida (modo estricto), de modo que `E_p` refleja cuánto
  paralelismo real se aprovechó sobre la porción serial de la cuota.
- **Chunking e inserción**: porciones pequeñas y acotadas que, a estos
  tamaños, contribuyen poco a `T_total`.

El benchmark mide cada tramo por separado (`porcentajes_por_etapa`) para
poder estimar `f_paralela` con las mediciones reales; la interpretación
cuantitativa (valor de `f_paralela`, curva S vs. p) queda para el informe
final con corridas reales.

### 14.5 Pruebas

`tests/test_benchmark.py` (31 tests) usa **mocks puros** — sin red, sin
Gemini, sin base de datos — e inyecta dobles para descarga, embeddings
(incluido `historico_llamadas`) e inserción. Cubre: validación de `workers`,
propagación de la concurrencia a la descarga, coherencia de conteos
(`recibidas = procesadas + error`; `fragmentos = embeddings = insertados`),
timers por fase, estados `COMPLETED/PARTIAL/FAILED/RATE_LIMITED`, speedup y
eficiencia con y sin `T1`, promedio de repeticiones descartando inválidas,
porcentajes por etapa, estructura/JSON del resultado, carga del dataset y
helpers de graficas. La suite completa es `183 passed`.

### 14.6 Estado real del benchmark

Hasta esta entrega la FASE 5 queda **implementada y verificada con mocks**;
las métricas T1–T8 **no** se han medido aún en producción porque dependen de
correr el pipeline real (red + cuota de Gemini) por unos minutos. El comando
de la sección 14.3 es el único paso pendiente, y sus resultados son
reproducibles. Además, la variable `GEMINI_MODEL` del entorno apunta a un
modelo de generación ya retirado upstream (`gemini-2.0-flash`); el de
embeddings (`gemini-embedding-001`) funciona y es el que usa el benchmark. La
solución es actualizar `GEMINI_MODEL` a `gemini-3.8-flash` en el entorno (no
se ha editado `.env` para no alterar fases anteriores).

### 14.7 Modo `--solo-scraping` (medición aislada de la descarga)

Como la cuota de Gemini puede agotarse (`429`), existe un modo que mide
**únicamente la descarga concurrente** y **retorna antes** de chunking,
embeddings e inserción. No llama a Gemini ni abre sesión de PostgreSQL:

```bash
docker compose exec -T backend python scripts/benchmark_concurrencia.py \
    --solo-scraping \
    --urls-file benchmarks/urls_benchmark.json \
    --categoria todas \
    --workers 1 2 4 8
```

Detalles del contrato:

- La **carga comparable** es la lista ORDENADA de URLs (`hash_carga` se calcula
  sobre las URLs, no sobre el texto), porque el contenido web varía entre
  corridas y no debe invalidar la comparación de scraping.
- Una ejecución con **errores de descarga** queda en `PARTIAL`, **nunca**
  `COMPLETED`; por tanto no entra en `T/S/E`.
- `S_p`/`E_p` solo se calculan entre ejecuciones `COMPLETED` con la **misma
  lista de URLs** y condiciones comparables.
- Los resultados se guardan en archivos **separados** para no sobrescribir el
  benchmark integral: `benchmark_scraping_resultados.json` y
  `benchmark_scraping_resumen.csv`.
- Al no requerir colección destino, `--coleccion-id` es opcional (usa
  `benchmark_scraping` por defecto).

El benchmark integral (sección 14.3) y el modo `--corpus` (14.4) **no
cambian**: `solo_scraping` está desactivado por defecto y es incompatible con
`--corpus`.