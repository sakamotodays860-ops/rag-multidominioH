#!/usr/bin/env python
"""Verificacion de fuentes oficiales candidatas (FASE 2) y muestreo de texto (FASE 3).

Herramienta de linea de comandos, sin Gemini ni base de datos:

* ``--verificar`` (por defecto): hace una peticion HTTP real a cada candidato y
  registra codigo HTTP, ``Content-Type``, tamano y firma ``%PDF-``.
* ``--muestras``: ademas descarga el documento y extrae su texto con
  :func:`app.services.pdf_extractor.extraer_texto_pdf` (o ``limpiar_html`` para
  HTML), mostrando un extracto y el numero de paginas.

Solo un candidato con ``http == 200`` y ``magic_pdf`` verdadero se considera
documento normativo verificable. Las paginas indice (HTML) se marcan como tales
y nunca se tratan como normativa incorporada.

Uso (dentro del contenedor):

    docker compose cp scripts/verificar_fuentes.py backend:/tmp/verificar_fuentes.py
    docker compose exec -T backend python /tmp/verificar_fuentes.py --muestras
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from app.services.pdf_extractor import detectar_pdf, extraer_texto_pdf
from app.utils.html_cleaner import limpiar_html

AGENTE = "Mozilla/5.0 (compatible; rag-multidominio/1.0)"

# Candidatos localizados por busqueda web en portales oficiales. No se inventa
# ninguna URL: todas provienen de asfi.gob.bo, bcb.gob.bo, impuestos.gob.bo,
# aduana.gob.bo, vuce.gob.bo o gacetaoficialdebolivia.gob.bo.
CANDIDATOS: list[dict[str, str]] = [
    {
        "dominio": "financiero",
        "titulo": "Ley N 393 de Servicios Financieros (oficial ASFI)",
        "url": "https://www.asfi.gob.bo/sites/default/files/2025-09/Ley%20N%C2%B0%20393%20de%20Servicios%20Financieros.pdf",
    },
    {
        "dominio": "financiero",
        "titulo": "Ley N 393 texto ordenado (ASFI)",
        "url": "https://www.asfi.gob.bo/sites/default/files/2025-07/Texto%20ordenado.pdf",
    },
    {
        "dominio": "financiero",
        "titulo": "ASFI Recopilacion de Normas para Servicios Financieros (indice)",
        "url": "https://www.asfi.gob.bo/la/recopilacion-normas-para-servicios-financieros-rnsf",
    },
    {
        "dominio": "financiero",
        "titulo": "ASFI Boletin normativo 73 (sep 2026)",
        "url": "https://www.asfi.gob.bo/sites/default/files/2026-10/Bolet%C3%ADn%2073%20-%20septiembre%202026.pdf",
    },
    {
        "dominio": "financiero",
        "titulo": "BCB Marco normativo (indice)",
        "url": "https://www.bcb.gob.bo/?q=marco-normativo",
    },
    {
        "dominio": "financiero",
        "titulo": "BCB RD 89/2026 Reglamento de Encaje Legal",
        "url": "https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%2089-2026%20%2826-06-2026%29.pdf",
    },
    {
        "dominio": "financiero",
        "titulo": "BCB RD 138/2026 Reglamento de Posicion de Cambios",
        "url": "https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20138-2026%20%2821-09-2026%29.pdf",
    },
    {
        "dominio": "financiero",
        "titulo": "BCB RD 142/2026 Reglamento de Operaciones Cambiarias",
        "url": "https://www.bcb.gob.bo/webdocs/01_resoluciones/RD%20N%C2%B0%20142-2026%20%2824-09-2026%29.pdf",
    },
    {
        "dominio": "tributario",
        "titulo": "Ley N 2492 Codigo Tributario Boliviano (SIN)",
        "url": "https://www.impuestos.gob.bo/wp-content/uploads/2026/08/2.-LEY-N%C2%B0-2492-07-26-1.pdf",
    },
    {
        "dominio": "tributario",
        "titulo": "Ley N 843 texto ordenado (SIN)",
        "url": "https://www.impuestos.gob.bo/wp-content/uploads/2026/09/1.-LEY-N%C2%B0-843-08-26.pdf",
    },
    {
        "dominio": "tributario",
        "titulo": "SIN indice de leyes tributarias",
        "url": "https://www.impuestos.gob.bo/index.php/leyes/",
    },
    {
        "dominio": "tributario",
        "titulo": "Ley N 1990 General de Aduanas (compilacion)",
        "url": "https://www.vuce.gob.bo/sites/default/files/2024-08/LEY%20GENERAL%20DE%20ADUANAS.pdf",
    },
    {
        "dominio": "tributario",
        "titulo": "Ley General de Aduanas (vista HTML Aduana Nacional)",
        "url": "https://www.aduana.gob.bo/lga-view",
    },
]


def verificar(candidato: dict[str, str], retener: list | None = None) -> dict:
    """Devuelve el resultado de verificar un candidato (sin Gemini)."""
    fila: dict = {"dominio": candidato["dominio"], "titulo": candidato["titulo"], "url": candidato["url"]}
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0, headers={"User-Agent": AGENTE}) as cliente:
            respuesta = cliente.get(candidato["url"])
        cuerpo = respuesta.content
        fila["http"] = respuesta.status_code
        fila["content_type"] = respuesta.headers.get("content-type", "")
        fila["bytes"] = len(cuerpo)
        fila["magic_pdf"] = cuerpo[:5] == b"%PDF-"
        fila["final_url"] = str(respuesta.url)
        if retener is not None:
            retener[0] = cuerpo
    except Exception as error:  # noqa: BLE001 - frontera de red
        fila["error"] = f"{type(error).__name__}: {error}"
    return fila


def muestrear(cuerpo: bytes, limite: int) -> str:
    """Extrae un extracto de texto del documento descargado (FASE 3)."""
    if detectar_pdf(cuerpo):
        from app.services.pdf_extractor import PdfInvalido, PdfSinTexto

        try:
            texto, paginas = extraer_texto_pdf(cuerpo)
        except (PdfInvalido, PdfSinTexto) as error:
            return f"[error de extraccion: {error}]"
        extracto = " ".join(texto.split())[:limite]
        return f"[PDF paginas={paginas} chars={len(texto)}] {extracto}"
    texto = limpiar_html(cuerpo.decode("utf-8", errors="replace"))
    extracto = " ".join(texto.split())[:limite]
    return f"[HTML chars={len(texto)}] {extracto}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="salida JSON de la verificacion")
    parser.add_argument("--muestras", action="store_true", help="extraer y mostrar texto")
    parser.add_argument("--limite", type=int, default=600, help="caracteres por muestra")
    parser.add_argument("--dominio", choices=["financiero", "tributario"], help="filtrar por dominio")
    argumentos = parser.parse_args(argv)

    candidatos = [c for c in CANDIDATOS if not argumentos.dominio or c["dominio"] == argumentos.dominio]
    filas = []
    for candidato in candidatos:
        cuerpo = [None]
        fila = verificar(candidato, cuerpo)
        if argumentos.muestras and fila.get("http") == 200 and cuerpo[0] is not None:
            fila["muestra"] = muestrear(cuerpo[0], argumentos.limite)
        filas.append(fila)

    if argumentos.json:
        print(json.dumps(filas, ensure_ascii=False, indent=2))
    else:
        for fila in filas:
            estado = fila.get("http", fila.get("error"))
            tipo = fila.get("content_type", "")
            marca = "PDF" if fila.get("magic_pdf") else "  -"
            print(f"[{marca}] {estado} {fila['dominio']:10s} {fila['titulo']}")
            if "bytes" in fila:
                print(f"        {tipo} {fila['bytes']} bytes  {fila['url']}")
            if "muestra" in fila:
                print(f"        muestra: {fila['muestra']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
