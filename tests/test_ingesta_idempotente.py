"""Pruebas de idempotencia de la ingesta frente a contenido ya almacenado.

Estrategia de seguridad: TODAS las inserciones ocurren dentro de la transaccion
de la sesion de prueba y terminan con ``rollback``. No se confirma ni una fila:
la base activa (80 fragmentos) y `benchmark_2026` quedan intactos.

Cubren los tres casos que exige el enunciado:

* (a) mismo contenido y misma URL -> no se reinserta;
* (b) mismo contenido con URL distinta -> no se reinserta (dedup por contenido);
* (c) contenido distinto desde la misma URL -> SI se conserva (no se deduplica
      por URL).

Ademas cubren el aislamiento por coleccion, la normalizacion de espacios y
mayusculas, la inmutabilidad de `benchmark_2026` y la exclusion del cerrojo por
coleccion.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from helpers import vector_financiero

from app.database.repository import (
    contar_coleccion,
    huellas_existentes,
    insertar_fragmentos_sin_duplicados,
)
from app.utils.dedup import huella_contenido

COLECCION_A = "fase4_idempotencia_a"
COLECCION_B = "fase4_idempotencia_b"
COLECCION_BENCHMARK = "benchmark_2026"


def _registro(contenido: str, url: str = "https://example.com/x") -> dict:
    return {
        "contenido": contenido,
        "origen_url": url,
        "embedding": vector_financiero(),
    }


# --- (a) misma URL y mismo contenido -----------------------------------------


async def test_segunda_ingesta_identica_no_inserta(sesion):
    registros = [_registro("Articulo 1 unico."), _registro("Articulo 2 unico.")]

    primera = await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_A, registros=registros
    )
    assert primera.insertados == 2
    assert primera.omitidos == 0

    segunda = await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_A, registros=registros
    )
    assert segunda.insertados == 0
    assert segunda.omitidos == 2
    assert await contar_coleccion(sesion, COLECCION_A) == 2

    await sesion.rollback()


async def test_ingesta_parcialmente_repetida_inserta_solo_lo_nuevo(sesion):
    await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_A, registros=[_registro("A"), _registro("B")]
    )
    resultado = await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("A"), _registro("B"), _registro("C")],
    )
    assert resultado.insertados == 1
    assert resultado.omitidos == 2
    assert await contar_coleccion(sesion, COLECCION_A) == 3

    await sesion.rollback()


# --- (b) mismo contenido con URL distinta ------------------------------------


async def test_mismo_contenido_con_url_distinta_no_duplica(sesion):
    await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("Ley 393 texto oficial.", "https://a.gob.bo/ley393")],
    )
    resultado = await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("Ley 393 texto oficial.", "https://b.gob.bo/copia")],
    )
    assert resultado.insertados == 0
    assert resultado.omitidos == 1
    assert await contar_coleccion(sesion, COLECCION_A) == 1

    await sesion.rollback()


# --- (c) contenido distinto desde la misma URL -------------------------------


async def test_contenido_distinto_misma_url_se_conserva(sesion):
    resultado = await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[
            _registro("Version anterior del reglamento.", "https://x.org/reglamento"),
            _registro("Version actualizada del reglamento.", "https://x.org/reglamento"),
        ],
    )
    assert resultado.insertados == 2
    assert resultado.omitidos == 0
    assert await contar_coleccion(sesion, COLECCION_A) == 2

    await sesion.rollback()


# --- normalizacion -----------------------------------------------------------


async def test_normalizacion_de_espacios_y_mayusculas(sesion):
    await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("Articulo  1\n\tdel   reglamento")],
    )
    resultado = await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("articulo 1 del reglamento")],
    )
    assert resultado.insertados == 0
    assert resultado.omitidos == 1

    await sesion.rollback()


# --- aislamiento por coleccion -----------------------------------------------


async def test_mismo_contenido_en_distinta_coleccion_es_valido(sesion):
    mismo = [_registro("Norma compartida entre dominios.")]

    en_a = await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_A, registros=mismo
    )
    en_b = await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_B, registros=mismo
    )
    assert en_a.insertados == 1
    assert en_b.insertados == 1
    assert await contar_coleccion(sesion, COLECCION_A) == 1
    assert await contar_coleccion(sesion, COLECCION_B) == 1

    await sesion.rollback()


async def test_huellas_existentes_refleja_lo_insertado(sesion):
    await insertar_fragmentos_sin_duplicados(
        sesion, coleccion_id=COLECCION_A, registros=[_registro("Hola mundo")]
    )
    huellas = await huellas_existentes(sesion, coleccion_id=COLECCION_A)
    assert huella_contenido("Hola mundo") in huellas
    assert huella_contenido("Otra cosa") not in huellas

    await sesion.rollback()


# --- benchmark intacto -------------------------------------------------------


async def test_benchmark_no_se_altera(sesion):
    antes = await contar_coleccion(sesion, COLECCION_BENCHMARK)
    await insertar_fragmentos_sin_duplicados(
        sesion,
        coleccion_id=COLECCION_A,
        registros=[_registro("Contenido que no debe tocar el benchmark.")],
    )
    assert await contar_coleccion(sesion, COLECCION_BENCHMARK) == antes

    await sesion.rollback()


# --- concurrencia y cerrojo --------------------------------------------------


async def test_cerrojo_por_coleccion_es_excluyente(motor):
    """Dos sesiones no pueden tomar el cerrojo de la MISMA coleccion a la vez.

    No modifica datos: solo adquiere cerrojos de transaccion y hace rollback.
    Comprueba que la serializacion de ingestas concurrentes de una coleccion
    funciona y que colecciones distintas NO se bloquean entre si.
    """
    factoria = async_sessionmaker(motor, expire_on_commit=False)
    async with factoria() as sesion_a, factoria() as sesion_b:
        tomar = text(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(:c, 0))"
        )

        assert (
            await sesion_a.execute(tomar, {"c": COLECCION_A})
        ).scalar_one() is True
        assert (
            await sesion_b.execute(tomar, {"c": COLECCION_A})
        ).scalar_one() is False
        assert (
            await sesion_b.execute(tomar, {"c": COLECCION_B})
        ).scalar_one() is True

        await sesion_a.rollback()
        await sesion_b.rollback()


async def test_toma_el_cerrojo_antes_de_comprobar_y_insertar():
    """La primera sentencia de la insercion idempotente es el cerrojo.

    Prueba unitaria con una sesion falsa (sin red ni base de datos) que captura
    el orden de las sentencias: cerrojo -> lectura de existentes -> insercion.
    """

    class _ResultadoFalso:
        def scalars(self):
            return self

        def all(self):
            return []

    class _SesionFalsa:
        def __init__(self) -> None:
            self.sentencias: list[str] = []

        async def execute(self, sentencia, parametros=None):  # noqa: ANN001
            self.sentencias.append(str(sentencia))
            return _ResultadoFalso()

    sesion = _SesionFalsa()
    resultado = await insertar_fragmentos_sin_duplicados(
        sesion,  # type: ignore[arg-type]
        coleccion_id=COLECCION_A,
        registros=[_registro("Nuevo fragmento para el cerrojo.")],
    )

    assert "pg_advisory_xact_lock" in sesion.sentencias[0]
    assert any("SELECT contenido" in s for s in sesion.sentencias[1:])
    assert any("INSERT INTO normativa_bancaria" in s for s in sesion.sentencias)
    assert resultado.insertados == 1
