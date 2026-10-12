"""Constantes y constructores de vectores para las pruebas de la FASE 2.

Este modulo es el origen unico de verdad de los datos de prueba dentro del
suite. Las pruebas lo importan en lugar de duplicar las colecciones.

Las pruebas NO insertan datos: solo leen y exigen que estos registros existan
con el tamano documentado.
"""

from app.database.repository import VECTOR_DIMENSION

COLECCION_FINANCIERO = "financiero_bolivia_2026"
COLECCION_TRIBUTARIO = "tributario_bolivia_2026"

# Tamano de los datos de demostracion cargados por seeds/aislamiento_demo.sql.
FRAGMENTOS_ESPERADOS = 4
TOTAL_ESPERADO = FRAGMENTOS_ESPERADOS * 2


def vector_de_dominio(indice_eje: int) -> list[float]:
    """Vector concentrado en la componente `indice_eje`.

    Replica la distribucion usada por `seeds/aislamiento_demo.sql`, donde la
    distancia coseno dentro de un mismo dominio es cercana a 0 y entre dominios
    cercana a 1.0.
    """
    if not 0 <= indice_eje < VECTOR_DIMENSION:
        raise ValueError("indice_eje fuera de rango")
    vector = [0.01] * VECTOR_DIMENSION
    vector[indice_eje] = 1.0
    return vector


def vector_financiero() -> list[float]:
    """Consulta orientada al dominio financiero (componente 0)."""
    return vector_de_dominio(0)


def vector_tributario() -> list[float]:
    """Consulta orientada al dominio tributario (componente 1)."""
    return vector_de_dominio(1)