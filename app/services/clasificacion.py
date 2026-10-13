"""Clasificacion de dominio de una pregunta (fase H: chat conversacional).

Se usa una heuristica determinista por palabras clave, sin llamar a Gemini:
asi el enrutado es barato, reproducible y verificable en pruebas. El objetivo
NO es comprender lenguaje natural de forma perfecta, sino elegir de forma
segura entre las colecciones indexadas (financiera / tributaria) y pedir
aclaracion cuando la pregunta es ambigua o ajena a esos dominios.

Regla de seguridad clave: ante duda, el resultado es ``ambigua=True`` y NO se
elige coleccion; el sistema pide aclaracion en lugar de consultar a ciegas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Palabras clave por dominio. Se normalizan (minusculas, sin acentos) antes de
# comparar. La lista prioriza terminos inequivocamente normativos.
PALABRAS_FINANCIERO: frozenset[str] = frozenset(
    {
        "financiero",
        "financiera",
        "asfi",
        "bancario",
        "bancaria",
        "banco",
        "bancos",
        "bcb",
        "monetario",
        "monetaria",
        "credito",
        "creditos",
        "prestamo",
        "prestamos",
        "ahorro",
        "ahorros",
        "deposito",
        "depositos",
        "seguro",
        "seguros",
        "pension",
        "pensiones",
        "valores",
        "bolsa",
        "fideicomiso",
        "cooperativa",
        "microfinanzas",
        "microcredito",
        "tarjeta",
        "interes",
        "tasa",
        "liquidez",
        "solvencia",
        "entidad financiera",
        "entidades financieras",
        "entidad de intermediacion financiera",
        "intermediacion financiera",
        "financieras",
        "bancarias",
        "bancarios",
        "servicios financieros",
        "sistema financiero",
        "lavado de activos",
        "prevencion de legitimacion",
        "extraccion de fondos",
        "supervision",
        "regulacion financiera",
        # Terminos de la RD BCB 138/2026 (Reglamento de Posicion de Cambios) y
        # normativa cambiaria del BCB. Sin ellos, preguntas sobre el documento
        # realmente ingerido quedaban fuera de dominio.
        "posicion de cambios",
        "posiciones de cambios",
        "operaciones cambiarias",
        "mercado cambiario",
        "tipo de cambio",
        "tipos de cambio",
        "cambiario",
        "cambiaria",
        "cambiarios",
        "cambiarias",
        "encaje",
        "encaje legal",
        "reserva de encaje",
    }
)

PALABRAS_TRIBUTARIO: frozenset[str] = frozenset(
    {
        "tributario",
        "tributaria",
        "tributo",
        "tributos",
        "impuesto",
        "impuestos",
        "sin",
        "iva",
        "it",
        "iue",
        "itf",
        "isae",
        "rc-iva",
        "regimen",
        "regimenes",
        "factura",
        "facturas",
        "facturacion",
        "contribuyente",
        "contribuyentes",
        "nit",
        "ruc",
        "declaracion",
        "declaracion jurada",
        "retencion",
        "retenciones",
        "credito fiscal",
        "comprobante",
        "comprobantes",
        "fiscal",
        "aduana",
        "arancel",
        "aranceles",
        "importacion",
        "exportacion",
        "tributacion",
        "servicio de impuestos nacionales",
        "alicuota",
        "exencion",
        "base imponible",
        "recaudacion",
    }
)

DOMINIO_FINANCIERO = "financiero"
DOMINIO_TRIBUTARIO = "tributario"


@dataclass(frozen=True)
class Clasificacion:
    """Resultado de clasificar una pregunta."""

    dominio: str | None
    ambigua: bool
    puntaje_financiero: int
    puntaje_tributario: int
    motivo: str


def _normalizar(texto: str) -> str:
    """Minusculas y sin acentos, conservando letras/numeros y espacios."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos).strip()


def _puntuar(normalizado: str, palabras: frozenset[str]) -> int:
    """Cuenta coincidencias de palabras clave (por token o frase completa)."""
    puntaje = 0
    for palabra in palabras:
        if " " in palabra:
            if palabra in normalizado:
                puntaje += 1
        else:
            if re.search(rf"\b{re.escape(palabra)}\b", normalizado):
                puntaje += 1
    return puntaje


def clasificar(pregunta: str) -> Clasificacion:
    """Clasifica ``pregunta`` en financiero, tributario o ambigua.

    * Si ambas puntuaciones son 0 -> fuera de dominio (``dominio=None``,
      ``ambigua=False``) para dar un mensaje de especializacion.
    * Si empatan con puntuacion > 0 -> ambigua (no se consulta).
    * Si una supera a la otra -> ese dominio.
    """
    normalizado = _normalizar(pregunta or "")
    puntaje_f = _puntuar(normalizado, PALABRAS_FINANCIERO)
    puntaje_t = _puntuar(normalizado, PALABRAS_TRIBUTARIO)

    if puntaje_f == 0 and puntaje_t == 0:
        return Clasificacion(
            dominio=None,
            ambigua=False,
            puntaje_financiero=0,
            puntaje_tributario=0,
            motivo="La pregunta no menciona terminos financieros ni tributarios.",
        )
    if puntaje_f == puntaje_t:
        return Clasificacion(
            dominio=None,
            ambigua=True,
            puntaje_financiero=puntaje_f,
            puntaje_tributario=puntaje_t,
            motivo=(
                "La pregunta menciona terminos de ambos dominios por igual; "
                "se requiere aclaracion."
            ),
        )
    dominio = DOMINIO_FINANCIERO if puntaje_f > puntaje_t else DOMINIO_TRIBUTARIO
    return Clasificacion(
        dominio=dominio,
        ambigua=False,
        puntaje_financiero=puntaje_f,
        puntaje_tributario=puntaje_t,
        motivo=f"Coincidencias: financiero={puntaje_f}, tributario={puntaje_t}.",
    )
