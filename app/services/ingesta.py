"""Orquestador de la ingesta web (FASE 3, requisito 1-7).

Encadena las cuatro fases del pipeline y mide cada una por separado con
:class:`MetricasIngesta` (requisito 7):

1. scraping / red   -> ``app.services.downloader``
2. chunking         -> ``app.utils.chunking``
3. embeddings       -> ``app.services.embeddings``
4. insercion        -> ``app.database.repository.insertar_fragmentos``

Contratos importantes:

* una URL fallida jamás aborta la ingesta (se reporta en ``errores``);
* los embeddings son obligatorios: sin ``GEMINI_API_KEY`` o con un fallo
  definitivo de Gemini se lanza :class:`ErrorEmbeddings` y NO se inserta
  ninguna fila parcial;
* la transaccion de insercion es atomica: o se insertan todas las filas o
  ninguna.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.database.repository import VECTOR_DIMENSION, insertar_fragmentos
from app.database.session import abrir_sesion
from app.models.ingesta import PeticionIngesta, RespuestaIngesta
from app.models.normativa import Embedding
from app.services.downloader import descargar_y_limpiar
from app.services.embeddings import ClienteEmbeddingsGemini, ErrorEmbeddings
from app.utils.chunking import dividir_en_fragmentos
from app.utils.metricas import MetricasIngesta, medir, redondear


@dataclass(frozen=True)
class FragmentoIngestado:
    """Un chunk de texto listo para embeddir."""

    contenido: str
    origen_url: str


@dataclass
class ResultadoIngesta:
    """Respuesta final + metricas (estas ultimas no van en el JSON)."""

    respuesta: RespuestaIngesta
    metricas: MetricasIngesta


async def ejecutar_ingesta(peticion: PeticionIngesta) -> ResultadoIngesta:
    """Ejecuta el pipeline completo de una ingesta web.

    Lanza :class:`ErrorEmbeddings` (mapena a HTTP 502) en fallos definitivos
    de embeddings y ``ValueError`` (mapeado a HTTP 400) en validaciones de
    dominio. Los fallos individuales de URL se reportan, pero no abortan.
    """
    peticion.validar()

    cfg = get_settings()
    metricas = MetricasIngesta()
    arranque_total = time.perf_counter()

    # ---------- 1. Scraping / red ----------
    with medir() as crono:
        descarga = await descargar_y_limpiar(
            peticion.urls,
            concurrency=peticion.concurrency_workers,
            timeout_s=cfg.http_timeout_s,
            user_agent=cfg.http_user_agent,
            max_bytes=cfg.http_max_bytes,
        )
    metricas.tiempo_scraping_s = crono.transcurrido

    # ---------- 2. Chunking ----------
    with medir() as crono:
        fragmentos: list[FragmentoIngestado] = []
        for url, texto in descarga.textos:
            for trozo in dividir_en_fragmentos(
                texto,
                chunk_size=peticion.chunk_size,
                chunk_overlap=peticion.chunk_overlap,
            ):
                fragmentos.append(FragmentoIngestado(trozo, url))
    metricas.tiempo_chunking_s = crono.transcurrido

    # ---------- 3. Embeddings (lotes) ----------
    cliente_emb = ClienteEmbeddingsGemini(
        api_key=cfg.gemini_api_key,
        modelo=cfg.gemini_embedding_model,
        dimension=cfg.pgvector_dimension,
        batch_size=min(max(1, cfg.gemini_batch_size), 100),
        max_reintentos=max(1, cfg.gemini_max_reintentos),
        backoff_base_s=cfg.gemini_backoff_base_s,
        backoff_max_s=cfg.gemini_backoff_max_s,
        timeout_s=cfg.gemini_timeout_s,
    )
    with medir() as crono:
        vectores = await cliente_emb.generar([f.contenido for f in fragmentos])
        _verificar_dimension(vectores)
    metricas.tiempo_embeddings_s = crono.transcurrido

    # ---------- 4. Insercion ----------
    registros = [
        {
            "contenido": frag.contenido,
            "origen_url": frag.origen_url,
            "embedding": vector,
        }
        for frag, vector in zip(fragmentos, vectores)
    ]
    with medir() as crono:
        async with abrir_sesion() as sesion:
            insertados = await insertar_fragmentos(
                sesion, coleccion_id=peticion.coleccion_id, registros=registros
            )
            await sesion.commit()
    metricas.tiempo_insercion_s = crono.transcurrido

    metricas.tiempo_total_s = round(time.perf_counter() - arranque_total, 6)
    num_error = len(descarga.errores)

    respuesta = RespuestaIngesta(
        coleccion_id=peticion.coleccion_id,
        urls_recibidas=len(peticion.urls),
        urls_procesadas=len(descarga.textos),
        urls_con_error=num_error,
        errores=descarga.errores,
        fragmentos_generados=len(fragmentos),
        embeddings_generados=len(vectores),
        registros_insertados=insertados,
        tiempo_scraping_s=metricas.tiempo_scraping_s,
        tiempo_chunking_s=metricas.tiempo_chunking_s,
        tiempo_embeddings_s=metricas.tiempo_embeddings_s,
        tiempo_insercion_s=metricas.tiempo_insercion_s,
        tiempo_total_s=metricas.tiempo_total_s,
    )
    return ResultadoIngesta(
        respuesta=respuesta, metricas=redondear(metricas)
    )


def _verificar_dimension(vectores: list[Embedding]) -> None:
    """Defensa en profundidad: 768 dims por cuestion de compatibilidad."""
    if any(len(v) != VECTOR_DIMENSION for v in vectores):
        raise ErrorEmbeddings(
            f"Se generaron embeddings de dimension != {VECTOR_DIMENSION}; no "
            "se puede insertar en VECTOR(768)."
        )