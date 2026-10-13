"""Consulta conversacional con deteccion automatica de dominio (fase H).

Encapsula ``POST /api/consultar-auto``: dado un texto en lenguaje natural,
decide de forma determinista entre las colecciones financiera y tributaria,
recupera evidencia con el MISMO aislamiento que FASE 4 y genera la respuesta.

Decisiones de seguridad:

* si la pregunta es ambigua o ajena a los dominios, NO se consulta ninguna
  coleccion y se pide aclaracion;
* si la unica evidencia procede de datos de prueba (``seed://``), NO se invoca
  al LLM: se informa de que no hay normativa real indexada;
* la generacion usa SOLO evidencia real (se excluyen los fragmentos
  ``seed://``), de modo que nunca se redacta normativa a partir de datos
  ficticios.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.database.repository import listar_colecciones
from app.database.session import abrir_sesion
from app.models.consulta import (
    PeticionConsulta,
    PeticionConsultaAuto,
    RespuestaConsultaAuto,
)
from app.services.clasificacion import (
    DOMINIO_FINANCIERO,
    DOMINIO_TRIBUTARIO,
    clasificar,
)
from app.services.consulta import (
    MENSAJE_SOLO_RECUPERACION,
    SIN_EVIDENCIA,
    generar_respuesta,
    recuperar_evidencias,
)
from app.services.embeddings import ClienteEmbeddingsGemini
from app.services.generacion import ClienteGeneracionGemini

ACLARACION_AMBIGUA = (
    "La pregunta puede corresponder a normativa financiera o tributaria. "
    "Indica el dominio (por ejemplo 'financiero' o 'tributario') o menciona "
    "la materia concreta para seleccionar la coleccion correcta."
)

FUERA_DE_DOMINIO = (
    "Este asistente esta especializado en normativa financiera y tributaria "
    "de Bolivia. La pregunta no parece corresponder a esos dominios; "
    "reformulela o indique la materia."
)

COLECCION_NO_DISPONIBLE = (
    "La coleccion '{coleccion}' (dominio '{dominio}') todavia no tiene "
    "documentos indexados, por lo que no es posible recuperar evidencia. "
    "Ingesta normativa oficial en esa coleccion antes de consultar."
)

ADVERTENCIA_SEED_UNICA = (
    "La unica evidencia recuperada procede de datos de prueba (seed://) y NO "
    "constituye normativa real. No se genero una respuesta normativa. Ingesta "
    "documentos oficiales en esta coleccion para consultas reales."
)

ADVERTENCIA_SEED_PARCIAL = (
    "Parte de la evidencia procede de datos de prueba (seed://); esos "
    "fragmentos se marcan como 'datos de prueba' y no deben tomarse como "
    "normativa real."
)


def _coleccion_para(dominio: str) -> str:
    """Mapea el dominio detectado a la coleccion configurada."""
    config = get_settings()
    if dominio == DOMINIO_FINANCIERO:
        return config.coleccion_financiera
    if dominio == DOMINIO_TRIBUTARIO:
        return config.coleccion_tributaria
    raise ValueError(f"dominio no soportado: {dominio}")


def _dominio_de_coleccion(coleccion_id: str) -> str | None:
    """Infiere el dominio a partir del nombre de coleccion configurado."""
    config = get_settings()
    if coleccion_id == config.coleccion_financiera:
        return DOMINIO_FINANCIERO
    if coleccion_id == config.coleccion_tributaria:
        return DOMINIO_TRIBUTARIO
    return None


def _respuesta_controlada(
    peticion: PeticionConsultaAuto,
    mensaje: str,
    *,
    dominio: str | None = None,
    coleccion: str | None = None,
    ambigua: bool = False,
    aclaracion: str | None = None,
    advertencia: str | None = None,
) -> RespuestaConsultaAuto:
    return RespuestaConsultaAuto(
        pregunta=peticion.pregunta,
        dominio=dominio,
        coleccion_id=coleccion,
        respuesta=mensaje,
        evidencias=[],
        total_evidencias=0,
        ambigua=ambigua,
        aclaracion=aclaracion,
        advertencia=advertencia,
    )


async def ejecutar_consulta_auto(
    peticion: PeticionConsultaAuto,
    *,
    cliente_embeddings: ClienteEmbeddingsGemini | None = None,
    cliente_generacion: ClienteGeneracionGemini | None = None,
    abrir: Callable[[], Any] = abrir_sesion,
    colecciones_disponibles: set[str] | None = None,
    usar_lexico: bool = False,
    permitir_lexico_sin_embedding: bool = False,
) -> RespuestaConsultaAuto:
    """Flujo conversacional: deteccion -> verificacion -> recuperacion -> generacion.

    ``colecciones_disponibles`` permite inyectar el conjunto de colecciones en
    pruebas; si es ``None`` se consulta la base de datos. ``usar_lexico`` activa
    la busqueda hibrida (vectorial + lexica con fusion RRF);
    ``permitir_lexico_sin_embedding`` permite continuar solo con la busqueda
    lexica si el embedding de la pregunta falla.
    """
    peticion.validar()

    # 1. Seleccion explicita o deteccion de dominio.
    if peticion.coleccion_id:
        coleccion = peticion.coleccion_id
        dominio = _dominio_de_coleccion(coleccion)
    elif peticion.dominio:
        dominio = peticion.dominio
        coleccion = _coleccion_para(dominio)
    else:
        clasificacion = clasificar(peticion.pregunta)
        if clasificacion.dominio is None:
            return _respuesta_controlada(
                peticion,
                ACLARACION_AMBIGUA if clasificacion.ambigua else FUERA_DE_DOMINIO,
                ambigua=clasificacion.ambigua,
                aclaracion=clasificacion.motivo,
            )
        dominio = clasificacion.dominio
        coleccion = _coleccion_para(dominio)

    # 2. La coleccion objetivo debe existir.
    disponibles = colecciones_disponibles
    if disponibles is None:
        async with abrir() as sesion:
            disponibles = {cid for cid, _ in await listar_colecciones(sesion)}
    if coleccion not in disponibles:
        return _respuesta_controlada(
            peticion,
            COLECCION_NO_DISPONIBLE.format(coleccion=coleccion, dominio=dominio),
            dominio=dominio,
            coleccion=coleccion,
        )

    # 3. Recuperacion aislada (sin LLM todavia).
    peticion_base = PeticionConsulta(
        coleccion_id=coleccion,
        pregunta=peticion.pregunta,
        top_k=peticion.top_k,
        similarity_threshold=peticion.similarity_threshold,
        generar=peticion.generar,
    )
    evidencias = await recuperar_evidencias(
        peticion_base,
        cliente_embeddings=cliente_embeddings,
        abrir=abrir,
        usar_lexico=usar_lexico,
        permitir_lexico_sin_embedding=permitir_lexico_sin_embedding,
    )

    if not evidencias:
        return _respuesta_controlada(
            peticion, SIN_EVIDENCIA, dominio=dominio, coleccion=coleccion
        )

    # 4. Nunca redactar normativa a partir de datos de prueba.
    reales = [ev for ev in evidencias if not ev.es_datos_prueba]
    if not reales:
        resultado = _respuesta_controlada(
            peticion,
            ADVERTENCIA_SEED_UNICA,
            dominio=dominio,
            coleccion=coleccion,
            advertencia=ADVERTENCIA_SEED_UNICA,
        )
        resultado.evidencias = evidencias
        resultado.total_evidencias = len(evidencias)
        return resultado

    # 4b. Solo recuperacion: se devuelve la evidencia SIN invocar al LLM.
    if not peticion.generar:
        resultado = _respuesta_controlada(
            peticion, MENSAJE_SOLO_RECUPERACION, dominio=dominio, coleccion=coleccion
        )
        resultado.evidencias = evidencias
        resultado.total_evidencias = len(evidencias)
        resultado.solo_recuperacion = True
        return resultado

    # 5. Generacion con evidencia real.
    respuesta = await generar_respuesta(
        peticion_base, reales, cliente_generacion=cliente_generacion
    )
    advertencia = (
        ADVERTENCIA_SEED_PARCIAL if len(reales) < len(evidencias) else None
    )
    return RespuestaConsultaAuto(
        pregunta=peticion.pregunta,
        dominio=dominio,
        coleccion_id=coleccion,
        respuesta=respuesta,
        evidencias=evidencias,
        total_evidencias=len(evidencias),
        advertencia=advertencia,
    )
