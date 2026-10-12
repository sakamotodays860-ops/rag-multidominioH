"""Esquemas y validacion de la FASE 2: aislamiento vectorial multi-dominio."""

import re
from typing import Annotated

from pydantic import BaseModel, Field

# Embedding tal y como lo espera pgvector.
Embedding = Annotated[list[float], Field(min_length=1)]

# Limite de caracteres de `coleccion_id`, espejo de VARCHAR(50) en el esquema.
COLECCION_ID_MAX_LEN = 50

# Formato admitido: identificador ASCII (letras, digitos, '_', '-', '.').
# Se descartan espacios y caracteres exoticos para que la coleccion sea
# representable tanto en la URL como en la columna de la base de datos.
COLECCION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def validar_coleccion_id(coleccion_id: str) -> str:
    """Valida `coleccion_id` tal y como lo exige la API.

    Reglas:
      1. no vacio ni solo espacios;
      2. sin espacios al inicio o al final;
      3. hasta 50 caracteres (VARCHAR(50));
      4. solo caracteres de identificador ASCII.

    Devuelve el valor normalizado o lanza `ValueError` con un mensaje apto
    para devolverlo al cliente como error 400.
    """
    if not isinstance(coleccion_id, str) or not coleccion_id.strip():
        raise ValueError("coleccion_id no puede estar vacio")
    if coleccion_id != coleccion_id.strip():
        raise ValueError("coleccion_id no puede contener espacios al inicio o al final")
    if len(coleccion_id) > COLECCION_ID_MAX_LEN:
        raise ValueError(
            f"coleccion_id supera los {COLECCION_ID_MAX_LEN} caracteres"
        )
    if not COLECCION_ID_RE.match(coleccion_id):
        raise ValueError(
            "coleccion_id solo admite letras, digitos, '_', '-' y '.'"
        )
    return coleccion_id


class NormativaMatch(BaseModel):
    """Fila devuelta por la funcion `match_normativa_coleccion`.

    `coleccion_id` se incluye en el resultado de forma deliberada: permite que
    el consumidor (y las pruebas) verifique que TODA fila devuelta pertenece a
    la coleccion solicitada, sin confiar en la buena fe del llamador.
    """

    id: int
    coleccion_id: str
    contenido: str
    origen_url: str | None = None
    distancia: float
    similitud: float


class ColeccionInfo(BaseModel):
    """Respuesta del endpoint de diagnostico de una coleccion."""

    coleccion_id: str
    existe: bool
    fragmentos: int


def embedding_a_literal_pgvector(embedding: Embedding) -> str:
    """Convierte una lista de floats al formato literal de pgvector.

    Se usa el formato `'[0.1,0.2,...]'` y un cast explicito a `vector` en el
    SQL, en lugar de un adaptador del driver: asi la misma consulta funciona
    con cualquier driver sin dependencias adicionales.
    """
    if not embedding:
        raise ValueError("el embedding no puede estar vacio")
    return "[" + ",".join(repr(float(valor)) for valor in embedding) + "]"