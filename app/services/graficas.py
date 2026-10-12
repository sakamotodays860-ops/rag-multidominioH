"""Generacion de graficas del benchmark (FASE 5).

Funciones puras (``cargar_resultados`` / ``preparar_series``) sin dependencias
graficas, y ``generar_graficas`` que importa ``matplotlib`` de forma perezosa
(solo se necesita para el analisis; no es dependencia de la app).

Solo se grafican los datos reales (ejecuciones COMPLETED). Si falta ``T1`` o
alguna ``T_p``, esa serie se omite: nunca se dibuja una curva enganosa.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.benchmark import COMPLETED, ESTADOS_VALIDOS_PARA_METRICAS


def cargar_resultados(ruta: str | Path) -> dict:
    """Carga un ``benchmark_resultados.json`` validando su estructura basica."""
    camino = Path(ruta)
    if not camino.is_file():
        raise FileNotFoundError(f"No existe el JSON del benchmark: {camino}")
    datos = json.loads(camino.read_text(encoding="utf-8"))
    if "ejecuciones" not in datos or "resumen" not in datos:
        raise ValueError(
            f"{camino} no parece un benchmark_resultados.json (faltan "
            "'ejecuciones' o 'resumen')."
        )
    return datos


def preparar_series(resultado: dict) -> dict:
    """Extrae las series reales para graficar (solo COMPLETED)."""
    series = {
        "workers": [],
        "tiempo_total_s": [],
        "speedup": [],
        "eficiencia": [],
        "estados": {},
    }
    for fila in resultado["resumen"]:
        series["estados"][fila["workers"]] = fila["estado"]
        if fila["tiempo_total_s"] is None:
            continue
        series["workers"].append(fila["workers"])
        series["tiempo_total_s"].append(fila["tiempo_total_s"])
        # Solo los datos validos informan speedup/eficiencia.
        if fila["estado"] in ESTADOS_VALIDOS_PARA_METRICAS:
            series["speedup"].append(fila["speedup"])
            series["eficiencia"].append(fila["eficiencia"])
        else:  # pragma: no cover - defensivo
            series["speedup"].append(None)
            series["eficiencia"].append(None)
    return series


def _nota_omision(series: dict) -> str:
    """Leyenda cuando alguna serie se omitio por falta de datos validos."""
    omitidos = [
        w
        for w, estado in series["estados"].items()
        if estado not in ESTADOS_VALIDOS_PARA_METRICAS
    ]
    if not omitidos:
        return ""
    return (
        "Se omitieron series sin ejecuciones COMPLETED "
        f"(workers={omitidos}; estado != COMPLETED)."
    )


def generar_graficas(
    resultado: dict,
    salida: str | Path,
) -> dict[str, Path]:
    """Genera y guarda las tres graficas del benchmark (REQUISITO 9).

    Devuelve ``{titulo: ruta_archivo}``. Requiere ``matplotlib`` instalado;
    si no lo esta, lanza ImportError con instrucciones.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:  # pragma: no cover - entorno de analisis
        raise ImportError(
            "Se necesita matplotlib para generar graficas: "
            "`python -m pip install -r requirements-dev.txt`"
        ) from error

    ruta_salida = Path(salida)
    ruta_salida.mkdir(parents=True, exist_ok=True)
    series = preparar_series(resultado)
    nota = _nota_omision(series)

    archivos: dict[str, Path] = {}

    # --- 1. Tiempo total vs. workers -------------------------------
    fig, ejes = plt.subplots(figsize=(7, 5))
    ejes.plot(
        series["workers"],
        series["tiempo_total_s"],
        marker="o",
        linestyle="-",
        color="#1f77b4",
    )
    ejes.set_xlabel("Workers (concurrencia)")
    ejes.set_ylabel("Tiempo total (s)")
    ejes.set_title("Tiempo total de ingesta vs. concurrencia")
    ejes.grid(True, linestyle="--", alpha=0.4)
    if nota:
        fig.text(0.02, 0.01, nota, fontsize=8, color="#666666")
    ruta = ruta_salida / "grafica_tiempo_workers.png"
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    archivos["tiempo_total"] = ruta

    # --- 2. Speedup S_p vs. workers --------------------------------
    fig, ejes = plt.subplots(figsize=(7, 5))
    if series["speedup"]:
        ejes.plot(
            series["workers"],
            series["speedup"],
            marker="s",
            linestyle="-",
            color="#2ca02c",
            label="S_p medido (solo COMPLETED)",
        )
        # Linea de speedup lineal ideal como referencia.
        ejes.plot(
            series["workers"],
            series["workers"],
            linestyle="--",
            color="#999999",
            label="Referencia lineal ideal (S=p)",
        )
    ejes.set_xlabel("Workers (concurrencia)")
    ejes.set_ylabel("Speedup S_p = T1 / T_p")
    ejes.set_title("Speedup vs. concurrencia")
    ejes.grid(True, linestyle="--", alpha=0.4)
    ejes.legend()
    if nota:
        fig.text(0.02, 0.01, nota, fontsize=8, color="#666666")
    ruta = ruta_salida / "grafica_speedup_workers.png"
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    archivos["speedup"] = ruta

    # --- 3. Eficiencia E_p vs. workers ------------------------------
    fig, ejes = plt.subplots(figsize=(7, 5))
    if series["eficiencia"]:
        ejes.plot(
            series["workers"],
            series["eficiencia"],
            marker="^",
            linestyle="-",
            color="#d62728",
        )
        ejes.axhline(y=1.0, color="#999999", linestyle="--", linewidth=1)
    ejes.set_xlabel("Workers (concurrencia)")
    ejes.set_ylabel("Eficiencia E_p = S_p / p")
    ejes.set_title("Eficiencia vs. concurrencia (limite ideal en 1.0)")
    ejes.set_ylim(bottom=0)
    ejes.grid(True, linestyle="--", alpha=0.4)
    if nota:
        fig.text(0.02, 0.01, nota, fontsize=8, color="#666666")
    ruta = ruta_salida / "grafica_eficiencia_workers.png"
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    archivos["eficiencia"] = ruta

    return archivos


def preparar_graficas(
    resultado: dict,
    salida: str | Path = "benchmarks/results",
) -> tuple[dict, dict[str, Path]]:
    """Comodidad: devuelve (series, archivos) para el CLI de graficado."""
    return preparar_series(resultado), generar_graficas(resultado, salida)