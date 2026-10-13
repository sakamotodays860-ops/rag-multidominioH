# Recuperación híbrida (vectorial + léxica)

Este documento registra el diagnóstico y el diseño de la mejora de recuperación
del chat RAG: **búsqueda léxica nativa de PostgreSQL** y **fusión híbrida** con
la búsqueda vectorial existente, sin consumir cuota de Gemini ni modificar el
esquema de la base de datos.

## 1. Diagnóstico

- La única vía de recuperación era la búsqueda vectorial
  (`match_normativa_coleccion`, pgvector, distancia coseno). Un identificador
  literal (p. ej. `R.D. N° 138/2026`) puede quedar lejos en el espacio de
  embeddings y no recuperarse.
- PostgreSQL 16.15 de este proyecto **solo** tiene las extensiones `plpgsql` y
  `vector`: **no** dispone de `pg_trgm` ni `unaccent`. Toda mejora debe usar
  capacidades nativas.
- El diccionario de texto completo `spanish` (integrado) **sí** está disponible
  y normaliza acentos y mayúsculas (`posición`→`posicion`, `Resolución`→`resolu`,
  `Cambios`→`cambi`). Los identificadores como `138/2026` se preservan como
  lexema.
- El corpus es pequeño (95 fragmentos), por lo que la búsqueda léxica a texto
  completo no necesita índice adicional.

## 2. Búsqueda léxica (`app/database/repository.py`)

`buscar_lexico_por_coleccion(session, *, coleccion_id, consulta, match_count=10)`
ejecuta SQL **parametrizado** (nunca concatenado) que:

1. Filtra SIEMPRE por `nb.coleccion_id = :coleccion_id` (aislamiento).
2. Convierte la consulta a lexemas (`tsvector_to_array(to_tsvector('spanish', …))`).
3. Cuenta los lexemas en común con cada fragmento (intersección de conjuntos) y
   exige **al menos 1**; también acepta la frase exacta si aparece literalmente.
4. Ordena de forma **reproducible**: coincidencia exacta → nº de lexemas →
   `ts_rank` → `id`.

La coincidencia exacta se calcula con `strpos` sobre la frase extraída de la
consulta (número de resolución o artículo); nunca se inventa.

## 3. Metadatos derivados (`app/utils/referencias.py`)

A partir del **texto** de cada fragmento se extraen, solo si el patrón aparece:

- `pagina`: marcador `[página N]`.
- `referencia`: encabezado de artículo (`Artículo N`).
- `frase exacta`: patrón `NNNN/AAAA` (resolución) o la referencia de artículo.

Estos metadatos son **derivados del fragmento**, no columnas de la base de
datos, y se presentan como "detectados" para no sobreafirmar.

## 4. Fusión híbrida (`app/services/recuperacion.py`)

`fusionar_resultados(vectoriales, lexicas, *, limite, es_prueba, rrf_k=60)`
combina ambas listas mediante **Reciprocal Rank Fusion**:

- `puntuacion_rrf = Σ 1/(k + rank)` sobre las listas donde aparece el fragmento;
- **bono** `1/k` si la coincidencia es exacta (prioriza identificadores);
- deduplicación por `id` (un fragmento en ambas listas es `hibrida`);
- orden por `(-puntuacion_rrf, id)` → determinista;
- `similitud` solo se conserva si el fragmento vino de la búsqueda vectorial;
  para resultados solo-léxicos queda en `None` (**no se inventa** una similitud).

`tipo_busqueda` ∈ {`vectorial`, `lexica`, `hibrida`}.

## 5. Integración en el servicio (`app/services/consulta.py`)

- `recuperar_evidencias(..., usar_lexico, permitir_lexico_sin_embedding)`:
  ejecuta la búsqueda vectorial y, si procede, la léxica, valida el aislamiento
  de **ambas** listas y fusiona.
- Si el embedding de la pregunta falla (`ErrorEmbeddings`) y
  `permitir_lexico_sin_embedding` es verdadero, se continúa **solo** con la
  búsqueda léxica (que no necesita embeddings). Esto permite responder sin
  consumir cuota de embeddings.
- `ejecutar_consulta(..., generar=False)` y el flujo de chat devuelven las
  evidencias sin invocar al LLM (modo **solo recuperación**,
  `solo_recuperacion=True`).

## 6. Configuración

| Variable | Por defecto | Descripción |
| --- | --- | --- |
| `RECUPERACION_LEXICA` | `true` | Activa la búsqueda híbrida. |
| `RECUPERACION_LEXICA_SIN_EMBEDDING` | `true` | Continúa solo-léxico si falla el embedding. |
| `HIBRIDO_RRF_K` | `60` | Parámetro `k` de RRF. |

La búsqueda **vectorial se conserva siempre**; la léxica solo la complementa.

## 7. Interfaz web (`app/static/chat.html`)

- Muestra `tipo_busqueda`, coincidencia exacta, `página`/`referencia`
  detectadas, y usa "relevancia léxica" cuando no hay similitud vectorial.
- Todo el contenido se pinta con `textContent` (sin HTML no confiable).
- Ante un **503** del proveedor de IA, reintenta la consulta con
  `generar=false` para mostrar igualmente las fuentes recuperadas.

## 8. Limitaciones conocidas

- La cobertura depende del corpus indexado: p. ej. la colección tributaria no
  contiene material sustantivo de IVA, por lo que esa consulta no recupera
  evidencia. No es un fallo de la búsqueda.
- La búsqueda léxica exige al menos un lexema en común; preguntas sin términos
  indexados devuelven vacío (no se inventan coincidencias).
- No se ha modificado el esquema ni la RPC vectorial (sin migraciones).

## 9. Pruebas

- `tests/test_referencias.py`: extracción de metadatos.
- `tests/test_busqueda_lexica.py`: búsqueda léxica real (solo lectura),
  aislamiento, normalización, casos vacíos.
- `tests/test_recuperacion_hibrida.py`: fusión RRF, deduplicación, orden,
  ausencia de similitudes inventadas, solo-recuperación, fallback sin embedding.
