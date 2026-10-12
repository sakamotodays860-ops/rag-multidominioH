#!/usr/bin/env python3
"""Genera las graficas del benchmark (FASE 5): tiempo, speedup y eficiencia.

Uso (desde el contenedor backend, tras instalar requirements-dev):

  python scripts/graficar_benchmark.py [benchmarks/results/benchmark_resultados.json]

Solo se grafican ejecuciones COMPLETED: si falta T1 o alguna T_p (estados
RATE_LIMITED/PARTIAL/FAILED), la serie correspondiente se omite y el script
lo anuncia en consola. No inventa datos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# El script se ejecuta como `python scripts/graficar_benchmark.py` desde el
# contenedor (/app), por lo que el paquete `app` no esta en sys.path.
RAIZ_APP = Path(__file__).resolve().parents[1]
if str(RAIZ_APP) not in sys.path:
    sys.path.insert(0, str(RAIZ_APP))

from app.services.graficas import cargar_resultados, generar_graficas  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "json_resultados",
        nargs="?",
        default=str(RAIZ / "benchmarks/results/benchmark_resultados.json"),
        help="JSON generado por benchmark_concurrencia.py",
    )
    parser.add_argument(
        "--salida",
        default=str(RAIZ / "benchmarks/results"),
        help="directorio donde guardar las PNG",
    )
    args = parser.parse_args()

    try:
        resultado = cargar_resultados(args.json_resultados)
        archivos = generar_graficas(resultado, args.salida)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print(
            "Primero ejecuta el benchmark real (resultados <=> COMPLETED), "
            "luego vuelve a graficar.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    for titulo, ruta in archivos.items():
        print(f"[graficar] {titulo:<12} -> {ruta}")
    print("[graficar] OK: graficas generadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())