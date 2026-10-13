# Validación con datos normativos reales (Bolivia)

## 1. Objetivo

Convertir el pipeline RAG multidominio en un sistema que responda a partir de
**fuentes normativas reales y verificables** de Bolivia:

- **Dominio financiero:** ASFI, BCB, BDP, FNDR, UDAPE.
- **Dominio tributario:** SIN, Aduana Nacional, portal del Estado, Presidencia, INE.

La meta es que el chat conversacional deje de apoyarse en datos sintéticos de
prueba y razone sobre documentos oficiales descargados de la web (HTML y PDF).

> **Alcance de este documento.** Describe cómo se **pueblan** y **prueban** las
> colecciones. No demuestra que el corpus cubra las normas necesarias ni que las
> respuestas jurídicas sean correctas. Cuatro cosas distintas que conviene no
> confundir: (a) una **URL institucional** que responde 200; (b) un **documento
> oficial** servido por esa URL; (c) una **norma vigente y contenido comprobado**
> (no verificado aquí); (d) un **fragmento recuperado** por el RAG. La cobertura
> normativa **no está completa**.

## 2. Estado verificado de la base de datos

Consulta de solo lectura sobre `normativa_bancaria` (no se modificó nada):

| Colección | Fragmentos | Con `origen_url` real | Semilla (`seed://`) |
|---|---:|---:|---:|
| `benchmark_2026` | 51 | 51 | 0 |
| `financiero_bolivia_2026` | 10 | 6 | 4 |
| `tributario_bolivia_2026` | 19 | 15 | 4 |

> **Importante sobre `benchmark_2026`.** Es una colección de **benchmark** (medición
> de concurrencia/rendimiento), no una colección normativa de producción. Sus 51
> fragmentos tienen `origen_url` real, pero **no deben considerarse automáticamente
> evidencia normativa validada** para responder consultas jurídicas. La columna
> «Con `origen_url` real» significa "texto descargado de una URL http(s)", **no**
> "norma vigente comprobada".

- pgvector `0.8.7`, columna `embedding VECTOR(768)`.
- Función `match_normativa_coleccion` presente; índice `idx_normativa_bancaria_coleccion_id`.
- Esquema: `id, coleccion_id, contenido, embedding, origen_url, created_at`.

**Causa raíz de la "falta de evidencia" (resuelta parcialmente):** las colecciones
financiera y tributaria contenían únicamente 4 vectores sintéticos cada una (ejes
ortogonales); las preguntas reales nunca alcanzaban el umbral. Se incorporó un lote
pequeño de **páginas institucionales** (ver §4.1). Esto demuestra que el *pipeline*
recupera y responde, **no** que exista cobertura suficiente de leyes y reglamentos;
de hecho, el lote son portales de navegación, no texto normativo denso.

## 3. Datos de prueba (`seed://`) y su exclusión

Las filas de semilla tienen `origen_url` con prefijo `seed://`. El sistema las
trata explícitamente como **datos de prueba**:

- `app/services/consulta.py::es_evidencia_de_prueba` las marca con
  `es_datos_prueba = true` en la respuesta.
- `app/services/chat.py` **nunca invoca al LLM** si la única evidencia es
  `seed://`; devuelve un aviso (`ADVERTENCIA_SEED_UNICA`).
- Si hay mezcla de evidencia real y de prueba, solo la evidencia real entra al
  prompt y se emite `ADVERTENCIA_SEED_PARCIAL`.

Las filas `seed://` **no se eliminan** hasta que haya documentos reales
ingeridos, verificados y con autorización explícita.

## 4. Fuentes reales de ingesta

El dataset ya presente en el repositorio (`benchmarks/urls_benchmark.json`)
contiene 10 URLs institucionales reales verificadas (GET/HEAD 200):

- Financiero: `bcb.gob.bo`, `asfi.gob.bo`, `bdp.com.bo`, `fndr.gob.bo`, `udape.gob.bo`.
- Tributario: `impuestos.gob.bo`, `aduana.gob.bo`, `gob.bo`, `presidencia.gob.bo`, `ine.gob.bo`.

Son portales raíz. Para normativa específica se recomienda reemplazar el path
por el de la resolución/ley concreta **dentro del mismo dominio**, verificando
primero que la URL responda 200. No se inventan URLs ni contenido normativo.

### 4.1 Ingesta real ejecutada (lote pequeño)

Con autorización explícita se ingirió un lote pequeño sobre las colecciones de
demostración (solo inserción; las filas `seed://` **no** se tocaron). Las URLs son
**páginas raíz de portales institucionales**, no leyes ni reglamentos:

| Dominio | URL | HTTP | Fragmentos | Título extraído |
|---|---|---|---:|---|
| financiero | `https://www.asfi.gob.bo/` | 200 | 3 | `¡Bienvenidos! | ASFI` |
| financiero | `https://www.bcb.gob.bo/` | 200 | 3 | `Banco Central de Bolivia` |
| tributario | `https://www.impuestos.gob.bo/` | 200 | 2 | `Impuestos - SIN` |
| tributario | `https://www.aduana.gob.bo/` | 200 | 13 | `Inicio | Aduana Nacional` |

Consumo total: **21 embeddings** (`gemini-embedding-001`). Resultado: 6
fragmentos reales en financiero y 15 en tributario.

> **Advertencia de cobertura.** Estos fragmentos provienen de **páginas de inicio**
> (navegación, avisos, noticias), no de texto normativo. Que el chat responda con
> ellos **no demuestra** cobertura suficiente de leyes y reglamentos: solo valida
> que el pipeline descarga, limpia, embebe, recupera y cita. Para respuestas
> jurídicas fiables hay que ingerir los documentos normativos concretos (§4).

Verificación end-to-end con `POST /api/consultar-auto` (generación real):

- «¿Quién regula y supervisa el sistema financiero en Bolivia?» → dominio
  `financiero`, evidencia real de `asfi.gob.bo` (similitud ≈ 0.72),
  `es_datos_prueba: false`, respuesta fundamentada y con salvedad de contexto.
- «¿Qué competencias tiene la Aduana Nacional en materia de importaciones?» →
  dominio `tributario`, evidencia real de `aduana.gob.bo`, sin advertencia de
  datos de prueba.

En ambos casos la evidencia semilla (similitud ≈ 0 con el vector real de la
pregunta) quedó por debajo del umbral, por lo que no se usó ni se advirtió.

**Corrección de modelo:** el modelo de generación configurado
(`gemini-2.0-flash`) estaba retirado upstream (HTTP 404). Se actualizó
`GEMINI_MODEL` a `gemini-3.8-flash` (recomendación explícita del propio error de
Google y de la nota de la sección 14.6 del README). El modelo de embeddings
(`gemini-embedding-001`) no cambió.

## 5. Cambios implementados

### 5.1 Descarga y extracción HTML + PDF

- `app/services/downloader.py`
  - `DocumentoDescargado` (url, texto, titulo, mime, paginas).
  - Detección de PDF por firma binaria (`%PDF-`) o `Content-Type`, tolerante a
    prefijos; rechazo de contenido binario no soportado.
  - `_leer_bytes_con_limite` limita el tamaño antes de decodificar.
  - Parametro `transporte` (inyectable en pruebas con `httpx.MockTransport`).
- `app/services/pdf_extractor.py` (nuevo)
  - `detectar_pdf`, `parece_binario`, `extraer_texto_pdf` -> `(texto, paginas)`.
  - `PdfInvalido` / `PdfSinTexto` para PDF corrupto o escaneado sin texto.
- `requirements.txt`: `pypdf==5.1.0`.

### 5.2 Metadatos de ingesta

- `app/models/ingesta.py`: `DocumentoIngestado` y `RespuestaIngesta.documentos`.
- `app/services/ingesta.py`: fragmentos por URL y construcción de documentos.

### 5.3 Clasificación de dominio y chat automático

- `app/services/clasificacion.py` (nuevo): clasificador determinista por
  palabras clave; marca como ambiguo el empate y como fuera de dominio la
  ausencia de señales.
- `app/services/chat.py` (nuevo): `ejecutar_consulta_auto` enruta a la colección
  financiera o tributaria, o pide aclaración.
- `app/api/v1/chat.py` (nuevo): `POST /api/consultar-auto`.
- `app/core/config.py`: `coleccion_financiera`, `coleccion_tributaria`.
- `app/models/consulta.py`: `PeticionConsultaAuto`, `RespuestaConsultaAuto`.

### 5.4 Interfaz web

- `app/static/chat.html` + `GET /chat` (`app/main.py`): selector de dominio
  (automático/financiero/tributario), consulta manual por colección, avisos de
  datos de prueba y aclaraciones.

### 5.5 Cuota diaria de Gemini

- `app/services/embeddings.py`: ante un agotamiento de **cuota diaria** se
  falla rápido (`ErrorEmbeddings`, sin reintentos) para no consumir más cuota.

## 6. Qué está probado y qué no

| Elemento | Estado | Cómo se verificó |
|---|---|---|
| Extracción PDF/HTML | Probado (offline) | `tests/test_pdf_extractor.py`, `tests/test_downloader_documentos.py` (MockTransport) |
| Clasificación de dominio | Probado (offline) | `tests/test_clasificacion.py` |
| Chat automático y exclusión de `seed://` | Probado (offline) | `tests/test_consulta_auto.py` |
| Endpoint `/api/consultar-auto` | Probado (offline) | `tests/test_api_consultar_auto.py` |
| Interfaz `/chat` | Probado (en proceso) | `tests/test_chat_web.py` |
| Cuota diaria sin reintentos | Probado (offline) | `tests/test_embeddings.py` |
| **Ingesta real con Gemini** | **Probada (lote pequeño)** | 4 URLs, 21 embeddings, HTTP 200 (§4.1) |
| **Chat end-to-end con datos reales** | **Probado** | 2 consultas reales vía `/api/consultar-auto` (§4.1) |

> **Lectura correcta de esta tabla.** «Probado» significa que el mecanismo funciona
> y no hay regresión, **no** que el sistema cubra la normativa ni que responda
> correctamente cualquier consulta jurídica. La ingesta real fueron páginas de
> inicio institucionales (§4.1); la cobertura de leyes y reglamentos **no** está
> demostrada.

Suite completa: **288 pruebas ejecutadas en el contenedor** `rag_backend`
(`python -m pytest -q`), todas en verde. (La cifra creció desde las 267 de la
primera redacción al añadirse pruebas de idempotencia, chat y previsualización;
no cambia ninguna conclusión.) Las pruebas que dependen del tamaño de
las colecciones de demostración se ajustaron para tolerar documentos reales
coexistiendo con las filas semilla (§6.1).

### 6.1 Ajuste de pruebas por coexistencia real + semilla

Las pruebas de aislamiento y conteo de FASE 2/3 asumían que las colecciones de
demostración tenían exactamente 4 filas. Tras la ingesta real pasaron a
contener 4 semilla + N reales. Se actualizaron para:

- exigir el tamaño **exacto** solo del subconjunto `seed://` (4 por colección);
- exigir el total como **`>=`** del tamaño semilla;
- conservar intactas las garantías de aislamiento (una consulta a una colección
  nunca devuelve filas de otra; el rollback no deja rastro).

No se debilitó ninguna garantía: solo se dejó de fijar un total que ya no es
constante por diseño.

## 7. Limitaciones conocidas

1. **Metadatos no persistidos:** el esquema actual (`normativa_bancaria`) no
   guarda `titulo`, `mime` ni `paginas`. Se conservan a nivel de la respuesta de
   ingesta. Se propone una migración aditiva (columnas anulables) sin aplicarla
   todavía para no alterar el esquema congelado.
2. **Clasificación por palabras clave:** es determinista y explicable, pero
   puede no cubrir todo el vocabulario normativo. En empate o duda se pide
   aclaración en lugar de adivinar.
3. **Un solo modelo de embeddings (768 dims):** cambiar de modelo requeriría
   re-ingerir todas las colecciones.
4. La ingesta real consume cuota de Gemini y depende de la red; solo se ha
   ejecutado un lote pequeño (4 URLs). Ampliar la cobertura (resoluciones, leyes
   y PDF específicos) es incremental y también consume cuota.
5. **Cobertura temática parcial:** los portales raíz aportan principalmente
   navegación y notas; para respuestas normativas densas conviene ingerir
   documentos concretos (resoluciones/leyes) del mismo dominio.
6. **Contenido con fecha:** los portales cambian; las respuestas reflejan lo
   descargado en la fecha de ingesta (§4.1).

## 8. Reproducción

```bash
# Reconstruir el backend (instala pypdf) sin tocar el volumen de datos
docker compose build backend
docker compose up -d backend

# Dependencias de prueba (convención del proyecto: fuera de la imagen)
docker compose exec -T backend python -m pip install -r requirements-dev.txt

# Suite completa
docker compose exec -T backend python -m pytest -q

# Interfaz de chat
#   http://localhost:8000/chat
```

## 9. Procedimiento para ampliar la ingesta real (requiere autorización)

El procedimiento se ejecutó para un lote pequeño (§4.1). Para ampliarlo:

1. Verificar que cada URL responda HTTP 200 en la fecha de ejecución.
2. Ingerir en lotes pequeños por dominio (financiero y tributario) con
   `POST /api/ingestar-web`, observando el consumo de cuota.
3. Confirmar por colección que aumentó el número de fragmentos reales y que las
   consultas devuelven evidencia con `es_datos_prueba: false`.
4. Verificar aislamiento: consultas de un dominio no devuelven fragmentos del
   otro.
5. Solo entonces, evaluar la retirada de las filas `seed://` (previa
   autorización explícita). **Aún no se retiró ninguna semilla.**

**Estado:** la ingesta real está habilitada por lotes pequeños. Ampliarla
consume cuota de Gemini, por lo que sigue requiriendo autorización explícita.
