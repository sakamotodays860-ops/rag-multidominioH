"""Pruebas del CORPUS CONGELADO (FASE 5) 100% offline.

Validan las primitivas de integridad del corpus implementadas en
``app/services/benchmark.py``:

* ``hash_documento`` / ``calcular_hash_carga``: hashes deterministas.
* ``crear_corpus``: congela ``(url, texto)`` en disco usando una descarga
  INYECTADA (sin red real).
* ``leer_corpus`` / ``validar_corpus``: ida y vuelta y validacion estructural.
* ``ejecutar_benchmark(corpus=...)``: modo controlado sin red, sin Gemini y
  sin PostgreSQL; detecta corpus alterado/hash inconsistente/diferencias de
  cantidad de documentos marcando ``INVALID_LOAD``.

Ninguna prueba descarga URLs, llama a Gemini ni toca la base de datos.
"""

import hashlib
import json
from contextlib import asynccontextmanager

import pytest

import app.services.benchmark as benchmark_svc
from app.database.repository import VECTOR_DIMENSION
from app.services.benchmark import (
    COMPLETED,
    INVALID_LOAD,
    RegistroEjecucion,
    calcular_hash_carga,
    construir_resumen,
    crear_corpus,
    ejecutar_benchmark,
    hash_documento,
    leer_corpus,
    validar_corpus,
)
from app.services.downloader import ResultadoDescarga

COLECCION = "corpus_benchmark_pruebas"

URLS = [
    "https://www.ejemplo.bo/corpus-a",
    "https://www.ejemplo.bo/corpus-b",
    "https://www.ejemplo.bo/corpus-c",
]

DOCUMENTOS = [
    (URLS[0], "Contenido congelado del documento A. " * 3),
    (URLS[1], "Contenido congelado del documento B. " * 3),
    (URLS[2], "Contenido congelado del documento C. " * 3),
]


# ---------------------------------------------------------------------------
# Dobles de prueba (sin red, sin Gemini, sin PostgreSQL)
# ---------------------------------------------------------------------------


class DescargaFalsaCorpus:
    """Sustituto de ``descargar_y_limpiar`` para ``crear_corpus``."""

    def __init__(self, textos=None, errores=None):
        self.textos = list(textos) if textos is not None else []
        self.errores = list(errores or [])
        self.kwargs = None
        self.llamadas = 0

    async def __call__(self, urls, **kwargs):
        self.kwargs = kwargs
        self.llamadas += 1
        resultado = ResultadoDescarga()
        for url, texto in self.textos:
            resultado.registrar_ok(url, texto)
        for url, motivo in self.errores:
            resultado.registrar_error(url, motivo)
        return resultado


class EmbFalso:
    """Cliente de embeddings falso: nunca toca Gemini."""

    def __init__(self, historial=None):
        self.historico_llamadas = list(historial or [])
        self.textos_recibidos = None

    async def generar(self, textos, cliente=None):
        self.textos_recibidos = list(textos)
        vector = [0.01] * VECTOR_DIMENSION
        vector[0] = 1.0
        return [list(vector) for _ in textos]


class EmbProhibido:
    """Falla si se llega a pedir embeddings (corpus invalido)."""

    async def generar(self, textos, cliente=None):
        raise AssertionError("no deben generarse embeddings con carga invalida")


class InsercionFalsa:
    """Sustituto de ``insertar_fragmentos``: captura lo recibido."""

    def __init__(self):
        self.coleccion_id = None
        self.registros = None

    async def __call__(self, sesion, *, coleccion_id, registros):
        self.coleccion_id = coleccion_id
        self.registros = list(registros)
        return len(registros)


class _SesionFalsa:
    async def commit(self):
        return None


@asynccontextmanager
async def sesion_falsa():
    yield _SesionFalsa()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def corpus_falso(documentos, *, hash_corpus=None, numero_documentos=None):
    """Construye un corpus valido en memoria (sin descarga)."""
    documentos = list(documentos)
    corpus = {
        "formato": 1,
        "hash_corpus": (
            hash_corpus if hash_corpus is not None else calcular_hash_carga(documentos)
        ),
        "documentos": [
            {"url": url, "texto": texto, "hash_documento": hash_documento(texto)}
            for url, texto in documentos
        ],
    }
    if numero_documentos is not None:
        corpus["numero_documentos"] = numero_documentos
    return corpus


async def ejecutar_modo_corpus(
    monkeypatch,
    corpus,
    *,
    corpus_ruta="corpus_falso.json",
    workers=1,
    emb=None,
    insercion=None,
):
    """Ejecuta el benchmark en modo controlado con dobles inyectados."""
    if emb is None:
        emb = EmbFalso()
    if insercion is None:
        insercion = InsercionFalsa()
    monkeypatch.setattr(benchmark_svc, "insertar_fragmentos", insercion)
    registro = await ejecutar_benchmark(
        URLS,
        coleccion_id=COLECCION,
        workers=workers,
        chunk_size=1200,
        chunk_overlap=200,
        emb_client=emb,
        abrir=sesion_falsa,
        corpus=corpus,
        corpus_ruta=corpus_ruta,
    )
    return registro, emb, insercion


def registro(
    workers,
    tiempo,
    estado=COMPLETED,
    *,
    hash_carga="hash",
    documentos=3,
    fragmentos=3,
    eventos_429=0,
):
    r = RegistroEjecucion(workers=workers, estado=estado)
    r.tiempo_total_s = tiempo
    r.hash_carga = hash_carga
    r.documentos_cargados = documentos
    r.fragmentos_generados = fragmentos
    r.eventos_429 = eventos_429
    return r


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------


def test_hash_documento_es_sha256_del_texto():
    texto = "Normativa congelada"
    assert hash_documento(texto) == hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()


def test_hash_carga_es_determinista():
    a = DOCUMENTOS
    b = list(DOCUMENTOS)
    assert calcular_hash_carga(a) == calcular_hash_carga(b)


def test_hash_carga_detecta_cambio_de_orden():
    assert calcular_hash_carga(DOCUMENTOS) != calcular_hash_carga(
        list(reversed(DOCUMENTOS))
    )


def test_hash_carga_detecta_cambio_de_contenido_y_url():
    alterado_texto = [
        (DOCUMENTOS[0][0], DOCUMENTOS[0][1] + " alterado"),
        *DOCUMENTOS[1:],
    ]
    alterado_url = [
        ("https://otro.bo/a", DOCUMENTOS[0][1]),
        *DOCUMENTOS[1:],
    ]
    assert calcular_hash_carga(DOCUMENTOS) != calcular_hash_carga(alterado_texto)
    assert calcular_hash_carga(DOCUMENTOS) != calcular_hash_carga(alterado_url)


def test_hash_carga_vacio_es_sha256_de_nada():
    assert calcular_hash_carga([]) == hashlib.sha256().hexdigest()


# ---------------------------------------------------------------------------
# validar_corpus
# ---------------------------------------------------------------------------


def test_validar_corpus_completa_numero_documentos():
    corpus = corpus_falso(DOCUMENTOS)
    validado = validar_corpus(corpus)
    assert validado["numero_documentos"] == len(DOCUMENTOS)


def test_validar_corpus_preserva_numero_documentos_declarado():
    corpus = corpus_falso(DOCUMENTOS, numero_documentos=99)
    assert validar_corpus(corpus)["numero_documentos"] == 99


@pytest.mark.parametrize(
    "invalido",
    [
        ["no", "es", "un", "dict"],
        {},
        {"documentos": []},
        {"documentos": [{"texto": "sin url"}], "hash_corpus": "abc"},
        {"documentos": [{"url": "https://x.bo", "texto": ""}], "hash_corpus": "abc"},
        {"documentos": [{"url": "https://x.bo", "texto": "ok"}]},
        {"documentos": [{"url": "https://x.bo", "texto": "ok"}], "hash_corpus": ""},
    ],
)
def test_validar_corpus_rechaza_estructuras_invalidas(invalido):
    with pytest.raises(ValueError):
        validar_corpus(invalido)


# ---------------------------------------------------------------------------
# crear_corpus (descarga inyectada, sin red)
# ---------------------------------------------------------------------------


async def test_crear_corpus_congela_documentos_y_escribe_json(tmp_path):
    ruta = tmp_path / "sub" / "corpus.json"
    descarga = DescargaFalsaCorpus(
        textos=DOCUMENTOS,
        errores=[(URLS[0], "timeout")],
    )

    corpus = await crear_corpus(URLS, ruta, descargar=descarga)

    assert descarga.llamadas == 1
    assert descarga.kwargs is not None
    assert corpus["urls_recibidas"] == len(URLS)
    assert corpus["urls_con_error"] == 1
    assert corpus["errores"] == [{"url": URLS[0], "motivo": "timeout"}]
    assert corpus["numero_documentos"] == len(DOCUMENTOS)
    assert [d["url"] for d in corpus["documentos"]] == [u for u, _ in DOCUMENTOS]
    assert [d["texto"] for d in corpus["documentos"]] == [t for _, t in DOCUMENTOS]
    assert corpus["hash_corpus"] == calcular_hash_carga(DOCUMENTOS)
    assert all(
        d["hash_documento"] == hash_documento(d["texto"])
        for d in corpus["documentos"]
    )
    assert "embeddings" not in corpus

    assert ruta.is_file()
    en_disco = json.loads(ruta.read_text(encoding="utf-8"))
    assert en_disco == corpus


async def test_crear_corpus_es_determinista_salvo_metadatos(tmp_path):
    descarga = DescargaFalsaCorpus(textos=DOCUMENTOS)
    primero = await crear_corpus(URLS, tmp_path / "uno.json", descargar=descarga)
    segundo = await crear_corpus(URLS, tmp_path / "dos.json", descargar=descarga)

    assert descarga.llamadas == 2
    assert primero["hash_corpus"] == segundo["hash_corpus"]
    assert primero["documentos"] == segundo["documentos"]


# ---------------------------------------------------------------------------
# leer_corpus / ida y vuelta
# ---------------------------------------------------------------------------


async def test_guardar_y_leer_corpus_es_identico(tmp_path):
    ruta = tmp_path / "corpus.json"
    descarga = DescargaFalsaCorpus(textos=DOCUMENTOS)
    creado = await crear_corpus(URLS, ruta, descargar=descarga)

    leido = leer_corpus(ruta)

    assert leido == creado
    assert leido["hash_corpus"] == creado["hash_corpus"]
    assert [d["texto"] for d in leido["documentos"]] == [
        d["texto"] for d in creado["documentos"]
    ]


def test_leer_corpus_archivo_ausente(tmp_path):
    with pytest.raises(FileNotFoundError):
        leer_corpus(tmp_path / "no_existe.json")


def test_leer_corpus_rechaza_json_estructuralmente_invalido(tmp_path):
    ruta = tmp_path / "mal.json"
    ruta.write_text(json.dumps({"documentos": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        leer_corpus(ruta)


# ---------------------------------------------------------------------------
# Modo controlado: carga identica
# ---------------------------------------------------------------------------


async def test_modo_corpus_carga_identica_completa_sin_red(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS)
    registro_ejec, emb, insercion = await ejecutar_modo_corpus(
        monkeypatch, corpus
    )

    assert registro_ejec.estado == COMPLETED
    assert registro_ejec.modo_carga == "corpus"
    assert registro_ejec.corpus_utilizado == "corpus_falso.json"
    assert registro_ejec.carga_identica is True
    assert registro_ejec.hash_carga == corpus["hash_corpus"]
    assert registro_ejec.documentos_cargados == len(DOCUMENTOS)
    assert registro_ejec.tiempo_scraping_s == 0.0
    assert insercion.coleccion_id == COLECCION
    assert insercion.registros is not None


async def test_documentos_y_contenidos_identicos_entre_corridas(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS)

    registro_1, emb_1, insercion_1 = await ejecutar_modo_corpus(
        monkeypatch, corpus, corpus_ruta="c.json"
    )
    registro_2, emb_2, insercion_2 = await ejecutar_modo_corpus(
        monkeypatch, corpus, corpus_ruta="c.json"
    )

    assert registro_1.estado == COMPLETED and registro_2.estado == COMPLETED
    assert registro_1.hash_carga == registro_2.hash_carga
    assert registro_1.documentos_cargados == registro_2.documentos_cargados
    assert registro_1.fragmentos_generados == registro_2.fragmentos_generados

    contenidos_1 = [r["contenido"] for r in insercion_1.registros]
    contenidos_2 = [r["contenido"] for r in insercion_2.registros]
    assert contenidos_1 == contenidos_2
    assert contenidos_1 == [texto.strip() for _, texto in DOCUMENTOS]
    assert emb_1.textos_recibidos == emb_2.textos_recibidos


async def test_speedup_y_eficiencia_con_carga_identica(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS)
    reg_1, _, _ = await ejecutar_modo_corpus(monkeypatch, corpus, workers=1)
    reg_2, _, _ = await ejecutar_modo_corpus(monkeypatch, corpus, workers=2)
    reg_1.tiempo_total_s = 8.0
    reg_2.tiempo_total_s = 4.0

    resumen = construir_resumen([reg_1, reg_2])
    fila_2 = next(f for f in resumen if f["workers"] == 2)

    assert fila_2["carga_identica"] is True
    assert fila_2["speedup"] == 2.0
    assert fila_2["eficiencia"] == 1.0


# ---------------------------------------------------------------------------
# Modo controlado: deteccion de carga invalida -> INVALID_LOAD
# ---------------------------------------------------------------------------


async def test_hash_de_carga_inconsistente_marca_invalid_load(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS, hash_corpus="0" * 64)
    registro_ejec, emb, insercion = await ejecutar_modo_corpus(
        monkeypatch, corpus, emb=EmbProhibido()
    )

    assert registro_ejec.estado == INVALID_LOAD
    assert "INVALID_LOAD" in (registro_ejec.mensaje_error or "")
    assert registro_ejec.carga_identica is False
    assert insercion.registros is None
    assert isinstance(emb, EmbProhibido)


async def test_corpus_alterado_marca_invalid_load(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS)
    corpus["documentos"][0]["texto"] = "texto manipulado despues de congelar"

    registro_ejec, _, insercion = await ejecutar_modo_corpus(
        monkeypatch, corpus, emb=EmbProhibido()
    )

    assert registro_ejec.estado == INVALID_LOAD
    assert registro_ejec.hash_carga != corpus["hash_corpus"]
    assert insercion.registros is None


async def test_diferencia_cantidad_documentos_marca_invalid_load(monkeypatch):
    corpus = corpus_falso(DOCUMENTOS)
    extra = ("https://www.ejemplo.bo/corpus-d", "Documento D anadido.")
    corpus["documentos"].append(
        {
            "url": extra[0],
            "texto": extra[1],
            "hash_documento": hash_documento(extra[1]),
        }
    )
    corpus["hash_corpus"] = calcular_hash_carga(DOCUMENTOS + [extra])
    corpus["numero_documentos"] = len(DOCUMENTOS)

    registro_ejec, _, insercion = await ejecutar_modo_corpus(
        monkeypatch, corpus, emb=EmbProhibido()
    )

    assert registro_ejec.documentos_cargados == len(DOCUMENTOS) + 1
    assert registro_ejec.hash_carga == corpus["hash_corpus"]
    assert registro_ejec.estado == INVALID_LOAD
    assert insercion.registros is None


# ---------------------------------------------------------------------------
# Gating de metricas por identidad de carga (reutiliza el motor existente)
# ---------------------------------------------------------------------------


def test_construir_resumen_exige_mismo_hash_de_carga():
    mismo = construir_resumen(
        [
            registro(1, 8.0, hash_carga="mismo", documentos=3, fragmentos=3),
            registro(2, 4.0, hash_carga="mismo", documentos=3, fragmentos=3),
        ]
    )
    fila_misma = next(f for f in mismo if f["workers"] == 2)
    assert fila_misma["carga_identica"] is True
    assert fila_misma["speedup"] == 2.0

    distinto = construir_resumen(
        [
            registro(1, 8.0, hash_carga="hash-1", documentos=3, fragmentos=3),
            registro(2, 4.0, hash_carga="hash-2", documentos=3, fragmentos=3),
        ]
    )
    fila_distinta = next(f for f in distinto if f["workers"] == 2)
    assert fila_distinta["carga_identica"] is False
    assert fila_distinta["speedup"] is None
    assert fila_distinta["eficiencia"] is None


def test_construir_resumen_exige_misma_cantidad_de_fragmentos():
    resumen = construir_resumen(
        [
            registro(1, 8.0, hash_carga="h", documentos=3, fragmentos=49),
            registro(2, 4.0, hash_carga="h", documentos=3, fragmentos=43),
        ]
    )
    fila_2 = next(f for f in resumen if f["workers"] == 2)
    assert fila_2["carga_identica"] is False
    assert fila_2["speedup"] is None
