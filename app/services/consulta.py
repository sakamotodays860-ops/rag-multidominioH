"""Consulta RAG multi-dominio (FASE 4).

Oquesta el flujo completo de ``POST /api/consultar``:

1. Embeber la pregunta con ``ClienteEmbeddingsGemini`` (REQUISITO 2: mismo
   modelo/dimension que FASE 3, 768 dims).
2. Recuperar fragmentos SOLO de la colección pedida vía la RPC existente
   ``match_normativa_coleccion`` (ninguna otra consulta SQL basada en
   ``embedding_cosine_distance`` fuera de la RPC).
3. Aislamiento estricto (REQUISITO 5): si algo ajeno a la coleccion pedida se
   colara, se levanta :class:`ErrorContaminacion` y la evidencia NUNCA llega
   al LLM. Con la RPC correcta esto no deberia ocurrir; es el cortafuegos.
4. Si no hay evidencia, se responde SIN invocar el LLM (REQUISITO 6) con
   ``total_evidencias=0``.
5. Con evidencia si, se construye el prompt con instrucciones estrictas
   (responder SOLO con la evidencia) y se llama a ``ClienteGeneracionGemini``
   con ``temperatura=0.0`` (REQUISITO 3), devolviendo la
   :class:`RespuestaConsulta` estructurada (REQUISITO 7). La temperatura y el
   prompt estan a la vista/verificables en los tests (REQUISITO 8).

Dependencias inyectables (``cliente_embeddings``, ``cliente_generacion``,
``abrir``) para que los tests ejecuten el flujo sin tocar red ni base de datos.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.database.repository import buscar_por_coleccion
from app.database.session import abrir_sesion
from app.models.consulta import (
    EvidenciaConsulta,
    PeticionConsulta,
    RespuestaConsulta,
)
from app.services.embeddings import (
    ClienteEmbeddingsGemini,
    ErrorEmbeddings,
)
from app.services.generacion import ClienteGeneracionGemini, ErrorGeneracion

# Texto que se devuelve cuando NO hay evidencia (el LLM no se invoca).
SIN_EVIDENCIA = (
    "No se encontro evidencia suficiente en la normativa de la coleccion "
    "solicitada para responder la pregunta."
)

INSTRUCCION_SISTEMA = (
    "Eres un asistente de consulta RAG sobre normativa financiera y "
    "tributaria de Bolivia.\n"
    "Responde en espanol, de forma clara y si breve como sea preciso, usando "
    "UNICAMENTE la informacion contenida en la evidencia recuperada de la "
    "coleccion indicada en el contexto.\n"
    "REGLAS OBLIGATORIAS:\n"
    "- No inventes articulos, leyes, fechas, instituciones, sanciones, "
    "obligaciones, numeros ni datos que no esten presentes en el contexto "
    "de la evidencia.\n"
    "- Si la evidencia no contiene informacion suficiente para responder, "
    "indicalo expresamente en lugar de completar con conocimiento externo.\n"
    "- Si citas el origen, hazlo como 'normativa recuperada' sin inventar "
    "numeros de articulo o referencias que no aparezcan en el contexto."
)


class ErrorBaseDatos(RuntimeError):
    """Fallo al ejecutar la RPC de recuperacion en la base de datos."""


class ErrorContaminacion(RuntimeError):
    """La RPC devolvio filas de una coleccion distinta a la solicitada.

    Como el RPC debe filtrar por ``coleccion_id``, su presencia indica una
    fuga de aislamiento; la consulta se ABORTA sin enviar nada al LLM.
    """


def _construir_prompt(
    peticion: PeticionConsulta, evidencias: list[EvidenciaConsulta]
) -> str:
    """Prompt: contexto unicamente de la coleccion pedida + pregunta del usuario."""
    contexto = "\n\n".join(
        f"[Evidencia {indice}] (coleccion: {ev.coleccion_id}, "
        f"similitud: {ev.similitud:.3f}, origen: {ev.origen_url or 'n/d'})\n"
        f"{ev.contenido}"
        for indice, ev in enumerate(evidencias, start=1)
    )
    return (
        f"{INSTRUCCION_SISTEMA}\n\n"
        f"CONTEXTO RECUPERADO (unica y exclusivamente de la coleccion "
        f"'{peticion.coleccion_id}'):\n{contexto}\n\n"
        f"PREGUNTA DEL USUARIO:\n{peticion.pregunta}\n\n"
        f"Responde ahora usando solo la evidencia proporcionada."
    )


async def ejecutar_consulta(
    peticion: PeticionConsulta,
    *,
    cliente_embeddings: ClienteEmbeddingsGemini | None = None,
    cliente_generacion: ClienteGeneracionGemini | None = None,
    abrir: Callable[[], Any] = abrir_sesion,
) -> RespuestaConsulta:
    """Ejecuta el flujo completo de la consulta contra UNA coleccion.

    Inyectando ``abrir``, ``cliente_embeddings`` y ``cliente_generacion`` los
    tests aíslan el flujo y validan cada requisito sin dependencias externas.
    """
    peticion.validar()
    config = get_settings()

    # 1. Embedding de la pregunta (REQUISITO 2: mismo modelo/dimension FASE 3).
    cliente = cliente_embeddings or ClienteEmbeddingsGemini(
        api_key=config.gemini_api_key,
        modelo=config.gemini_embedding_model,
        dimension=config.pgvector_dimension,
    )
    vectores = await cliente.generar([peticion.pregunta])
    if len(vectores) != 1:
        raise ErrorEmbeddings(
            "Se esperaba 1 embedding para la pregunta y Gemini devolvio "
            f"{len(vectores)}."
        )
    embedding = vectores[0]
    if len(embedding) != config.pgvector_dimension:
        raise ErrorEmbeddings(
            f"Embedding de {len(embedding)} dimensiones; se esperaban "
            f"{config.pgvector_dimension} (incompatible con VECTOR(768))."
        )

    # 2. Rutina vectorial de la coleccion pedida via RPC existente.
    async with abrir() as sesion:
        try:
            filas = await buscar_por_coleccion(
                sesion,
                coleccion_id=peticion.coleccion_id,
                query_embedding=embedding,
                match_threshold=peticion.similarity_threshold,
                match_count=peticion.top_k,
            )
        except ValueError:
            # Entrada no valida (coleccion malformada, umbral fuera de rango,
            # etc.): el error de dominio viaja intacto para el 400.
            raise
        except Exception as error:  # noqa: BLE001 -- frontera de servicios.
            raise ErrorBaseDatos(
                "Error de base de datos al consultar la coleccion "
                f"'{peticion.coleccion_id}': {type(error).__name__}: {error}"
            ) from error

    # 3. Aislamiento estricto: verificar que TODO vino de la coleccion pedida.
    if any(fila.coleccion_id != peticion.coleccion_id for fila in filas):
        ajenas = {fila.coleccion_id for fila in filas} - {peticion.coleccion_id}
        raise ErrorContaminacion(
            "Deteccion de contaminacion cruzada: la RPC de la coleccion "
            f"'{peticion.coleccion_id}' devolvio ademas filas de: "
            f"{sorted(ajenas)}. Se aborta la consulta sin enviar contexto al "
            "LLM."
        )
    evidencias = [
        EvidenciaConsulta(
            id=fila.id,
            coleccion_id=fila.coleccion_id,
            origen_url=fila.origen_url,
            similitud=fila.similitud,
            contenido=fila.contenido,
        )
        for fila in filas
    ]

    # 4. Sin evidencia -> respuesta controlada, SIN LLM (REQUISITO 6).
    if not evidencias:
        return RespuestaConsulta(
            coleccion_id=peticion.coleccion_id,
            pregunta=peticion.pregunta,
            respuesta=SIN_EVIDENCIA,
            evidencias=[],
            total_evidencias=0,
        )

    # 5. Generacion con temperatura 0.0 y contexto (REQUISITO 3/6/8/9).
    generador = cliente_generacion or ClienteGeneracionGemini(
        api_key=config.gemini_api_key,
        modelo=config.gemini_model,
        temperatura=config.gemini_temperatura,
        max_reintentos=config.gemini_max_reintentos,
        backoff_base_s=config.gemini_backoff_base_s,
        backoff_max_s=config.gemini_backoff_max_s,
        timeout_s=config.gemini_timeout_s,
    )
    prompt = _construir_prompt(peticion, evidencias)
    respuesta = await generador.generar(prompt)
    return RespuestaConsulta(
        coleccion_id=peticion.coleccion_id,
        pregunta=peticion.pregunta,
        respuesta=respuesta,
        evidencias=evidencias,
        total_evidencias=len(evidencias),
    )