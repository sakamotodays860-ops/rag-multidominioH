# Informe técnico: Fase 5 — Benchmark de concurrencia

## 1. Objetivo

Evaluar el efecto de la concurrencia en el scraping web y en la ingesta
integral del pipeline RAG multidominio mediante una carga de 10 URLs.

Se consideran el tiempo total, el speedup y la eficiencia.

## 2. Entorno y validación

- Backend: FastAPI y Python.
- Base de datos: PostgreSQL con pgvector.
- Embeddings: Gemini gemini-embedding-001.
- URLs de referencia: 10.
- Pruebas automatizadas: 217 pasaron en la última ejecución registrada.
- Resultados originales: backups/benchmark_20261012/.

## 3. Resultados del scraping concurrente

Se realizaron tres repeticiones por nivel de concurrencia. Las ejecuciones
finalizaron correctamente y utilizaron la misma carga, verificada mediante
el hash b5053f6c843c8935962f02acba2605645cea0316c0967ecde3501ee122fbb20d.

| Trabajadores | Tiempo promedio (s) | Speedup | Eficiencia | Estado |
|---:|---:|---:|---:|---|
| 1 | 6.931249 | 1.0000 | 1.0000 | COMPLETED |
| 2 | 3.040423 | 2.2797 | 1.1398 | COMPLETED |
| 4 | 2.198843 | 3.1522 | 0.7881 | COMPLETED |
| 8 | 1.773352 | 3.9086 | 0.4886 | COMPLETED |

Con ocho trabajadores, el tiempo promedio disminuyó aproximadamente un 74.4%
respecto a la ejecución con uno. El speedup observado fue de 3.91x.

La eficiencia superó el 100% con dos trabajadores. Esto puede ocurrir por
el solapamiento de esperas de red y la variabilidad de las mediciones;
por tanto, debe interpretarse con cautela. La eficiencia disminuyó al
aumentar la concurrencia de cuatro a ocho trabajadores.

Estos resultados corresponden únicamente al scraping concurrente.

## 4. Resultados de la ingesta integral

Se utilizó un corpus congelado de 10 documentos y 51 fragmentos. El hash
de la carga fue e18be6a6dc3f612d5200cd4c94c640e8d7643ad0b26c08e4d6067ae0e8a9aca7.

| Trabajadores | Tiempo registrado (s) | Fragmentos | Embeddings generados | Registros insertados | Estado |
|---:|---:|---:|---:|---:|---|
| 1 | 2.578299 | 51 | 51 | 51 | COMPLETED |
| 2 | 23.817562 | 51 | 0 | 0 | FAILED |
| 4 | 23.867451 | 51 | 0 | 0 | FAILED |
| 8 | 14.834215 | 51 | 51 | 0 | RATE_LIMITED |

Las ejecuciones con dos, cuatro y ocho trabajadores se vieron afectadas
por errores HTTP 429 de Gemini (RESOURCE_EXHAUSTED).

Estas ejecuciones no son válidas para calcular speedup ni eficiencia del
pipeline integral. Sus tiempos se conservan únicamente como evidencia de
los intentos y de la limitación encontrada.

La ejecución secuencial completó la ingesta de 51 fragmentos, pero una sola
ejecución válida no permite determinar el escalamiento integral.

## 5. Limitaciones

- La cuota disponible de Gemini impidió completar comparaciones integrales
  válidas para todos los niveles de concurrencia.
- Los tiempos de ejecuciones fallidas o limitadas por cuota no son
  comparables con una ejecución completada.
- Los resultados de scraping y de ingesta integral corresponden a
  mediciones diferentes y no deben mezclarse.
- Para cerrar el benchmark integral se deberán repetir las ejecuciones
  afectadas cuando exista cuota suficiente, usando el mismo corpus.

## 6. Conclusiones

1. El scraping concurrente redujo el tiempo promedio al aumentar los
   trabajadores, con un speedup observado de 3.91x para ocho trabajadores.
2. La mejora no fue proporcional al número de trabajadores y la eficiencia
   disminuyó en los niveles más altos.
3. La ingesta secuencial completó 51 embeddings y 51 inserciones.
4. No hay datos válidos suficientes para afirmar cuál es el speedup integral
   con dos, cuatro u ocho trabajadores debido a los errores 429.
5. El benchmark integral queda parcialmente validado y requiere nuevas
   ejecuciones cuando haya cuota suficiente de Gemini.
