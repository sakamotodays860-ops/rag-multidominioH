#!/usr/bin/env python3
"""Benchmark de concurrencia de la ingesta (FASE 5).

Ejecuta el pipeline de FASE 3 (descarga -> chunking -> embeddings Gemini ->
insercion) para cada nivel de concurrencia p con una MISMA carga. Hay DOS
modos:

  A) Benchmark de scraping real (por defecto): las URLs se descargan de nuevo
     en cada ejecucion y se mide ``tiempo_scraping_s`` real. El contenido web
     varia, asi que este modo NO es apto para comparar T1/T_p.

  B) Benchmark controlado con CORPUS CONGELADO (recomendado para T/S/E):
     primero se crea el corpus UNA sola vez:

       python scripts/benchmark_concurrencia.py \
           --crear-corpus benchmarks/corpus.json \
           --urls-file benchmarks/urls_benchmark.json --categoria todas

     y despues se comparan p=1,2,4,8 sobre esos mismos textos:

       python scripts/benchmark_concurrencia.py \
           --coleccion-id benchmark_2026 \
           --corpus benchmarks/corpus.json \
           --workers 1 2 4 8

     Todas las corridas usan EXACTAMENTE los documentos del corpus (hash
     verificado: si la carga no coincide -> INVALID_LOAD y sin T/S/E).

  C) Benchmark SOLO SCRAPING (--solo-scraping): mide UNICAMENTE la descarga
     concurrente y RETORNA antes de chunking, embeddings e insercion. NO usa
     Gemini ni PostgreSQL. La carga comparable es la lista ORDENADA de URLs
     (el contenido web puede variar). Escribe sus resultados en archivos
     SEPARADOS para no sobrescribir el benchmark integral:

       python scripts/benchmark_concurrencia.py \
           --solo-scraping \
           --urls-file benchmarks/urls_benchmark.json --categoria todas \
           --workers 1 2 4 8

Genera, por defecto en benchmarks/results/:
  benchmark_resultados.json   (JSON completo, reproducible)
  benchmark_resumen.csv       (resumen workers | tiempo | speedup | eficiencia)
  y, en modo --solo-scraping, en su lugar:
  benchmark_scraping_resultados.json
  benchmark_scraping_resumen.csv

Estados posibles por ejecucion: COMPLETED, PARTIAL, FAILED, RATE_LIMITED,
INVALID_LOAD (SOLO las COMPLETED sin 429 y con carga identica entran en
T_p/S_p/E_p). El modo por defecto es ESTRICTO: cualquier 429 de Gemini
invalida la ejecucion. Los embeddings se generan SECUENCIALMENTE por lotes
(FASE 3): no se paralelizan aqui (seccion serial para el analisis de Amdahl).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# El script se ejecuta como `python scripts/benchmark_concurrencia.py` desde
# el contenedor (/app), por lo que el paquete `app` no esta en sys.path.
RAIZ_APP = Path(__file__).resolve().parents[1]
if str(RAIZ_APP) not in sys.path:
    sys.path.insert(0, str(RAIZ_APP))

from app.core.config import get_settings  # noqa: E402
from app.services.benchmark import (  # noqa: E402
    INVALID_LOAD,
    PARTIAL,
    RATE_LIMITED,
    cargar_dataset,
    construir_resultado,
    crear_corpus,
    ejecutar_benchmark,
    leer_corpus,
    seleccionar_urls,
    validar_urls,
    validar_workers,
)

RAIZ = Path(__file__).resolve().parents[1]


def _listar_urls(args: argparse.Namespace) -> list[str]:
    """Resuelve la lista de URLs desde `--urls`, `--urls-file` o ambos."""
    if args.urls and args.urls_file:
        raise SystemExit("ERROR: use --urls o --urls-file, no ambos.")

    if args.urls_file:
        datos = cargar_dataset(RAIZ / args.urls_file)
        urls = seleccionar_urls(datos, args.categoria)
    elif args.urls:
        urls = list(args.urls)
    else:
        raise SystemExit(
            "ERROR: indica --urls 'url1' 'url2' ... o --urls-file benchmarks/urls_benchmark.json"
        )
    return validar_urls(urls)


def _listar_workers(args: argparse.Namespace) -> list[int]:
    """Valida y normaliza el nivel de concurrencia (por defecto 1 2 4 8)."""
    try:
        workers = [int(w) for w in args.workers]
    except (TypeError, ValueError):
        raise SystemExit("ERROR: --workers debe ser una lista de enteros.")
    for nivel in workers:
        try:
            validar_workers(nivel)
        except ValueError as error:
            raise SystemExit(f"ERROR: {error}")
    # Sin duplicados, manteniendo el orden pedido.
    return list(dict.fromkeys(workers))


def _imprimir_resumen(resumen: list[dict]) -> None:
    """Tabla legible en terminal (workers | T_p | S_p | E_p | estado)."""
    ancho_estado = max(len(str(row["estado"])) for row in resumen)
    print()
    print(
        f"{'workers':>8} | {'T_p (s)':>10} | {'S_p':>8} | {'E_p':>8} | "
        f"{'estado':>{ancho_estado}}"
    )
    print("-" * (8 + 3 + 10 + 3 + 8 + 3 + 8 + 3 + ancho_estado))
    for fila in resumen:
        tiempo = (
            f"{fila['tiempo_total_s']:.6f}"
            if fila["tiempo_total_s"] is not None
            else "-"
        )
        speedup = f"{fila['speedup']:.4f}" if fila["speedup"] is not None else "-"
        eficiencia = (
            f"{fila['eficiencia']:.4f}"
            if fila["eficiencia"] is not None
            else "-"
        )
        marcador = " (!)" if fila["afectado_429"] else ""
        print(
            f"{fila['workers']:>8} | {tiempo:>10} | {speedup:>8} | "
            f"{eficiencia:>8} | {fila['estado'] + marcador:>{ancho_estado}}"
        )
    print()


def _guardar_resultados(
    datos: dict, salida: Path, resumen: list[dict], *, solo_scraping: bool = False
) -> tuple[Path, Path]:
    """Escribe el JSON completo y el CSV resumido.

    El modo ``--solo-scraping`` usa nombres de archivo PROPIOS para no
    sobrescribir los resultados del benchmark integral.
    """
    salida.mkdir(parents=True, exist_ok=True)
    prefijo = "benchmark_scraping" if solo_scraping else "benchmark"
    ruta_json = salida / f"{prefijo}_resultados.json"
    ruta_csv = salida / f"{prefijo}_resumen.csv"

    ruta_json.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    columnas = [
        "workers",
        "tiempo_total_s",
        "speedup",
        "eficiencia",
        "estado",
        "ejecuciones",
        "ejecuciones_validas",
        "afectado_429",
        "carga_identica",
        "hash_carga",
        "T1_base_s",
    ]
    with ruta_csv.open("w", encoding="utf-8", newline="") as manejador:
        escritor = csv.DictWriter(manejador, fieldnames=columnas, extrasaction="ignore")
        escritor.writeheader()
        for fila in resumen:
            escritor.writerow(fila)
    return ruta_json, ruta_csv


def _comando_referencia(args: argparse.Namespace) -> str:
    """Reconstruye el comando usado, para reproducir la medicion."""
    origen = args.urls_file or "--urls 'url1' 'url2' ..."
    partes = [
        "docker compose exec -T backend python scripts/benchmark_concurrencia.py",
        f"--coleccion-id {args.coleccion_id}",
    ]
    if args.solo_scraping:
        partes.append("--solo-scraping")
    if args.corpus:
        partes.append(f"--corpus {args.corpus}")
    else:
        partes.append(f"--urls-file {origen}")
        partes.append(f"--categoria {args.categoria}")
    partes.extend(
        [
            f"--workers {' '.join(str(w) for w in args.workers)}",
            f"--repeticiones {args.repeticiones}",
            f"--chunk-size {args.chunk_size} --chunk-overlap {args.chunk_overlap}",
            f"--modo-429 {args.modo_429}",
        ]
    )
    return " \\\n    ".join(partes)


async def _correr(
    args: argparse.Namespace, urls: list[str], corpus: dict | None = None
) -> list:
    ejecuciones = []
    for nivel in _listar_workers(args):
        for repeticion in range(1, args.repeticiones + 1):
            etiqueta = (
                f"workers={nivel}"
                if args.repeticiones == 1
                else f"workers={nivel} rep={repeticion}/{args.repeticiones}"
            )
            print(f"[benchmark] {etiqueta} ...", flush=True)
            registro = await ejecutar_benchmark(
                urls,
                coleccion_id=args.coleccion_id,
                workers=nivel,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                modo_429=args.modo_429,
                batch_size=args.batch_size,
                corpus=corpus,
                corpus_ruta=args.corpus,
                solo_scraping=args.solo_scraping,
            )
            ejecuciones.append(registro)
            mensaje = (
                f"  -> {registro.estado} "
                f"(T={registro.tiempo_total_s:.3f}s, urls_ok="
                f"{registro.urls_procesadas}/{registro.urls_recibidas}, "
                f"fragmentos={registro.fragmentos_generados}, "
                f"insertados={registro.registros_insertados}, "
                f"429={registro.eventos_429})"
            )
            if registro.mensaje_error:
                mensaje += f"\n      aviso: {registro.mensaje_error[:200]}"
            print(mensaje, flush=True)
    return ejecuciones


def main() -> int:
    descripcion = (
        "Benchmark reproducible de concurrencia de la ingesta (FASE 5). "
        "Compara p=1,2,4,8 con la misma carga de trabajo."
    )
    parser = argparse.ArgumentParser(
        description=descripcion,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--coleccion-id", default=None, help="coleccion destino")
    parser.add_argument(
        "--crear-corpus",
        default=None,
        metavar="RUTA",
        help="crea el corpus congelado (descarga las URLs UNA vez) y sale",
    )
    parser.add_argument(
        "--corpus",
        default=None,
        metavar="RUTA",
        help="benchmark controlado: usa los documentos del corpus congelado "
        "(carga identica verificada por hash)",
    )
    parser.add_argument(
        "--solo-scraping",
        action="store_true",
        help="mide SOLO la descarga concurrente (sin chunking, embeddings ni "
        "insercion; no usa Gemini ni PostgreSQL)",
    )
    parser.add_argument("--urls", nargs="*", help="URLs directas (--url 'a' 'b' ...)")
    parser.add_argument(
        "--urls-file",
        default=None,
        help="dataset JSON con listas 'financiero'/'tributario'",
    )
    parser.add_argument(
        "--categoria",
        choices=["financiero", "tributario", "todas"],
        default="todas",
        help="categoria de URLs del dataset",
    )
    parser.add_argument(
        "--workers",
        nargs="*",
        default=["1", "2", "4", "8"],
        help="niveles de concurrencia (por defecto: 1 2 4 8)",
    )
    parser.add_argument(
        "--repeticiones", type=int, default=1, help="corridas por cada workers"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=1200, help="tamano de fragmento"
    )
    parser.add_argument(
        "--chunk-overlap", type=int, default=200, help="solape de fragmentos"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="textos por llamada a batchEmbedContents (por defecto del config)",
    )
    parser.add_argument(
        "--modo-429",
        choices=["estricto", "permitir"],
        default="estricto",
        help="estricto: cualquier 429 invalida la ejecucion (recomendado)",
    )
    parser.add_argument(
        "--salida",
        default=None,
        help="directorio de resultados (por defecto benchmarks/results)",
    )
    args = parser.parse_args()

    if args.crear_corpus and args.corpus:
        raise SystemExit("ERROR: use --crear-corpus o --corpus, no ambos.")
    if args.solo_scraping and args.corpus:
        raise SystemExit("ERROR: --solo-scraping no es compatible con --corpus.")
    if args.solo_scraping and args.crear_corpus:
        raise SystemExit("ERROR: --solo-scraping no es compatible con --crear-corpus.")
    if args.corpus and (args.urls or args.urls_file):
        raise SystemExit(
            "ERROR: --corpus ya define las URLs congeladas; no uses --urls/--urls-file."
        )
    if args.repeticiones < 1:
        raise SystemExit("ERROR: --repeticiones debe ser >= 1")

    # --- Modo A2: --crear-corpus (descarga real UNA sola vez, sin benchmark) ---
    if args.crear_corpus:
        try:
            urls = _listar_urls(args)
        except SystemExit:
            raise
        except Exception as error:  # noqa: BLE001 - CLI
            raise SystemExit(f"ERROR: {error}")
        print(f"[corpus] Descargando {len(urls)} URLs UNA sola vez ...")
        try:
            creado = asyncio.run(crear_corpus(urls, args.crear_corpus))
        except Exception as error:  # noqa: BLE001 - CLI
            raise SystemExit(f"ERROR: {error}")
        print(f"[corpus] CREADO: {args.crear_corpus}")
        print(
            f"[corpus]   documentos       : {creado['numero_documentos']}"
        )
        print(f"[corpus]   urls_con_error  : {creado['urls_con_error']}")
        print(f"[corpus]   hash_corpus     : {creado['hash_corpus']}")
        print(
            "\n[corpus] Ahora ejecuta el benchmark controlado con EXACTAMENTE "
            "estos documentos, p. ej.:\n    "
            "docker compose exec -T backend python scripts/benchmark_concurrencia.py "
            f"--coleccion-id benchmark_2026 --corpus {args.crear_corpus} "
            "--workers 1 2 4 8"
        )
        return 0

    # --- Modos A (scraping real) y B (corpus congelado) ---
    if args.coleccion_id is None:
        if args.solo_scraping:
            args.coleccion_id = "benchmark_scraping"
        else:
            raise SystemExit("ERROR: --coleccion-id es obligatorio.")

    corpus = None
    try:
        if args.corpus:
            corpus = leer_corpus(args.corpus)
            urls = [doc["url"] for doc in corpus["documentos"]]
            if not urls:
                raise SystemExit("ERROR: el corpus no contiene documentos.")
        else:
            urls = _listar_urls(args)
        workers = _listar_workers(args)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - CLI
        raise SystemExit(f"ERROR: {error}")

    config = get_settings()
    configuracion = {
        "workers": workers,
        "repeticiones": args.repeticiones,
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "batch_size": args.batch_size or config.gemini_batch_size,
        "modelo_embeddings": config.gemini_embedding_model,
        "dimension": config.pgvector_dimension,
        "max_reintentos_gemini": config.gemini_max_reintentos,
        "modo_429": args.modo_429,
        "modo_carga": (
            "scraping_solo"
            if args.solo_scraping
            else ("corpus" if args.corpus else "scraping")
        ),
        "corpus_ruta": args.corpus,
        "coleccion_id": args.coleccion_id,
        "estrategia_embeddings": "batchEmbedContents por lotes (FASE 3)",
    }

    print(f"[benchmark] modo_carga={configuracion['modo_carga']}"
          + (f", corpus={args.corpus}" if args.corpus else ""))
    print(f"[benchmark] URLS ({len(urls)}):")
    for url in urls:
        print(f"  - {url}")
    print(f"[benchmark] coleccion={args.coleccion_id}, "
          f"workers={workers}, repeticiones={args.repeticiones}, "
          f"modo_429={args.modo_429}")

    try:
        ejecuciones = asyncio.run(_correr(args, urls, corpus))
    except KeyboardInterrupt:
        print("\n[benchmark] interrumpido por el usuario (*interrumpido*)")
        return 130
    except ValueError as error:
        raise SystemExit(f"ERROR: {error}")

    datos = construir_resultado(
        coleccion_id=args.coleccion_id,
        urls=urls,
        configuracion=configuracion,
        ejecuciones=ejecuciones,
    )
    datos["timestamp_iso"] = datetime.now(timezone.utc).isoformat()

    # Advertencias ante estados no COMPLETED: nunca se presentan como validas.
    anotaciones = []
    for fila in datos["resumen"]:
        if fila["estado"] == RATE_LIMITED:
            anotaciones.append(
                f"workers={fila['workers']}: RATE_LIMITED por Gemini; la "
                "medicion NO es valida. Reintentar cuando la cuota lo permita."
            )
        elif fila["estado"] == INVALID_LOAD:
            anotaciones.append(
                f"workers={fila['workers']}: INVALID_LOAD; la carga no coincide "
                "con el corpus congelado, no se calculan T/S/E. "
                "Recrea el corpus (--crear-corpus) y reintenta."
            )
        elif fila["estado"] == PARTIAL:
            anotaciones.append(
                f"workers={fila['workers']}: PARTIAL (hubo URLs fallidas); la "
                "carga no es identica, no entra en T/S/E."
            )
    if datos["carga"] and not datos["carga"]["carga_identica"]:
        anotaciones.append(
            "CARGA NO IDENTICA entre corridas COMPLETED; S/E se mantienen "
            "en '-' (nunca comparar cargas distintas p. ej. 49 vs 43)."
            if anotaciones
            else "Carga identica NO verificada (sin corridas COMPLETED); no hay T/S/E."
        )
    if anotaciones:
        print("\n[benchmark] ADVERTENCIAS (no se ocultan):")
        for nota in anotaciones:
            print(f"  - {nota}")

    _imprimir_resumen(datos["resumen"])

    salida = Path(args.salida) if args.salida else RAIZ / "benchmarks" / "results"
    ruta_json, ruta_csv = _guardar_resultados(
        datos, salida, datos["resumen"], solo_scraping=args.solo_scraping
    )
    print(f"[benchmark] JSON completo : {ruta_json}")
    print(f"[benchmark] CSV resumen  : {ruta_csv}")

    pendientes = [f for f in datos["resumen"] if f["tiempo_total_s"] is None]
    if pendientes:
        print(
            "\n[benchmark] Mediciones reales PENDIENTES: alguna ejecucion no "
            "debio considerarse T1/Tp (ver estados). Cuando la API de Gemini "
            "lo permita, vuelve a ejecutar el mismo comando."
        )
    else:
        print(
            "\n[benchmark] OK: todas las ejecuciones COMPLETED. Reintenta la "
            "medicion entera unas cuantas veces para promediar (--repeticiones N)."
        )
    print("\n[benchmark] Comando reproducible de esta corrida:")
    print("    " + _comando_referencia(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())