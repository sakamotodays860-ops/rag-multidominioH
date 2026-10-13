# Entrega — Calidad documental del RAG multidominio

Informe de cierre de las fases 1–6. Todas las verificaciones se hicieron **desde el
contenedor `rag_backend`**. No se modificó la base de datos, no se borró ni reindexó
`benchmark_2026`, no se alteraron los backups y **no se ingirió ningún documento nuevo**
(la ingesta real requiere autorización explícita y cuota de Gemini).

> **Alcance (importante).** Este trabajo mejora la **extracción y preparación** de
> documentos (limpieza, páginas, deduplicación, previsualización). **No** mejora ni
> demuestra por sí mismo la cobertura normativa del chat. Que existan colecciones con
> fragmentos **no** significa que las preguntas jurídicas tengan respuesta correcta ni
> que el corpus cubra las leyes y reglamentos pertinentes. La **cobertura normativa NO
> está completa** y las consultas de prueba sobre **páginas institucionales** (portales
> raíz) sirven para validar el *pipeline*, no para demostrar cobertura suficiente de
> leyes/reglamentos. Ver §3 y §5.

## 1. Archivos modificados / creados

### Creados

| Archivo | Para qué |
|---|---|
| `docs/fuentes_oficiales.md` | Inventario HTTP-verificado de fuentes oficiales (FASE 2) + previsualizaciones (FASE 3/4) |
| `docs/entrega_calidad_documental.md` | Este informe |
| `scripts/verificar_fuentes.py` | Verificador HTTP reutilizable + muestreador de texto (`--json --muestras --limite --dominio`) |
| `tests/test_previsualizacion.py` | Pruebas offline del preview (sin Gemini ni BD) |

### Modificados (código)

| Archivo | Cambio | Motivo |
|---|---|---|
| `requirements.txt` | `cryptography==50.0.2` | pypdf requiere `cryptography>=3.1` para PDF con AES; sin él, `DependencyError` |
| `app/core/config.py` | `http_max_bytes` 2 MB → **20 MB** | Los PDF normativos oficiales superan 2 MB y se rechazaban |
| `app/services/pdf_extractor.py` | `PdfRequiereDependencia`, `extraer_paginas_pdf`, marcas `[página N]`, manejo de `DependencyError` | No perder la página y fallar con mensaje claro en PDF cifrados |
| `app/utils/html_cleaner.py` | Detección de boilerplate, runs de navegación, enlaces aislados, comparación sin acentos | Evitar ruido de portales (menús, avisos `.gob.bo`) en el corpus |
| `app/services/downloader.py` | `ResultadoDescarga.duplicados`, `_huella_texto`, `_deduplicar` | No embeber dos veces el mismo contenido |
| `app/models/ingesta.py` | `PeticionIngesta.min_caracteres`, `RespuestaPrevisualizacion` | Gate de contenido mínimo y contrato del preview |
| `app/services/ingesta.py` | `_descargar`, `_fragmentar`, `_aplicar_gate_minimo`, `previsualizar_ingesta`; `ejecutar_ingesta` reutiliza los helpers | Previsualizar sin Gemini ni BD y compartir la lógica real |
| `app/api/v1/ingesta.py` | Endpoint `POST /api/previsualizar-ingesta` | Exponer el preview seguro |
| `tests/helpers.py` | `construir_pdf_paginas` | Generar PDF multipágina en pruebas |
| `tests/test_pdf_extractor.py` | Multipágina/marcas, `extraer_paginas_pdf`, `PdfRequiereDependencia` | Cubrir extracción robusta |
| `tests/test_html_cleaner.py` | Boilerplate, menú de navegación, enlace aislado, sin acentos | Cubrir limpieza |
| `tests/test_downloader_documentos.py` | Deduplicación | Cubrir dedup |
| `tests/test_api_coleccion.py` | Guardián OpenAPI incluye `/api/previsualizar-ingesta` | Mantener contrato explícito |

## 2. Pruebas ejecutadas y resultados

```
docker compose exec -T backend python -m pytest -q
288 passed, 1 warning in 2.12s
```

- Enfoque previo FASE 3: `36 passed`.
- FASE 4: `tests/test_previsualizacion.py test_validacion_ingesta.py test_api_coleccion.py` → `49 passed`.
- Idempotencia de ingesta (posterior): `tests/test_ingesta_idempotente.py` → 10 pruebas.
- La única advertencia es un `DeprecationWarning` de Starlette/anyio (ajeno al código propio).

## 3. Fuentes oficiales verificadas (URLs)

Detalle completo en `docs/fuentes_oficiales.md`. Lo siguiente son **URLs
institucionales** que respondieron `HTTP 200` sirviendo un PDF (`application/pdf`,
magic `%PDF-`). Esto prueba **disponibilidad HTTP**, no vigencia ni contenido:

- Ley N° 393 (ASFI): `https://www.asfi.gob.bo/sites/default/files/2025-09/Ley%20N%C2%B0%20393%20de%20Servicios%20Financieros.pdf`
- Ley N° 393 texto ordenado (ASFI): `https://www.asfi.gob.bo/sites/default/files/2025-07/Texto%20ordenado.pdf`
- Boletín 73 ASFI: `https://www.asfi.gob.bo/sites/default/files/2026-10/Bolet%C3%ADn%2073%20-%20septiembre%202026.pdf`
- RD N° 89/2026 (BCB): `https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%2089-2026%20%2826-06-2026%29.pdf`
- RD N° 138/2026 (BCB): `https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20138-2026%20%2821-09-2026%29.pdf`
- RD N° 142/2026 (BCB): `https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20142-2026%20%2824-09-2026%29.pdf`
- Ley N° 2492 (SIN): `https://www.impuestos.gob.bo/wp-content/uploads/2026/08/2.-LEY-N%C2%B0-2492-07-26-1.pdf`
- Ley N° 843 (SIN): `https://www.impuestos.gob.bo/wp-content/uploads/2026/09/1.-LEY-N%C2%B0-843-08-26.pdf`
- Ley N° 1990 — Aduanas (VUCE): `https://www.vuce.gob.bo/sites/default/files/2024-08/LEY%20GENERAL%20DE%20ADUANAS.pdf`

**Ninguna de estas normas tiene vigencia ni contenido comprobados por este proyecto**;
solo se comprobó que la URL servía un PDF en esa fecha.

**Índices HTML** (NO se tratan como normativa incorporada): RNSF ASFI, marco normativo BCB,
índice de leyes SIN, `lga-view` Aduana.

**Descartados:** enlaces de la Gaceta Oficial (`descargarPdf/...`, `verGratis_gob/...`)
devuelven `text/html` de 0–206 bytes, no el PDF.

**Posible duplicación:** las dos versiones de la Ley N° 393 (texto oficial y «texto
ordenado») son la **misma** norma por dos rutas; debe compararse el contenido antes de
decidir si se ingieren ambas. Lo mismo aplica a la Ley N° 1990 (PDF de VUCE y vista
HTML de Aduana).

## 4. Muestras de texto extraído

Ley N° 393, página 1 (fragmento devuelto por el preview):

```
[página 1]
LEY  Nº 393

EVO MORALES AYMA
PRESIDENTE CONSTITUCIONAL DEL ESTADO PLURINACIONAL DE BOLIVIA

Por cuanto, la Asamblea Legislativa Plurinacional, ha sancionado la siguiente Ley:

LA AS...
```

Las marcas `[página N]` permiten citar la página exacta en las respuestas. La limpieza de
HTML redujo, por ejemplo, el marco normativo del BCB de 5449 a 1079 caracteres.

## 5. Documentos candidatos que requieren aprobación

Ninguno se ha ingerido. Los siguientes fragmentos son **conteos de extracción medidos**
con `/api/previsualizar-ingesta` (no estimaciones, no cuota consumida):

| Documento | Páginas | Fragmentos (extracción) |
|---|---|---|
| Ley N° 393 (ASFI) | 167 | 518 |
| RD N° 138/2026 (BCB) | 7 | 15 |
| Ley N° 2492 (SIN) | 329 | 689 |
| Ley N° 843 (SIN) | 567 | 1127 |
| Ley N° 1990 — Aduanas (VUCE) | 130 | 289 |

Pendientes de previsualizar (verificados por HTTP): Ley 393 texto ordenado, Boletín 73 ASFI,
RD N° 89/2026 y RD N° 142/2026 (BCB). La duplicación Ley 393 (dos versiones) debe
resolverse comparando contenido antes de ingerir.

> **No confundir con evidencia recuperada.** Estos conteos son lo que *se generaría*;
> no son fragmentos devueltos por el RAG ni normativa validada.

## 6. Cambios que aún requieren cuota de Gemini

- **Ingesta real** de cualquiera de los documentos de la sección 5: cada fragmento es un
  embedding (`gemini-embedding-001`). Las 5 leyes/reglamentos ya previsualizadas suman
  **2638 fragmentos** (518 + 15 + 689 + 1127 + 289).
- La previsualización (`/api/previsualizar-ingesta`) y todas las pruebas **no** consumen cuota.
- La ingesta es append-only y se hará con autorización explícita, vigilando HTTP 429.

## Estado de la configuración

- `GEMINI_MODEL = gemini-3.8-flash` (el antiguo `gemini-2.0-flash` está retirado → HTTP 404).
- Embeddings: `gemini-embedding-001` (768 dimensiones, pgvector 0.8.7, `VECTOR(768)`).
- `http_max_bytes = 20_000_000` (FASE 3).
- Base de datos intacta: `benchmark_2026`=51, `financiero_bolivia_2026`=10, `tributario_bolivia_2026`=19.

> **Sobre `benchmark_2026`.** Es una **colección de benchmark** (51 fragmentos), no una
> colección normativa de producción. Sus fragmentos **no deben considerarse
> automáticamente evidencia normativa validada** para responder consultas jurídicas:
> existen para medir concurrencia/rendimiento. Cualquier respuesta que use esos
> fragmentos debe tratar la procedencia con cautela y no presentarlos como normativa
> vigente comprobada.
