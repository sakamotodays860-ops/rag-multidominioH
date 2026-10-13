"""Huella de contenido compartida para deduplicar (una sola normalizacion).

Tanto el deduplicado dentro de una peticion (``app.services.downloader``) como
el deduplicado frente a lo ya almacenado (``app.database.repository``) deben
usar EXACTAMENTE la misma normalizacion; de lo contrario un mismo fragmento
podria considerarse nuevo por una via y duplicado por la otra. Este modulo es
la unica fuente de verdad.
"""

from __future__ import annotations

import hashlib


def normalizar_contenido(texto: str) -> str:
    """Normaliza un fragmento: colapsa espacios y unifica mayusculas/acentos.

    Se colapsan todos los espacios en blanco (incluidos saltos de linea y
    tabuladores) a un unico espacio y se pasa a minusculas insensibles al caso
    (``casefold``), de modo que dos textos equivalentes producidos por la
    limpieza de HTML/PDF tengan la misma huella.
    """
    return " ".join(texto.split()).casefold()


def huella_contenido(texto: str) -> str:
    """Devuelve la huella SHA-256 (hex) del contenido normalizado."""
    return hashlib.sha256(normalizar_contenido(texto).encode("utf-8")).hexdigest()
