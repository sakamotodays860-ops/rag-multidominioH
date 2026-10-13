# Plan de ingesta controlada de normativa (Bolivia)

Guía para incorporar documentos normativos al RAG **sin** comprometer datos
existentes y **sin** gastar cuota de Gemini por error.

> **Estado: NO ejecutado.** Este documento es un plan. **No se ha ingerido ningún
> documento** de los aquí listados y **no se ha llamado a Gemini**. Toda ingesta
> real requiere **autorización explícita** del responsable del proyecto (consume
> cuota de embeddings).

Referencias: inventario en [`fuentes_oficiales.md`](fuentes_oficiales.md),
alcance/límites ahí descritos, y el diseño de idempotencia en
`app/database/repository.py`.

## 0. Principios

1. **Un documento conocido por lote.** Nunca ingerir URLs «a ciegas» en bloque.
2. **Comprobar antes de embeber.** Origen, título y contenido se validan con
   `POST /api/previsualizar-ingesta` (no consume cuota ni escribe en la BD).
3. **No duplicar.** La inserción es idempotente (`registros_insertados` vs
   `registros_omitidos`); aun así, no ingerir dos veces la misma norma (ver Ley 393).
4. **Nada destructivo.** Solo inserción (append-only). No se borra, actualiza ni
   reindexa ninguna fila.

## 1. Preparación (antes de autorizar)

- [ ] Verificar que cada URL responde HTTP 200 y sirve el tipo esperado.
- [ ] Confirmar entidad emisora y fecha del documento (origen).
- [ ] Confirmar el **título** real extraído (campo `documentos[].titulo`) y que
      corresponda a la norma buscada.
- [ ] Comparar contenido entre versiones duplicadas (Ley 393 texto oficial vs
      texto ordenado; Ley 1990 PDF VUCE vs HTML Aduana) y decidir cuál ingerir.
- [ ] Definir la colección destino: `financiero_bolivia_2026` (ASFI/BCB) o
      `tributario_bolivia_2026` (SIN/Aduana).
- [ ] Ejecutar la previsualización del paso 2 y revisarla.

## 2. Previsualización (sin Gemini, sin BD)

```bash
# Requiere el backend en marcha. No consume cuota ni inserta filas.
curl -s -X POST http://localhost:8000/api/previsualizar-ingesta \
  -H 'Content-Type: application/json' \
  -d '{"coleccion_id":"financiero_bolivia_2026",
       "urls":["<URL del PDF>"],
       "min_caracteres":500}'
```

Revisar en la respuesta:

- `urls_procesadas` / `urls_con_error` y `errores[].motivo` (URL, tamaño, PDF, etc.);
- `documentos[].titulo`, `.mime`, `.paginas` y `.fragmentos`;
- `fragmentos_generados` y `muestra_fragmentos` (calidad del texto extraído);
- `duplicados` (URLs con contenido idéntico dentro de la misma petición).

**Criterio de aceptación:** título coherente, sin errores, y muestra de
fragmentos legible que corresponda a la norma (no menú/navegación).

## 3. Lotes propuestos (uno por documento)

Ingerir **un documento por vez** con `POST /api/ingestar-web` (NO ejecutar todavía).

| # | Documento | Colección | Estado doc. | Fragmentos (medidos) |
|---|---|---|---|---:|
| 1 | Ley N° 393 (texto oficial) | financiero_bolivia_2026 | previsualizado | 518 |
| 2 | RD N° 138/2026 (BCB) | financiero_bolivia_2026 | previsualizado | 15 |
| 3 | Ley N° 2492 (SIN) | tributario_bolivia_2026 | previsualizado | 689 |
| 4 | Ley N° 843 (SIN) | tributario_bolivia_2026 | previsualizado | 1127 |
| 5 | Ley N° 1990 — Aduanas (VUCE) | tributario_bolivia_2026 | previsualizado | 289 |
| 6 | Ley N° 393 texto ordenado | financiero_bolivia_2026 | solo HTTP | por medir |
| 7 | Boletín 73 ASFI | financiero_bolivia_2026 | solo HTTP | por medir |
| 8 | RD N° 89/2026 (BCB) | financiero_bolivia_2026 | solo HTTP | por medir |
| 9 | RD N° 142/2026 (BCB) | financiero_bolivia_2026 | solo HTTP | por medir |

**Ley 393 (filas 1 y 6):** decidir **una** versión (la vigente/autoritativa) o
justificar por escrito por qué se ingieren ambas. No ingerir ambas por defecto.

## 4. Estimación de consumo de embeddings

- Cada fragmento = **1 embedding** (`gemini-embedding-001`).
- **Dato medido** (previsualización, no cuota): los 5 documentos de las filas 1–5
  suman **2638 fragmentos** (518 + 15 + 689 + 1127 + 289) → hasta **2638
  embeddings** si se ingieren los cinco.
- Los documentos 6–9 **no se han previsualizado**: su número de fragmentos **no
  se conoce** y **no se estima aquí** (sería inventarlo). Previsualizarlos primero.

> **Cuota:** no se dispone de datos de la cuota disponible de Gemini en este
> proyecto, por lo que **no se inventa** una estimación de cuota. Antes de ingerir:
> confirmar cuota con el responsable y proceder en lotes, vigilando HTTP 429.

## 5. Ejecución de un lote (tras autorización)

- Ejecutar `POST /api/ingestar-web` con **una** URL y la colección destino.
- Leer la respuesta: `registros_insertados`, `registros_omitidos`, `duplicados`,
  `embeddings_generados` y `documentos`.
- **Verificación:** `registros_insertados` debe ser > 0 en la primera ingesta;
  en una repetición del mismo documento, `registros_omitidos` = fragmentos y
  `registros_insertados` = 0.
- **`benchmark_2026` no se toca:** es una colección de benchmark; nunca es destino
  de estos lotes. Comprobar su conteo antes y después (debe seguir en 51).
- Si aparece `ErrorEmbeddings` (429/cuota), **detener** y no reintentar en bucle.

## 6. Verificación posterior (recuperación y citas)

Solo lectura, sin ingestión adicional:

- `GET /api/colecciones/{coleccion_id}` → confirmar aumento de fragmentos.
- `POST /api/consultar` (o `/api/consultar-auto`) y comprobar que `evidencias[]`
  trae `origen_url` del documento y `es_datos_prueba: false`.
- Confirmar que las citas incluyen la marca `[página N]` y que la página
  corresponde al PDF.
- **No cambiar umbrales** (`similarity_threshold`, `top_k`) sin evidencia de que
  mejoran la recuperación; si se cambian, registrar antes/después.

## 7. Preguntas de validación (respuesta contrastable en el PDF)

Preguntas propuestas para verificar recuperación. **La respuesta NO se afirma
aquí**: debe localizarse y contrastarse en el PDF ingerido (documento y página),
y compararse con lo que devuelve el chat.

| Documento | Pregunta de validación | Contraste |
|---|---|---|
| Ley N° 393 | ¿Cuál es el objeto/alcance de la Ley de Servicios Financieros? | Artículo inicial del PDF |
| Ley N° 393 | ¿Qué entidades regula la ley? | Definiciones/ámbito del PDF |
| Ley N° 2492 | ¿Qué es el Código Tributario y a quién se aplica? | Artículos iniciales del PDF |
| Ley N° 843 | ¿Qué impuestos establece la ley? | Títulos del PDF |
| Ley N° 1990 | ¿Qué es el régimen aduanero y qué regula? | Artículos iniciales del PDF |
| RD N° 138/2026 | ¿Qué regula la resolución sobre posición de cambios? | Texto de la resolución (BCB) |

Criterio: la respuesta del chat debe poder rastrearse a un fragmento real
(`origen_url` + página) y coincidir con el PDF. Si no, es señal de baja cobertura
o de mala extracción, no de que «falte un umbral».

## 8. Criterios de parada

- 429 o cuota agotada → detener el lote; esperar autorización/renovación.
- Errores de extracción repetidos (PDF escaneado, sin texto) → descartar el
  documento y documentarlo, no forzar.
- Fragmentos de navegación (título/muestra no normativos) → no ingerir; buscar la
  URL del documento concreto.

## 9. Autorización

**Este plan no se ejecuta hasta obtener autorización explícita** para consumir
Gemini. Hasta entonces, solo se permiten: verificación HTTP, previsualización y
pruebas offline.
