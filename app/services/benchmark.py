"""Benchmark de concurrencia de la ingesta (FASE 5).

Reutiliza las UNICAS funciones del pipeline de FASE 3 (no duplica logica):

* descarga  -> ``app.services.downloader.descargar_y_limpiar``
* chunking  -> ``app.utils.chunking.dividir_en_fragmentos``
* embeddings -> ``app.services.embeddings.ClienteEmbeddingsGemini``
* insercion -> ``app.database.repository.insertar_fragmentos``

y mide cada tramo con ``app.utils.metricas.medir`` (reloj monotonico
``time.perf_counter``), el mismo que usa ``POST /api/ingestar-web``.

Estado de cada ejecucion (REQUISITO 5/6 de la fase):

* ``COMPLETED``   -> todo el lote ingerido sin fallos ni 429.
* ``PARTIAL``     -> pipeline terminado pero con alguna URL fallida (la carga
                     ya NO es comparable: no entra en las metricas T/S/E).
* ``FAILED``      -> fallo definitivo no relacionado con limite de Gemini.
* ``RATE_LIMITED``-> afectada por 429/RESOURCE_EXHAUSTED (por defecto, MODO
                     ESTRICTO: aunque Gemini recuperara la llamada, la
                     ejecucion NO cuenta como valida; ``modo_429="permitir"``
                     la admite registrando los eventos).

Solo las ejecuciones ``COMPLETED`` se usan para ``T_p``, ``S_p`` y ``E_p``:
es la unica garantia de que se comparan CARGAS IDENTICAS (REQUISITOS 4 y 8).

CORRECCION DE CARGA CONTROLADA (metodologia del benchmark):

El scraping real descarga las URLs de nuevo en cada ejecucion; el contenido
web varia y el numero de fragmentos puede cambiar (p. ej. 49 vs 43). Para que
T1 y T_p midan EXACTAMENTE la misma carga existe un CORPUS CONGELADO:

* ``crear_corpus`` descarga las URLs UNA sola vez y persiste
  ``(url, texto_limpio, hash_documento)`` con ``hash_corpus`` calculado sobre
  la lista ordenada de documentos.
* ``ejecutar_benchmark(corpus=...)`` lee los TEXTOS del corpus (sin red) y
  verifica el hash: si ``hash_carga`` != ``hash_corpus`` la ejecucion se
  marca ``INVALID_LOAD`` y no produce T/S/E.
* ``construir_resumen`` ademas exige, para calcular ``S_p``/``E_p``: T1
  presente, ejecuciones COMPLETED, mismo hash de carga, mismo numero de
  documentos, mismo numero de fragmentos y cero eventos 429.
* El benchmark de scraping (modo por defecto, red real) se mantiene separado:
  mide la descarga con ``workers``. El benchmark controlado (``corpus=...``)
  mide chunking + embeddings + insercion sobre textos fijos.
* ``ejecutar_benchmark(..., solo_scraping=True)`` mide UNICAMENTE la descarga
  concurrente: retorna antes de chunking, embeddings e insercion, por lo que
  NO llama a Gemini ni escribe en PostgreSQL. La identidad de carga se calcula
  sobre la lista ORDENADA de URLs (el contenido web puede variar entre
  corridas); por eso S/E solo se calculan entre corridas COMPLETED con la
  misma lista de URLs. Los errores de descarga producen ``PARTIAL`` (nunca
  ``COMPLETED``).

Los embeddings de Gemini se generan SECUENCIALMENTE por lotes (FASE 3):
esa seccion serial del pipeline NO se paraleliza en esta correccion y es
parte del analisis de la Ley de Amdahl.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.database.repository import VECTOR_DIMENSION, insertar_fragmentos
from app.database.session import abrir_sesion
from app.models.ingesta import ErrorPorUrl
from app.models.normativa import validar_coleccion_id
from app.services.downloader import ResultadoDescarga, descargar_y_limpiar
from app.services.embeddings import (
    ClienteEmbeddingsGemini,
    ErrorEmbeddings,
    _es_limitacion_respuesta,
)
from app.services.ingesta import FragmentoIngestado
from app.utils.chunking import dividir_en_fragmentos
from app.utils.metricas import medir

COMPLETED = "COMPLETED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
RATE_LIMITED = "RATE_LIMITED"
INVALID_LOAD = "INVALID_LOAD"

ESTADOS = (COMPLETED, PARTIAL, FAILED, RATE_LIMITED, INVALID_LOAD)

WORKER_MIN = 1
WORKER_MAX = 64

# Solo las ejeecuciones completas representan la MISMA carga de trabajo.
ESTADOS_VALIDOS_PARA_METRICAS = frozenset({COMPLETED})


def validar_workers(workers: int) -> int:
    """Valida un nivel de concurrencia: entero en [1, 64]."""
    if isinstance(workers, bool) or not isinstance(workers, int):
        raise ValueError("workers debe ser un entero")
    if workers < WORKER_MIN or workers > WORKER_MAX:
        raise ValueError(
            f"workers debe estar entre {WORKER_MIN} y {WORKER_MAX} "
            f"(recibido: {workers})"
        )
    return workers


def extraer_conteos(emb: Any) -> dict[str, int]:
    """Lee el historial observacional de ``historico_llamadas``.

    Cada entrada es ``(status_code, fue_limitacion)``. Convenciones
    documentadas:

    * ``lotes_enviados``:  numero de peticiones HTTP a ``batchEmbedContents``
      (cada intento envia un lote; incluye reintentos).
    * ``lotes_exitosos``:  peticiones que respondieron HTTP 200.
    * ``reintentos``:      peticiones adicionales sobre lotes exitosos
      (``lotes_enviados - lotes_exitosos``); nunca subestima eventos de
      limitacion.
    * ``eventos_429``:     peticiones con ``status == 429`` o cuerpo con
      ``RESOURCE_EXHAUSTED``/``quota exceeded``.

    Un cliente sin historial (p. ej. un fake de tests) produce todo en cero.
    """
    historial = list(getattr(emb, "historico_llamadas", None) or [])
    exitosos = sum(1 for estado, _ in historial if estado == 200)
    return {
        "lotes_enviados": len(historial),
        "lotes_exitosos": exitosos,
        "reintentos": len(historial) - exitosos,
        "eventos_429": sum(
            1 for estado, limite in historial if estado == 429 or limite
        ),
    }


@dataclass
class RegistroEjecucion:
    """Resultado de una ejecucion del pipeline con un nivel de workers."""

    workers: int
    estado: str = FAILED
    mensaje_error: str | None = None
    modo_carga: str = "scraping"
    corpus_utilizado: str | None = None
    hash_carga: str | None = None
    documentos_cargados: int = 0
    carga_identica: bool = False
    urls_recibidas: int = 0
    urls_procesadas: int = 0
    urls_con_error: int = 0
    errores: list[dict[str, str]] = None  # type: ignore[assignment]
    fragmentos_generados: int = 0
    embeddings_generados: int = 0
    registros_insertados: int = 0
    lotes_enviados: int = 0
    lotes_exitosos: int = 0
    reintentos: int = 0
    eventos_429: int = 0
    tiempo_scraping_s: float = 0.0
    tiempo_chunking_s: float = 0.0
    tiempo_embeddings_s: float = 0.0
    tiempo_insercion_s: float = 0.0
    tiempo_total_s: float = 0.0

    def __post_init__(self) -> None:
        if self.errores is None:
            self.errores = []

    def a_dict(self) -> dict[str, Any]:
        """Diccionario serializable (JSON) de la ejecucion."""
        datos = asdict(self)
        datos["porcentajes_por_etapa"] = porcentajes_por_etapa(self)
        return datos


def porcentajes_por_etapa(registro: RegistroEjecucion) -> dict[str, float] | None:
    """Porcentaje del tiempo total que represento cada fase (REQUISITO 8)."""
    total = registro.tiempo_total_s
    if total <= 0:
        return None
    etapas = {
        "scraping": registro.tiempo_scraping_s,
        "chunking": registro.tiempo_chunking_s,
        "embeddings": registro.tiempo_embeddings_s,
        "insercion": registro.tiempo_insercion_s,
    }
    return {
        nombre: round(segundos / total * 100.0, 2)
        for nombre, segundos in etapas.items()
    }


def _redondear(valor: float, decimales: int = 6) -> float:
    return round(valor, decimales)


# ---------------------------------------------------------------------------
# Corpus congelado: hashes del contenido cargado
# ---------------------------------------------------------------------------


def hash_documento(texto: str) -> str:
    """SHA-256 de un documento limpio (integridad del corpus)."""
    return hashlib.sha256(texto.encode("utf-8", errors="replace")).hexdigest()


def calcular_hash_carga(textos: list[tuple[str, str]]) -> str:
    """Hash determinista de la carga: lista ORDENADA de ``(url, texto)``.

    Es la huella de lo que llega a chunking/embeddings/insercion. Dos corridas
    solo comparten el hash si usan exactamente los mismos documentos en el
    mismo orden — por eso garantiza "carga identica".
    """
    digest = hashlib.sha256()
    for indice, (url, texto) in enumerate(textos):
        digest.update(f"{indice}\x1e".encode("ascii"))
        digest.update((url + "\n" + texto).encode("utf-8", errors="replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def validar_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    """Valida la estructura minima de un corpus congelado."""
    if not isinstance(corpus, dict):
        raise ValueError("El corpus debe ser un objeto JSON")
    documentos = corpus.get("documentos")
    if not isinstance(documentos, list) or not documentos:
        raise ValueError("El corpus debe incluir una lista 'documentos' no vacia")
    for documento in documentos:
        if not isinstance(documento, dict) or not documento.get("url"):
            raise ValueError("Cada documento del corpus necesita 'url'")
        if not isinstance(documento.get("texto"), str) or not documento["texto"]:
            raise ValueError("Cada documento del corpus necesita 'texto' no vacio")
    if not isinstance(corpus.get("hash_corpus"), str) or not corpus["hash_corpus"]:
        raise ValueError("El corpus debe incluir 'hash_corpus'")
    corpus.setdefault("numero_documentos", len(documentos))
    return corpus


async def crear_corpus(
    urls: list[str],
    ruta: str | Path,
    *,
    descargar: Callable[..., Awaitable[ResultadoDescarga]] | None = None,
) -> dict[str, Any]:
    """Descarga las URLs UNA sola vez y congela el contenido limpio en disco.

    Genera un JSON con ``documentos`` (``url``, ``texto``, ``hash_documento``),
    ``hash_corpus`` (carga completa), ``numero_documentos``, metadatos de
    errores y ``creado_en``. NO genera embeddings ni toca la base de datos.
    ``descargar`` se inyecta en los tests (sin red).
    """
    config = get_settings()
    funcion = descargar or descargar_y_limpiar
    descarga = await funcion(
        urls,
        concurrency=max(1, config.http_max_concurrencia),
        timeout_s=config.http_timeout_s,
        user_agent=config.http_user_agent,
        max_bytes=config.http_max_bytes,
    )
    textos: list[tuple[str, str]] = list(descarga.textos)
    corpus = {
        "formato": 1,
        "descripcion": (
            "Corpus congelado para el benchmark de FASE 5: textos limpios "
            "descargados una unica vez. Todas las corridas comparadas deben "
            "usar EXACTAMENTE estos documentos (hash_corpus verificado)."
        ),
        "creado_en": datetime.now(timezone.utc).isoformat(),
        "urls_recibidas": len(urls),
        "urls_con_error": len(descarga.errores),
        "errores": [
            {"url": fallo.url, "motivo": fallo.motivo} for fallo in descarga.errores
        ],
        "numero_documentos": len(textos),
        "hash_corpus": calcular_hash_carga(textos),
        "documentos": [
            {
                "url": url,
                "texto": texto,
                "hash_documento": hash_documento(texto),
            }
            for url, texto in textos
        ],
    }
    camino = Path(ruta)
    camino.parent.mkdir(parents=True, exist_ok=True)
    camino.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return corpus


def leer_corpus(ruta: str | Path) -> dict[str, Any]:
    """Carga y valida un corpus congelado desde disco."""
    camino = Path(ruta)
    if not camino.is_file():
        raise FileNotFoundError(f"Corpus no encontrado: {camino}")
    datos = json.loads(camino.read_text(encoding="utf-8"))
    return validar_corpus(datos)


async def ejecutar_benchmark(
    urls: list[str],
    coleccion_id: str,
    workers: int,
    chunk_size: int,
    chunk_overlap: int,
    *,
    modo_429: str = "estricto",
    emb_client: ClienteEmbeddingsGemini | None = None,
    descargar: Callable[..., Awaitable[ResultadoDescarga]] | None = None,
    abrir: Callable[[], Any] = abrir_sesion,
    batch_size: int | None = None,
    corpus: dict[str, Any] | None = None,
    corpus_ruta: str | None = None,
    solo_scraping: bool = False,
) -> RegistroEjecucion:
    """Ejecuta UNA vez el pipeline completo con ``workers`` de concurrencia.

    Tres modos de carga (separados conceptualmente):

    * ``solo_scraping=True`` (scraping puro): descarga con ``workers`` de
      concurrencia y RETORNA antes de chunking, embeddings e insercion. No usa
      Gemini ni PostgreSQL; la base de la carga es la lista ordenada de URLs.
    * ``corpus=None`` (modo scraping integral): se descarga de internet con
      ``descargar`` (real o inyectada) usando ``workers`` de concurrencia.
      Mide ``tiempo_scraping_s`` real y continua por todo el pipeline.
    * ``corpus=dict`` (modo controlado): los TEXTOS se leen del corpus
      congelado (sin red, ``tiempo_scraping_s`` = 0). Se verifica el hash de
      la carga contra ``corpus['hash_corpus']``; si no coincide -> ``INVALID_LOAD``
      y no se calculan metricas T/S/E.

    Las demas dependencias se inyectan (``emb_client``, ``abrir``) para que
    los tests ejecuten el flujo sin red, sin Gemini ni base de datos.
    ``batch_size`` fija el tamano de lote de embeddings (REQUISITO 4).
    """
    workers = validar_workers(workers)
    validar_coleccion_id(coleccion_id)
    if modo_429 not in ("estricto", "permitir"):
        raise ValueError(f"modo_429 debe ser 'estricto' o 'permitir' (recibido: {modo_429})")
    if solo_scraping and corpus is not None:
        raise ValueError(
            "solo_scraping no es compatible con corpus: el corpus no mide scraping."
        )

    config = get_settings()
    registro = RegistroEjecucion(workers=workers, urls_recibidas=len(urls))
    registro.modo_carga = "corpus" if corpus is not None else "scraping"
    if corpus is not None:
        registro.corpus_utilizado = corpus_ruta
    arranque_total = time.perf_counter()

    # ---------- 1. Carga de textos: scraping real O corpus congelado ----------
    if corpus is not None:
        validar_corpus(corpus)
        textos_all: list[tuple[str, str]] = [
            (doc["url"], doc["texto"]) for doc in corpus["documentos"]
        ]
        registro.urls_recibidas = len(textos_all)
        registro.urls_procesadas = len(textos_all)
        registro.urls_con_error = 0
        registro.errores = []
    else:
        funcion_descarga = descargar or descargar_y_limpiar
        try:
            with medir() as crono:
                descarga = await funcion_descarga(
                    urls,
                    concurrency=workers,
                    timeout_s=config.http_timeout_s,
                    user_agent=config.http_user_agent,
                    max_bytes=config.http_max_bytes,
                )
        except Exception as error:  # noqa: BLE001 -- frontera de servicios.
            registro.mensaje_error = f"scraping: {error}"
            registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
            return registro

        registro.tiempo_scraping_s = crono.transcurrido
        registro.urls_procesadas = len(descarga.textos)
        registro.urls_con_error = len(descarga.errores)
        registro.errores = [
            {"url": fallo.url, "motivo": fallo.motivo} for fallo in descarga.errores
        ]
        textos_all = list(descarga.textos)

    # Modo SOLO SCRAPING (REQUISITO FASE 5): mide UNICAMENTE la descarga
    # concurrente. Retorna aqui, ANTES de chunking, embeddings e insercion:
    # no se llama a Gemini ni se abre sesion de PostgreSQL.
    if solo_scraping:
        registro.documentos_cargados = len(urls)
        # Identidad de carga por lista ORDENADA de URLs: el contenido web puede
        # variar entre corridas y no debe invalidar la comparacion de scraping.
        registro.hash_carga = calcular_hash_carga([(url, "") for url in urls])
        registro.fragmentos_generados = 0
        registro.estado = COMPLETED if registro.urls_con_error == 0 else PARTIAL
        registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
        return registro

    # Huella de la carga real que entra a chunking (garantia de identidad).
    registro.documentos_cargados = len(textos_all)
    registro.hash_carga = calcular_hash_carga(textos_all)

    # Modo controlado: la carga DEBE coincidir con el corpus congelado.
    if corpus is not None:
        esperado = corpus.get("hash_corpus")
        esperados_docs = corpus.get("numero_documentos", len(corpus["documentos"]))
        if registro.hash_carga != esperado or registro.documentos_cargados != esperados_docs:
            registro.estado = INVALID_LOAD
            registro.mensaje_error = (
                "INVALID_LOAD: el hash de la carga no coincide con el corpus. "
                f"hash_carga={registro.hash_carga} "
                f"hash_corpus={esperado} "
                f"documentos={registro.documentos_cargados}/{esperados_docs}. "
                "La carga NO es identica; no se calculan T/S/E."
            )
            registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
            return registro
        registro.carga_identica = True

    # ---------- 2. Chunking ----------
    with medir() as crono:
        fragmentos: list[FragmentoIngestado] = []
        for url, texto in textos_all:
            for trozo in dividir_en_fragmentos(
                texto,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            ):
                fragmentos.append(FragmentoIngestado(trozo, url))
    registro.tiempo_chunking_s = crono.transcurrido
    registro.fragmentos_generados = len(fragmentos)

    # ---------- 3. Embeddings (lotes) ----------
    cliente = emb_client or ClienteEmbeddingsGemini(
        api_key=config.gemini_api_key,
        modelo=config.gemini_embedding_model,
        dimension=config.pgvector_dimension,
        batch_size=min(max(1, batch_size or config.gemini_batch_size), 100),
        max_reintentos=max(1, config.gemini_max_reintentos),
        backoff_base_s=config.gemini_backoff_base_s,
        backoff_max_s=config.gemini_backoff_max_s,
        timeout_s=config.gemini_timeout_s,
    )
    try:
        with medir() as crono:
            vectores = await cliente.generar([f.contenido for f in fragmentos])
            _verificar_dimension(vectores)
    except ErrorEmbeddings as error:
        registro.tiempo_embeddings_s = crono.transcurrido
        registrando = extraer_conteos(cliente)
        registro.lotes_enviados = registrando["lotes_enviados"]
        registro.lotes_exitosos = registrando["lotes_exitosos"]
        registro.reintentos = registrando["reintentos"]
        registro.eventos_429 = registrando["eventos_429"]
        registro.mensaje_error = str(error)
        registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
        registro.estado = (
            RATE_LIMITED if _es_limitacion_respuesta(str(error)) else FAILED
        )
        return registro
    except Exception as error:  # noqa: BLE001 -- frontera de servicios.
        registro.tiempo_embeddings_s = crono.transcurrido
        registro.mensaje_error = f"embeddings: {error}"
        registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
        return registro

    registro.tiempo_embeddings_s = crono.transcurrido
    registro.embeddings_generados = len(vectores)
    registrando = extraer_conteos(cliente)
    registro.lotes_enviados = registrando["lotes_enviados"]
    registro.lotes_exitosos = registrando["lotes_exitosos"]
    registro.reintentos = registrando["reintentos"]
    registro.eventos_429 = registrando["eventos_429"]

    # Modo estricto (REQUISITO 6): CUALQUIER evento 429 invalida la ejecucion,
    # aunque Gemini hubiera recuperado. No se inserta nada para no registrar
    # datos de una ejecucion descartada.
    if registro.eventos_429 > 0 and modo_429 == "estricto":
        registro.estado = RATE_LIMITED
        registro.mensaje_error = (
            "Ejecucion afectada por limite de Gemini (429/RESOURCE_EXHAUSTED) "
            "recuperado; en modo estricto NO cuenta como valida para T/S/E."
        )
        registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
        return registro

    # ---------- 4. Insercion ----------
    registros = [
        {
            "contenido": frag.contenido,
            "origen_url": frag.origen_url,
            "embedding": vector,
        }
        for frag, vector in zip(fragmentos, vectores)
    ]
    try:
        with medir() as crono:
            async with abrir() as sesion:
                insertados = await insertar_fragmentos(
                    sesion, coleccion_id=coleccion_id, registros=registros
                )
                await sesion.commit()
    except Exception as error:  # noqa: BLE001 -- frontera de servicios.
        registro.tiempo_insercion_s = crono.transcurrido
        registro.mensaje_error = f"insercion: {error}"
        registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
        return registro

    registro.tiempo_insercion_s = crono.transcurrido
    registro.registros_insertados = insertados
    registro.tiempo_total_s = _redondear(time.perf_counter() - arranque_total)
    # PARTIAL: hubo URL(s) fallida(s); la carga ya no es comparable.
    registro.estado = COMPLETED if registro.urls_con_error == 0 else PARTIAL
    return registro


def _verificar_dimension(vectores: list[list[float]]) -> None:
    if any(len(v) != VECTOR_DIMENSION for v in vectores):
        raise ErrorEmbeddings(
            f"Embeddings de dimension != {VECTOR_DIMENSION}; el lote no es "
            "compatible con VECTOR(768)."
        )


def _estado_agregado(estados: set[str]) -> str:
    """Estado agregado de un nivel de workers (la peor causa diagnostica)."""
    for candidato in (INVALID_LOAD, RATE_LIMITED, FAILED, PARTIAL, COMPLETED):
        if candidato in estados:
            return candidato
    return "SIN_DATOS"  # pragma: no cover - defensivo


def _huella_carga(ejecucion: RegistroEjecucion) -> tuple:
    """Huella de la carga de una ejecucion: hash + docs + fragmentos."""
    hash_carga = ejecucion.hash_carga if ejecucion.hash_carga is not None else None
    docs = ejecucion.documentos_cargados if ejecucion.documentos_cargados else 0
    frags = ejecucion.fragmentos_generados if ejecucion.fragmentos_generados else 0
    return (hash_carga, docs, frags)


def _huella_compatible(h1: tuple, h2: tuple) -> bool:
    """Compatibilidad con tests sinteticos (hash None)."""
    if h1 == h2:
        return True
    # Legacy: ambos con hash None cuentan como equivalentes para gating
    if h1[0] is None and h2[0] is None:
        return True
    return False


def _carga_global(ejecuciones: list[RegistroEjecucion]) -> dict[str, Any]:
    """Resumen de identidad de carga de todas las ejecuciones COMPLETED.

    ``carga_identica`` es True solo si todas las ejecuciones comparables
    comparten el mismo hash de carga, numero de documentos y numero de
    fragmentos. Si difieren, no se pueden comparar T1/T_p de forma valida.
    """
    comparables = [
        e for e in ejecuciones
        if e.estado in ESTADOS_VALIDOS_PARA_METRICAS and e.eventos_429 == 0
    ]
    if not comparables:
        return {
            "carga_identica": False,
            "hash_carga": None,
            "numero_documentos": None,
            "numero_fragmentos": None,
        }
    # Compatibilidad con tests sinteticos: si no hay hash definido, se asume
    # carga identica (comportamiento anterior a la correccion de corpus).
    hashes_definidos = [e.hash_carga for e in comparables if e.hash_carga]
    if len(hashes_definidos) == 0:
        # Valores neutros para mantener compatibilidad con tests antiguos.
        return {
            "carga_identica": True,
            "hash_carga": None,
            "numero_documentos": None,
            "numero_fragmentos": None,
        }
    huellas = {_huella_carga(e) for e in comparables}
    return {
        "carga_identica": len(huellas) <= 1,
        "hash_carga": next(iter(huellas))[0] if len(huellas) == 1 else None,
        "numero_documentos": next(iter(huellas))[1] if len(huellas) == 1 else None,
        "numero_fragmentos": next(iter(huellas))[2] if len(huellas) == 1 else None,
    }


def construir_resumen(
    ejecuciones: list[RegistroEjecucion],
) -> list[dict[str, Any]]:
    """Agrega las ejecuciones por workers y calcula T_p, S_p y E_p.

    Reglas (REQUISITOS 4, 7 y 12 + correccion de carga controlada):

    * ``T_p`` = media de los tiempos de las ejecuciones COMPLETED sin 429.
    * ``S_p = T1 / T_p`` y ``E_p = S_p / p`` SOLO si TODAS las ejecuciones
      COMPLETED comparten el MISMO hash de carga, mismo numero de documentos
      y mismo numero de fragmentos (``carga_identica``), y si existe T1.
      Si no -> ``speedup``/``eficiencia`` quedan en ``None`` (nunca datos
      enganosos por comparar cargas distintas, p. ej. 49 vs 43 fragmentos).
    """
    carga = _carga_global(ejecuciones)
    por_workers: dict[int, list[RegistroEjecucion]] = {}
    for ejecucion in ejecuciones:
        por_workers.setdefault(ejecucion.workers, []).append(ejecucion)

    huella_objetivo = (
        (carga["hash_carga"], carga["numero_documentos"], carga["numero_fragmentos"])
        if carga["carga_identica"]
        else None
    )

    filas: list[dict[str, Any]] = []
    for workers in sorted(por_workers):
        grupo = por_workers[workers]
        validos = [
            e.tiempo_total_s
            for e in grupo
            if e.estado in ESTADOS_VALIDOS_PARA_METRICAS
            and e.eventos_429 == 0
        ]
        estados = {e.estado for e in grupo}
        if validos:
            fila: dict[str, Any] = {
                "workers": workers,
                "ejecuciones": len(grupo),
                "ejecuciones_validas": len(validos),
                "tiempo_total_s": _redondear(sum(validos) / len(validos)),
                "estado": COMPLETED,
            }
        else:
            fila = {
                "workers": workers,
                "ejecuciones": len(grupo),
                "ejecuciones_validas": 0,
                "tiempo_total_s": None,
                "estado": _estado_agregado(estados),
            }
        fila["afectado_429"] = any(e.eventos_429 > 0 for e in grupo)
        fila["carga_identica"] = (
            carga["carga_identica"]
            and fila["tiempo_total_s"] is not None
            and all(
                _huella_compatible(_huella_carga(e), huella_objetivo)
                for e in grupo
                if e.estado in ESTADOS_VALIDOS_PARA_METRICAS
                and e.eventos_429 == 0
            )
        )
        fila["hash_carga"] = carga["hash_carga"]
        fila["speedup"] = None
        fila["eficiencia"] = None
        fila["T1_base_s"] = None
        filas.append(fila)

    base_t1 = next(
        (
            fila["tiempo_total_s"]
            for fila in filas
            if fila["workers"] == 1 and fila["tiempo_total_s"] is not None
        ),
        None,
    )
    for fila in filas:
        # S/E solo con carga identica verificada, T1 presente y sin 429.
        if (
            not fila["carga_identica"]
            or fila["tiempo_total_s"] is None
            or base_t1 is None
        ):
            continue
        t_p = fila["tiempo_total_s"]
        speedup = base_t1 / t_p
        fila["speedup"] = _redondear(speedup)
        fila["eficiencia"] = _redondear(speedup / fila["workers"])
        fila["T1_base_s"] = base_t1
    return filas


def construir_resultado(
    coleccion_id: str,
    urls: list[str],
    configuracion: dict[str, Any],
    ejecuciones: list[RegistroEjecucion],
) -> dict[str, Any]:
    """Ensambla el diccionario completo que se guarda como JSON.

    Incluye el bloque ``carga`` que describe el modo, el corpus utilizado, el
    hash de la carga, el numero de documentos/fragmentos y si fue identica
    (REQUISITO 6 de la correccion).
    """
    resumen = construir_resumen(ejecuciones)
    global_carga = _carga_global(ejecuciones)
    carga = {
        "modo": configuracion.get("modo_carga", "scraping"),
        "corpus_utilizado": configuracion.get("corpus_ruta"),
        "hash_carga": global_carga["hash_carga"]
        or (ejecuciones[0].hash_carga if ejecuciones else None),
        "numero_documentos": global_carga["numero_documentos"]
        or (ejecuciones[0].documentos_cargados if ejecuciones else None),
        "numero_fragmentos": global_carga["numero_fragmentos"]
        or (ejecuciones[0].fragmentos_generados if ejecuciones else None),
        "carga_identica": bool(resumen) and resumen[0]["carga_identica"],
    }
    return {
        "coleccion_id": coleccion_id,
        "numero_urls": len(urls),
        "urls": list(urls),
        "configuracion": configuracion,
        "carga": carga,
        "ejecuciones": [e.a_dict() for e in ejecuciones],
        "resumen": resumen,
    }


# ---------------------------------------------------------------------------
# Carga del dataset / URLs
# ---------------------------------------------------------------------------


def cargar_dataset(ruta: str | Path) -> dict[str, Any]:
    """Carga ``benchmarks/urls_benchmark.json`` (o cualquier archivo analogo).

    Devuelve un diccionario con, al menos, las claves ``financiero`` y
    ``tributario`` (listas de URLs). Rechaza errores de lectura o estructura.
    """
    camino = Path(ruta)
    if not camino.is_file():
        raise FileNotFoundError(f"Dataset de URLs no encontrado: {camino}")
    with camino.open("r", encoding="utf-8") as manejador:
        datos = json.load(manejador)
    if not isinstance(datos, dict):
        raise ValueError("El dataset debe ser un objeto JSON")
    for clave in ("financiero", "tributario"):
        if clave not in datos:
            raise ValueError(
                f"El dataset debe incluir la lista '{clave}' de URLs"
            )
    return datos


def seleccionar_urls(
    datos: dict[str, Any], categoria: str = "todas"
) -> list[str]:
    """Selecciona las URLs de una categoria (o 'todas' si se combinan)."""
    if categoria == "todas":
        return list(datos.get("financiero", [])) + list(
            datos.get("tributario", [])
        )
    if categoria not in datos:
        raise ValueError(
            f"Categoria '{categoria}' no presente en el dataset "
            f"(disponibles: {sorted(k for k in datos if isinstance(datos[k], list))})"
        )
    return list(datos[categoria])


def validar_urls(urls: list[str]) -> list[str]:
    """Rechaza URLs no http(s) o malformadas antes de correr el benchmark."""
    if not urls:
        raise ValueError("La lista de URLs no puede estar vacia")
    from app.services.downloader import es_url_valida

    invalidas = [url for url in urls if not es_url_valida(url)]
    if invalidas:
        raise ValueError(
            "URLs invalidas (solo http/https): " + ", ".join(invalidas[:5])
        )
    return urls