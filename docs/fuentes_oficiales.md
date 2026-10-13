# Fuentes oficiales: inventario y alcance de la verificación

Inventario de **URLs institucionales** localizadas y comprobadas con una petición
GET real desde el contenedor `rag_backend` (sin Gemini). Cada fila registra
código HTTP, `Content-Type`, tamaño y, cuando aplica, magic bytes `%PDF-`.

## Alcance y límites (leer antes de usar este documento)

Este documento distingue cuatro niveles que **no** son equivalentes:

- **(a) URL institucional real:** el dominio pertenece a una entidad oficial
  (p. ej. `asfi.gob.bo`) y respondió HTTP 200. Solo prueba la existencia del
  recurso en ese momento.
- **(b) Documento oficial servido:** el recurso es un PDF `application/pdf` con
  cabecera `%PDF-` (o HTML institucional) publicado por la entidad. Prueba que la
  entidad publica *un* archivo, **no** que sea la versión autoritativa vigente.
- **(c) Norma vigente y contenido comprobado:** exige verificar legalmente la
  vigencia y contrastar el texto contra una fuente autoritativa. **Esto no se ha
  hecho en este proyecto** para ningún documento.
- **(d) Fragmento recuperado por el RAG:** un fragmento devuelto por la RPC
  `match_normativa_coleccion`. Que se recupere **no** implica que su contenido
  sea normativa validada: puede proceder de portales de navegación o de datos de
  benchmark/semilla.

Por tanto: **la verificación aquí es de disponibilidad HTTP, no de vigencia ni
de contenido normativo**, y **no demuestra cobertura normativa suficiente**. La
columna «Verificado» indica únicamente que la URL respondió HTTP 200 con el tipo
esperado en la fecha de la comprobación.

- **Regla 7:** ninguna URL de este documento fue inventada; todas provienen de los
  portales oficiales de las instituciones o de resultados de búsqueda confirmados.
- **Regla 10:** una página índice (HTML) **no** se trata como normativa incorporada.
  Un PDF publicado por una entidad se considera *documento oficial candidato*,
  nunca norma vigente ya validada.
- **Estado:** inventario para aprobación. **No se ha ingerido ningún documento nuevo**
  (la ingesta real requiere autorización explícita y cuota de Gemini).

## Dominio financiero

| Documento (etiqueta de origen) | Institución | URL verificada | Tipo | HTTP | Bytes | Verificado (HTTP) | Previsualizado |
|---|---|---|---|---|---|---|---|
| Ley N° 393 de Servicios Financieros (texto oficial) | ASFI | https://www.asfi.gob.bo/sites/default/files/2025-09/Ley%20N%C2%B0%20393%20de%20Servicios%20Financieros.pdf | PDF | 200 | 651 280 | sí | sí |
| Ley N° 393 texto ordenado | ASFI | https://www.asfi.gob.bo/sites/default/files/2025-07/Texto%20ordenado.pdf | PDF | 200 | 1 470 710 | sí | no |
| Recopilación de Normas para Servicios Financieros (RNSF) — índice | ASFI | https://www.asfi.gob.bo/la/recopilacion-normas-para-servicios-financieros-rnsf | HTML (índice) | 200 | — | sí (índice, no normativa) | no |
| Boletín normativo 73 (sep 2026) | ASFI | https://www.asfi.gob.bo/sites/default/files/2026-10/Bolet%C3%ADn%2073%20-%20septiembre%202026.pdf | PDF | 200 | 303 901 | sí | no |
| Marco normativo — índice | BCB | https://www.bcb.gob.bo/?q=marco-normativo | HTML (índice) | 200 | — | sí (índice, no normativa) | no |
| RD N° 89/2026 — Reglamento de Encaje Legal | BCB | https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%2089-2026%20%2826-06-2026%29.pdf | PDF | 200 | 6 055 402 | sí | no |
| RD N° 138/2026 — Reglamento de Posición de Cambios | BCB | https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20138-2026%20%2821-09-2026%29.pdf | PDF | 200 | 1 749 503 | sí | sí |
| RD N° 142/2026 — Reglamento de Operaciones Cambiarias | BCB | https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20142-2026%20%2824-09-2026%29.pdf | PDF | 200 | 1 576 255 | sí | no |

> **Posible duplicación (Ley N° 393):** las dos primeras filas apuntan a versiones
> distintas de la **misma** norma (texto de la ley y «texto ordenado»), servidas en
> rutas y con tamaños diferentes (651 280 y 1 470 710 bytes). **No se debe asumir que
> son documentos distintos ni que son idénticos.** Antes de decidir si se ingieren
> ambas hay que **comparar el contenido** (texto y fecha/versión) y quedarse, como
> norma de trabajo, con la versión vigente y autoritativa, o marcar explícitamente
> ambas y documentar la relación. Ninguna URL se elimina del inventario.

## Dominio tributario

| Documento (etiqueta de origen) | Institución | URL verificada | Tipo | HTTP | Bytes | Verificado (HTTP) | Previsualizado |
|---|---|---|---|---|---|---|---|
| Ley N° 2492 — Código Tributario Boliviano (texto ordenado) | SIN | https://www.impuestos.gob.bo/wp-content/uploads/2026/08/2.-LEY-N%C2%B0-2492-07-26-1.pdf | PDF | 200 | 6 691 596 | sí | sí |
| Ley N° 843 — texto ordenado, complementado y actualizado | SIN | https://www.impuestos.gob.bo/wp-content/uploads/2026/09/1.-LEY-N%C2%B0-843-08-26.pdf | PDF | 200 | 9 797 684 | sí | sí |
| Índice de leyes tributarias | SIN | https://www.impuestos.gob.bo/index.php/leyes/ | HTML (índice) | 200 | — | sí (índice, no normativa) | no |
| Ley N° 1990 — Ley General de Aduanas (compilación) | Aduana Nacional / VUCE | https://www.vuce.gob.bo/sites/default/files/2024-08/LEY%20GENERAL%20DE%20ADUANAS.pdf | PDF | 200 | 4 148 597 | sí | sí |
| Ley General de Aduanas — vista HTML | Aduana Nacional | https://www.aduana.gob.bo/lga-view | HTML | 200 | — | sí (HTML oficial, no PDF) | no |

> **Nota (tributario/aduanero):** «Ley General de Aduanas» aparece como PDF (VUCE) y
> como vista HTML (Aduana). Son **la misma norma por dos vías**; conviene comparar el
> contenido y no ingerir la misma norma dos veces sin justificarlo.

## Candidatos descartados

| URL | Motivo |
|---|---|
| http://www.gacetaoficialdebolivia.gob.bo/normas/descargarPdf/142913 | HTTP 200 pero `Content-Type: text/html`, 206 bytes → no es PDF |
| http://www.gacetaoficialdebolivia.gob.bo/normas/verGratis_gob/17891 | HTTP 200, `text/html`, 0 bytes → sin contenido |
| http://www.gacetaoficialdebolivia.gob.bo/normas/descargarPdf/22557 | HTTP 200, `text/html`, 0 bytes → sin contenido |

Los enlaces de la Gaceta Oficial no entregan el PDF vía HTTP directo (posible bloqueo/JS);
se descartan hasta contar con una URL directa confirmada. No se ingresan.

## Procedimiento reproducible

1. `docker compose cp scripts/verificar_fuentes.py backend:/app/scripts/verificar_fuentes.py`
2. `docker compose exec -T -e PYTHONPATH=/app backend python scripts/verificar_fuentes.py --json`
3. Revisar el JSON: solo entran a la lista de candidatos los que tienen
   `magic_pdf: true` y `http: 200`.
4. **Solicitar autorización** antes de cualquier ingesta real (cuota de Gemini).

## Extracción y previsualización (FASE 3/4, sin Gemini)

Verificado con `POST /api/previsualizar-ingesta` (scraping + limpieza + chunking,
**sin** llamar a Gemini ni escribir en la base de datos). Los siguientes son
**conteos de extracción**, es decir, cuántos fragmentos *se generarían*; **no** son
fragmentos recuperados por el RAG ni evidencia normativa validada:

| Documento | Páginas | Fragmentos (extracción) |
|---|---|---|
| Ley N° 393 (ASFI) | 167 | 518 |
| RD N° 138/2026 (BCB) | 7 | 15 |
| Ley N° 2492 (SIN) | 329 | 689 |
| Ley N° 843 (SIN) | 567 | 1127 |
| Ley N° 1990 — Aduanas (VUCE) | 130 | 289 |

Marca de página: cada fragmento de un PDF multipágina empieza con `[página N]`,
de modo que las citas de las respuestas pueden indicar la página exacta.

### Estado por documento

- **Previsualizados** (extracción medida): Ley N° 393 (texto oficial), RD N° 138/2026,
  Ley N° 2492, Ley N° 843, Ley N° 1990 (PDF de VUCE).
- **Solo verificados por HTTP** (sin previsualizar): Ley N° 393 texto ordenado,
  Boletín 73 ASFI, RD N° 89/2026, RD N° 142/2026, y las vistas/índices HTML.

Ninguno de estos documentos está ingerido. La previsualización **no** consume cuota.

> **Nota de configuración (FASE 3):** el límite `http_max_bytes` se elevó de 2 MB a
> **20 MB**. Con el valor antiguo se rechazaban por tamaño los PDF normativos
> grandes (Ley 2492 ≈ 6.7 MB, Ley 843 ≈ 9.8 MB, Ley 1990 ≈ 4.1 MB).
