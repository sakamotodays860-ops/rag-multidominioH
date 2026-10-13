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
from app.database.repository import (
    VECTOR_DIMENSION,
    huellas_existentes,
    insertar_fragmentos_sin_duplicados,
)
from app.database.session import abrir_sesion
from app.models.ingesta import (
    DocumentoIngestado,
    ErrorPorUrl,
    PeticionIngesta,
    RespuestaIngesta,
    RespuestaPrevisualizacion,
)
from app.models.normativa import Embedding
from app.services.downloader import DocumentoDescargado, ResultadoDescarga, descargar_y_limpiar
from app.services.embeddings import ClienteEmbeddingsGemini, ErrorEmbeddings
from app.utils.chunking import dividir_en_fragmentos
from app.utils.dedup import huella_contenido
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


def _fragmentar(
    descarga: ResultadoDescarga, peticion: PeticionIngesta
) -> tuple[list[FragmentoIngestado], dict[str, int]]:
    """Divide cada documento descargado en fragmentos (helper compartido)."""
    fragmentos: list[FragmentoIngestado] = []
    fragmentos_por_url: dict[str, int] = {}
    for url, texto in descarga.textos:
        cuantos = 0
        for trozo in dividir_en_fragmentos(
            texto,
            chunk_size=peticion.chunk_size,
            chunk_overlap=peticion.chunk_overlap,
        ):
            fragmentos.append(FragmentoIngestado(trozo, url))
            cuantos += 1
        fragmentos_por_url[url] = cuantos
    return fragmentos, fragmentos_por_url


def _aplicar_gate_minimo(descarga: ResultadoDescarga, minimo: int) -> None:
    """Descarta in situ los documentos con texto limpio insuficiente.

    El gate es opt-in (``min_caracteres <= 0`` lo desactiva) para no alterar el
    comportamiento historico. Los documentos descartados se reportan como
    errores individuales, igual que cualquier otro fallo de URL.
    """
    if minimo <= 0:
        return
    textos: list[tuple[str, str]] = []
    documentos: list[DocumentoDescargado] = []
    for documento in descarga.documentos:
        if len(documento.texto.strip()) < minimo:
            descarga.errores.append(
                ErrorPorUrl(
                    url=documento.url,
                    motivo=(
                        "contenido insuficiente: "
                        f"{len(documento.texto.strip())} < {minimo} caracteres"
                    ),
                )
            )
            continue
        documentos.append(documento)
        textos.append((documento.url, documento.texto))
    descarga.textos = textos
    descarga.documentos = documentos


async def _descargar(
    urls: list[str], concurrency_workers: int
) -> ResultadoDescarga:
    """Descarga y limpia las URLs con la configuracion global (helper)."""
    cfg = get_settings()
    return await descargar_y_limpiar(
        urls,
        concurrency=concurrency_workers,
        timeout_s=cfg.http_timeout_s,
        user_agent=cfg.http_user_agent,
        max_bytes=cfg.http_max_bytes,
    )


async def previsualizar_ingesta(
    peticion: PeticionIngesta, *, max_muestras: int = 5
) -> RespuestaPrevisualizacion:
    """Ejecuta scraping + chunking + dedup SIN Gemini ni insercion.

    Permite revisar la calidad de la extraccion (y el gasto potencial de
    embeddings) antes de autorizar una ingesta real. No consume cuota de Gemini
    ni escribe en la base de datos.
    """
    peticion.validar()
    met = MetricasIngesta()
    arranque = time.perf_counter()

    with medir() as crono:
        descarga = await _descargar(peticion.urls, peticion.concurrency_workers)
        _aplicar_gate_minimo(descarga, peticion.min_caracteres)
    met.tiempo_scraping_s = crono.transcurrido

    with medir() as crono:
        fragmentos, fragmentos_por_url = _fragmentar(descarga, peticion)
    met.tiempo_chunking_s = crono.transcurrido

    met.tiempo_total_s = round(time.perf_counter() - arranque, 6)
    return RespuestaPrevisualizacion(
        coleccion_id=peticion.coleccion_id,
        urls_recibidas=len(peticion.urls),
        urls_procesadas=len(descarga.textos),
        urls_con_error=len(descarga.errores),
        errores=descarga.errores,
        documentos=[
            _a_documento_ingestado(doc, fragmentos_por_url)
            for doc in descarga.documentos
        ],
        fragmentos_generados=len(fragmentos),
        duplicados=list(descarga.duplicados),
        muestra_fragmentos=[f.contenido[:280] for f in fragmentos[:max_muestras]],
        tiempo_scraping_s=met.tiempo_scraping_s,
        tiempo_chunking_s=met.tiempo_chunking_s,
        tiempo_total_s=met.tiempo_total_s,
    )


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
        descarga = await _descargar(peticion.urls, peticion.concurrency_workers)
        _aplicar_gate_minimo(descarga, peticion.min_caracteres)
    metricas.tiempo_scraping_s = crono.transcurrido

    # ---------- 2. Chunking ----------
    with medir() as crono:
        fragmentos, fragmentos_por_url = _fragmentar(descarga, peticion)
    metricas.tiempo_chunking_s = crono.transcurrido

    # ---------- 2b. Pre-filtro frente a la BD (sin Gemini) ----------
    # Se consultan las huellas ya almacenadas en la coleccion para NO volver a
    # pedir embeddings de contenido que ya existe. Este pre-filtro es solo una
    # optimizacion de cuota: la garantia de no duplicar se aplica despues, ya
    # bajo el cerrojo de la insercion (paso 4).
    async with abrir_sesion() as sesion:
        ya_existentes = await huellas_existentes(
            sesion, coleccion_id=peticion.coleccion_id
        )
    fragmentos_nuevos = [
        fragmento
        for fragmento in fragmentos
        if huella_contenido(fragmento.contenido) not in ya_existentes
    ]
    omitidos_por_prefiltro = len(fragmentos) - len(fragmentos_nuevos)

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
        vectores = await cliente_emb.generar(
            [f.contenido for f in fragmentos_nuevos]
        )
        _verificar_dimension(vectores)
    metricas.tiempo_embeddings_s = crono.transcurrido

    # ---------- 4. Insercion idempotente ----------
    registros = [
        {
            "contenido": frag.contenido,
            "origen_url": frag.origen_url,
            "embedding": vector,
        }
        for frag, vector in zip(fragmentos_nuevos, vectores)
    ]
    with medir() as crono:
        async with abrir_sesion() as sesion:
            resultado_insercion = await insertar_fragmentos_sin_duplicados(
                sesion, coleccion_id=peticion.coleccion_id, registros=registros
            )
            await sesion.commit()
    metricas.tiempo_insercion_s = crono.transcurrido

    metricas.tiempo_total_s = round(time.perf_counter() - arranque_total, 6)
    num_error = len(descarga.errores)

    documentos = [_a_documento_ingestado(doc, fragmentos_por_url) for doc in descarga.documentos]

    respuesta = RespuestaIngesta(
        coleccion_id=peticion.coleccion_id,
        urls_recibidas=len(peticion.urls),
        urls_procesadas=len(descarga.textos),
        urls_con_error=num_error,
        errores=descarga.errores,
        documentos=documentos,
        fragmentos_generados=len(fragmentos),
        embeddings_generados=len(vectores),
        registros_insertados=resultado_insercion.insertados,
        registros_omitidos=(
            omitidos_por_prefiltro + resultado_insercion.omitidos
        ),
        duplicados=list(descarga.duplicados),
        tiempo_scraping_s=metricas.tiempo_scraping_s,
        tiempo_chunking_s=metricas.tiempo_chunking_s,
        tiempo_embeddings_s=metricas.tiempo_embeddings_s,
        tiempo_insercion_s=metricas.tiempo_insercion_s,
        tiempo_total_s=metricas.tiempo_total_s,
    )
    return ResultadoIngesta(
        respuesta=respuesta, metricas=redondear(metricas)
    )


def _a_documento_ingestado(
    doc: DocumentoDescargado, fragmentos_por_url: dict[str, int]
) -> DocumentoIngestado:
    """Convierte la metadata de descarga en el modelo de la respuesta."""
    return DocumentoIngestado(
        url=doc.url,
        titulo=doc.titulo,
        mime=doc.mime,
        paginas=doc.paginas,
        fragmentos=fragmentos_por_url.get(doc.url, 0),
    )


def _verificar_dimension(vectores: list[Embedding]) -> None:
    """Defensa en profundidad: 768 dims por cuestion de compatibilidad."""
    if any(len(v) != VECTOR_DIMENSION for v in vectores):
        raise ErrorEmbeddings(
            f"Se generaron embeddings de dimension != {VECTOR_DIMENSION}; no "
            "se puede insertar en VECTOR(768)."
        )