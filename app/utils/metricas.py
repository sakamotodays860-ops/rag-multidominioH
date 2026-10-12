"""Metricas temporales de una ingesta (FASE 3, requisito K).

Separa los cuatro tramos del pipeline — scraping, chunking, embeddings e
insercion — para poder identificar, en el informe de la practica, cual es el
cuello de botella real de `POST /api/ingestar-web`.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator


@dataclass
class MetricasIngesta:
    """Duracion en segundos de cada fase del pipeline de ingesta."""

    tiempo_scraping_s: float = 0.0
    tiempo_chunking_s: float = 0.0
    tiempo_embeddings_s: float = 0.0
    tiempo_insercion_s: float = 0.0
    tiempo_total_s: float = 0.0

    def a_dict(self) -> dict[str, float]:
        """Copia plana, lista para serializar en la respuesta JSON."""
        return asdict(self)

    @property
    def suma_fases_s(self) -> float:
        """Suma de las cuatro fases; se usa para comprobar coherencia."""
        return round(
            self.tiempo_scraping_s
            + self.tiempo_chunking_s
            + self.tiempo_embeddings_s
            + self.tiempo_insercion_s,
            6,
        )


class _Cronometro:
    def __init__(self) -> None:
        self.inicio = time.perf_counter()
        self.transcurrido = 0.0

    def parar(self) -> float:
        self.transcurrido = round(time.perf_counter() - self.inicio, 6)
        return self.transcurrido


@contextmanager
def medir() -> Iterator[_Cronometro]:
    """Mide el tiempo de un bloque con :func:`time.perf_counter`.

    ``perf_counter`` es monotono: no se ve afectado por cambios del reloj del
    sistema, que es lo correcto para tiempos de duracion.
    """
    cronometro = _Cronometro()
    try:
        yield cronometro
    finally:
        if cronometro.transcurrido == 0.0:
            cronometro.parar()


def redondear(medidas: MetricasIngesta, decimales: int = 6) -> MetricasIngesta:
    """Redondea todas las duraciones al numero de decimales indicado."""
    for campo in (
        "tiempo_scraping_s",
        "tiempo_chunking_s",
        "tiempo_embeddings_s",
        "tiempo_insercion_s",
        "tiempo_total_s",
    ):
        setattr(medidas, campo, round(getattr(medidas, campo), decimales))
    return medidas
