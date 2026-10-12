"""Pruebas de la FASE 5 (benchmark de concurrencia) 100% mockeadas.

NO tocan red, Gemini ni base de datos reales: se inyectan fakes para la
descarga, el cliente de embeddings (con ``historico_llamadas``) y la
insercion, replicando el patron de DI de `tests/test_consulta.py`.

Cubren los REQUISITOS 1-12 de la fase (sin mediciones reales):

- REQUISITO 1: nivel de concurrencia validado y propagado a la descarga.
- REQUISITO 2/9: estructura de resultados JSON y conteos coherentes
  (urls_recibidas = procesadas + error; fragmentos = embeddings = insertados).
- REQUISITO 3: timers por fase con `perf_counter` (metricas no nulas).
- REQUISITO 4/7/12: SOLO ejecuciones COMPLETED alimentan T_p/S_p/E_p; sin T1
  no hay metricas (nunca valores enganosos).
- REQUISITO 5: las ejecuciones PARTIAL/FAILED no entran en T/S/E.
- REQUISITO 6: 429/RESOURCE_EXHAUSTED -> RATE_LIMITED (modo estricto); en
  modo `permitir` se registran los eventos (nunca se ocultan).
- DATASET/graficas: helpers puros `cargar_dataset`/`seleccionar_urls`/
  `validar_urls` y `cargar_resultados`/`preparar_series`.
"""

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import app.services.benchmark as benchmark_svc
from app.database.repository import VECTOR_DIMENSION
from app.services.benchmark import (
    COMPLETED,
    FAILED,
    PARTIAL,
    RATE_LIMITED,
    RegistroEjecucion,
    cargar_dataset,
    construir_resumen,
    construir_resultado,
    ejecutar_benchmark,
    extraer_conteos,
    porcentajes_por_etapa,
    seleccionar_urls,
    validar_urls,
    validar_workers,
)
from app.services.downloader import ResultadoDescarga
from app.services.embeddings import ErrorEmbeddings
from app.services.graficas import cargar_resultados, preparar_series

RAIZ = Path(__file__).resolve().parents[1]
RUTA_DATASET = RAIZ / "benchmarks" / "urls_benchmark.json"

COLECCION = "coleccion_benchmark_pruebas"
URLS = [
    "https://www.ejemplo.bo/a",
    "https://www.ejemplo.bo/b",
    "https://www.ejemplo.bo/c",
]

TEXTO = "Normativa de prueba. " * 40  # 840 chars: 1 fragmento (< chunk 1200)


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


class DescargaFalsa:
    """Sustituto de `descargar_y_limpiar`: registra args y devuelve textos."""

    def __init__(self, textos=None, errores=None):
        self.textos = textos if textos is not None else URLS[:2]
        self.errores = errores if errores is not None else []
        self.kwargs = None
        self.lanzar = None

    async def __call__(self, urls, **kwargs):
        self.kwargs = kwargs
        await asyncio.sleep(0.01)
        if self.lanzar:
            raise self.lanzar
        resultado = ResultadoDescarga()
        for url in self.textos:
            resultado.registrar_ok(url, TEXTO)
        for url, motivo in self.errores:
            resultado.registrar_error(url, motivo)
        return resultado


class EmbFalso:
    """Cliente de embeddings falso con `historico_llamadas` configurable."""

    def __init__(self, cantidad=None, historial=None, error=None, dimension=None):
        self.cantidad = cantidad
        self.historico_llamadas = list(historial or [])
        self.error = error
        self.dimension = dimension or VECTOR_DIMENSION
        self.textos = []

    async def generar(self, textos, cliente=None):
        self.textos = list(textos)
        await asyncio.sleep(0.01)
        if self.error:
            raise self.error
        n = self.cantidad if self.cantidad is not None else len(textos)
        vector = [0.01] * self.dimension
        vector[0] = 1.0
        return [list(vector) for _ in range(n)]


class InsercionFalsa:
    """Sustituto de `insertar_fragmentos`: registra lo que recibe."""

    def __init__(self, error=None):
        self.registros = None
        self.coleccion_id = None
        self.error = error

    async def __call__(self, sesion, *, coleccion_id, registros):
        await asyncio.sleep(0.01)
        if self.error:
            raise self.error
        self.coleccion_id = coleccion_id
        self.registros = list(registros)
        return len(registros)


class _SesionFalsa:
    """Sesion con commit de no-op: la insercion se parchea, no se usa."""

    async def commit(self):
        return None


@asynccontextmanager
async def sesion_falsa():
    yield _SesionFalsa()


def emb_con_historial(*eventos):
    """Devuelve un EmbFalso con la lista `historico_llamadas` asignada."""
    return EmbFalso(historial=[tuple(e) for e in eventos])


async def registrar_benchmark(
    monkeypatch,
    descarga=None,
    emb=None,
    insercion=None,
    *,
    modo_429="estricto",
    urls=URLS,
    workers=1,
):
    """Ejecuta el benchmark con dobles inyectados y devuelve el registro."""
    if descarga is None:
        descarga = DescargaFalsa()
    if emb is None:
        emb = EmbFalso()  # nunca se toca Gemini real en estas pruebas
    if insercion is None:
        insercion = InsercionFalsa()
    monkeypatch.setattr(benchmark_svc, "insertar_fragmentos", insercion)
    return await ejecutar_benchmark(
        list(urls),
        coleccion_id=COLECCION,
        workers=workers,
        chunk_size=1200,
        chunk_overlap=200,
        modo_429=modo_429,
        emb_client=emb,
        descargar=descarga,
        abrir=sesion_falsa,
    )


def ejecucion(
    workers: int, tiempo: float, estado=COMPLETED, *, eventos_429: int = 0
) -> RegistroEjecucion:
    r = RegistroEjecucion(workers=workers, estado=estado)
    r.tiempo_total_s = tiempo
    r.eventos_429 = eventos_429
    return r


# ---------------------------------------------------------------------------
# REQUISITO 1: validacion del nivel de concurrencia y de coleccion
# ---------------------------------------------------------------------------


async def test_validar_workers_acepta_rango_valido():
    for nivel in (1, 2, 8, 64):
        assert validar_workers(nivel) == nivel


async def test_validar_workers_rechaza_invalidos():
    for nivel in (0, -1, 65, 1.5, "2", True, None):
        with pytest.raises(ValueError):
            validar_workers(nivel)


async def test_concurrencia_pedida_se_propaga_a_la_descarga(monkeypatch):
    descarga = DescargaFalsa()
    await registrar_benchmark(monkeypatch, descarga=descarga, workers=8)
    assert descarga.kwargs["concurrency"] == 8


async def test_coleccion_id_ilegal_aborta_antes_de_descargar(monkeypatch):
    descarga = DescargaFalsa()
    with pytest.raises(ValueError):
        await ejecutar_benchmark(
            URLS,
            coleccion_id="coleccion/ilegal",
            workers=1,
            chunk_size=1200,
            chunk_overlap=200,
            descargar=descarga,
            abrir=sesion_falsa,
        )
    assert descarga.kwargs is None  # ni siquiera se descargo


async def test_modo_429_invalido_se_rechaza(monkeypatch):
    with pytest.raises(ValueError):
        await ejecutar_benchmark(
            URLS,
            coleccion_id=COLECCION,
            workers=1,
            chunk_size=1200,
            chunk_overlap=200,
            modo_429="fantasma",
            descargar=DescargaFalsa(),
            abrir=sesion_falsa,
        )


# ---------------------------------------------------------------------------
# REQUISITOS 2, 3 y 9: flujo feliz, timers y conteos coherentes
# ---------------------------------------------------------------------------


async def test_ejecucion_completa_registra_todo(monkeypatch):
    descarga = DescargaFalsa(textos=URLS)
    emb = emb_con_historial((200, False), (200, False))
    insercion = InsercionFalsa()
    registro = await registrar_benchmark(
        monkeypatch, descarga=descarga, emb=emb, insercion=insercion
    )

    assert registro.estado == COMPLETED
    assert registro.workers == 1
    assert registro.urls_recibidas == len(URLS)
    assert registro.urls_procesadas == len(URLS)
    assert registro.urls_con_error == 0
    assert registro.errores == []
    fragmentos = registro.fragmentos_generados
    assert fragmentos == len(URLS)  # 1 chunk por URL con este tamano
    assert registro.embeddings_generados == fragmentos
    assert registro.registros_insertados == fragmentos
    assert insercion.coleccion_id == COLECCION
    assert insercion.registros[0]["origen_url"] == URLS[0]

    # REQUISITO 3: los tramos tienen reloj monotono propio (>= 10ms simulados).
    assert registro.tiempo_scraping_s >= 0.005
    assert registro.tiempo_embeddings_s >= 0.005
    assert registro.tiempo_insercion_s >= 0.005
    assert registro.tiempo_total_s > 0

    # REQUISITO 9: el historial observacional se vuelca a conteos.
    assert registro.lotes_enviados == 2
    assert registro.lotes_exitosos == 2
    assert registro.reintentos == 0
    assert registro.eventos_429 == 0


async def test_url_count_consistency_con_errores_parciales(monkeypatch):
    descarga = DescargaFalsa(
        textos=URLS[:1], errores=[(URLS[1], "timeout"), (URLS[2], "HTTP 503")]
    )
    registro = await registrar_benchmark(monkeypatch, descarga=descarga)

    # REQUISITO 2: recibidas = procesadas + con_error SIEMPRE.
    assert registro.urls_recibidas == len(URLS)
    assert registro.urls_procesadas + registro.urls_con_error == registro.urls_recibidas
    assert registro.urls_con_error == 2
    assert registro.errores[0]["url"] == URLS[1]
    assert registro.errores[1]["motivo"] == "HTTP 503"
    assert registro.estado == PARTIAL  # carga incompleta, no comparable


async def test_dimension_incorrecta_aborta_la_ejecucion(monkeypatch):
    emb = EmbFalso(cantidad=1, dimension=VECTOR_DIMENSION - 3)
    insercion = InsercionFalsa()
    registro = await registrar_benchmark(
        monkeypatch, emb=emb, insercion=insercion
    )
    assert registro.estado == FAILED
    assert "dimension" in (registro.mensaje_error or "").lower()
    assert insercion.registros is None  # no se inserto nada


# ---------------------------------------------------------------------------
# REQUISITO 5: PARTIAL y FAILED quedan fuera de las metricas
# ---------------------------------------------------------------------------


async def test_ejecucion_parcial_no_entra_en_metricas(monkeypatch):
    descarga = DescargaFalsa(
        textos=[], errores=[(URLS[0], "contenido vacio")]
    )
    registro = await registrar_benchmark(monkeypatch, descarga=descarga)
    assert registro.estado == PARTIAL

    resumen = construir_resumen([registro])
    assert resumen[0]["tiempo_total_s"] is None
    assert resumen[0]["speedup"] is None
    assert resumen[0]["eficiencia"] is None
    assert resumen[0]["estado"] == PARTIAL


async def test_fallo_en_descarga_marca_failed(monkeypatch):
    descarga = DescargaFalsa()
    descarga.lanzar = RuntimeError("sin conexion")
    registro = await registrar_benchmark(monkeypatch, descarga=descarga)
    assert registro.estado == FAILED
    assert (registro.mensaje_error or "").startswith("scraping:")


async def test_fallo_en_insercion_marca_failed(monkeypatch):
    insercion = InsercionFalsa(error=RuntimeError("postgres caido"))
    registro = await registrar_benchmark(monkeypatch, insercion=insercion)
    assert registro.estado == FAILED
    assert (registro.mensaje_error or "").startswith("insercion:")


async def test_fallo_embeddings_no_limitacion_marca_failed(monkeypatch):
    emb = EmbFalso(error=ErrorEmbeddings("Gemini devolvio una respuesta invalida"))
    registro = await registrar_benchmark(monkeypatch, emb=emb)
    assert registro.estado == FAILED


# ---------------------------------------------------------------------------
# REQUISITO 6: RATE_LIMITED (modo estricto) y modo permitir (no ocultar)
# ---------------------------------------------------------------------------


async def test_429_recuperado_en_estricto_invalida_ejecucion(monkeypatch):
    """Aunque Gemini recupere, el modo estricto NO admite la corrida."""
    emb = emb_con_historial((200, False), (429, True), (200, False))
    insercion = InsercionFalsa()
    registro = await registrar_benchmark(
        monkeypatch, emb=emb, insercion=insercion
    )

    assert registro.estado == RATE_LIMITED
    assert registro.eventos_429 == 1
    assert registro.lotes_enviados == 3
    assert insercion.registros is None  # NO se inserto: corrida descartada
    assert "429" in (registro.mensaje_error or "")


async def test_error_de_embeddings_por_quota_marca_rate_limited(monkeypatch):
    emb = EmbFalso(
        error=ErrorEmbeddings(
            "Gemini no respondio tras 5 intentos: HTTP 429: quota exceeded"
        ),
        historial=[(429, True)] * 5,
    )
    registro = await registrar_benchmark(monkeypatch, emb=emb)
    assert registro.estado == RATE_LIMITED
    assert registro.eventos_429 == 5  # los agotamientos quedan grabados
    assert "429" in (registro.mensaje_error or "")


async def test_modo_permitir_registra_y_completa(monkeypatch):
    """modo_429=permitir: se completa insertando, PERO los 429 quedan grabados."""
    emb = emb_con_historial((200, False), (429, True), (200, False))
    insercion = InsercionFalsa()
    registro = await registrar_benchmark(
        monkeypatch,
        descarga=DescargaFalsa(textos=URLS),
        emb=emb,
        insercion=insercion,
        modo_429="permitir",
    )

    assert registro.estado == COMPLETED
    assert registro.eventos_429 == 1
    assert registro.reintentos == 1
    assert registro.registros_insertados == len(URLS)


# ---------------------------------------------------------------------------
# REQUISITOS 7 y 12: speedup/eficiencia, T1 obligatorio, sin numeros falsos
# ---------------------------------------------------------------------------


async def test_speedup_y_eficiencia_con_valores_de_trabajo():
    resumen = construir_resumen(
        [ejecucion(1, 8.0), ejecucion(2, 4.0), ejecucion(4, 2.0)]
    )
    assert resumen[0]["tiempo_total_s"] == 8.0
    assert resumen[0]["speedup"] == 1.0
    assert resumen[0]["eficiencia"] == 1.0
    assert resumen[1]["speedup"] == 2.0
    assert resumen[1]["eficiencia"] == 1.0
    assert resumen[2]["speedup"] == 4.0
    assert resumen[2]["eficiencia"] == 1.0
    assert all(fila["T1_base_s"] == 8.0 for fila in resumen)


async def test_speedup_sublineal_se_calcula_correctamente():
    resumen = construir_resumen([ejecucion(1, 10.0), ejecucion(2, 6.0)])
    assert resumen[1]["speedup"] == round(10.0 / 6.0, 6)
    assert resumen[1]["eficiencia"] == round(10.0 / 6.0 / 2, 6)


async def test_sin_t1_no_hay_metricas():
    """REQUISITO 12: si falta T1 (workers=1), nunca hay S/E enganosos."""
    resumen = construir_resumen([ejecucion(3, 2.0), ejecucion(4, 1.5)])
    for fila in resumen:
        assert fila["tiempo_total_s"] is not None
        assert fila["speedup"] is None
        assert fila["eficiencia"] is None
        assert fila["T1_base_s"] is None


async def test_sin_ninguna_ejecucion_valida_todo_none():
    resumen = construir_resumen(
        [
            ejecucion(1, 9.0, PARTIAL),
            ejecucion(2, 3.0, RATE_LIMITED, eventos_429=3),
        ]
    )
    assert resumen[0]["tiempo_total_s"] is None
    assert resumen[1]["tiempo_total_s"] is None
    assert resumen[0]["speedup"] is None and resumen[1]["speedup"] is None
    assert resumen[0]["afectado_429"] is False
    assert resumen[1]["afectado_429"] is True


async def test_promedio_de_repeticiones_solo_validas():
    resumen = construir_resumen([ejecucion(1, 8.0), ejecucion(1, 10.0)])
    assert resumen[0]["tiempo_total_s"] == 9.0
    assert resumen[0]["speedup"] == 1.0


async def test_descarta_ejecuciones_no_validas_del_promedio():
    resumen = construir_resumen(
        [
            ejecucion(1, 8.0),
            ejecucion(2, 4.0),
            # No entra en el promedio: corrida descartada por 429.
            ejecucion(2, 1.0, RATE_LIMITED, eventos_429=2),
        ]
    )
    fila2 = next(f for f in resumen if f["workers"] == 2)
    assert fila2["tiempo_total_s"] == 4.0
    assert fila2["ejecuciones_validas"] == 1
    assert fila2["afectado_429"] is True  # el evento NO se oculta
    assert fila2["speedup"] == 2.0


# ---------------------------------------------------------------------------
# REQUISITOS 8 y 9: porcentajes por etapa y estructura / serializacion
# ---------------------------------------------------------------------------


async def test_porcentajes_por_etapa_suman_100():
    registro = RegistroEjecucion(workers=1, estado=COMPLETED)
    registro.tiempo_scraping_s = 0.3
    registro.tiempo_chunking_s = 0.1
    registro.tiempo_embeddings_s = 0.5
    registro.tiempo_insercion_s = 0.1
    registro.tiempo_total_s = 1.0

    porcentajes = porcentajes_por_etapa(registro)
    assert porcentajes["scraping"] == 30.0
    assert porcentajes["chunking"] == 10.0
    assert porcentajes["embeddings"] == 50.0
    assert porcentajes["insercion"] == 10.0
    assert abs(sum(porcentajes.values()) - 100.0) < 0.01


async def test_porcentajes_nulos_sin_tiempo_total():
    assert porcentajes_por_etapa(RegistroEjecucion(workers=1)) is None


async def test_estructura_del_resultado_serializable(monkeypatch):
    registro = await registrar_benchmark(
        monkeypatch, emb=emb_con_historial((200, False))
    )
    ejecuciones = [registro, ejecucion(2, 4.0, RATE_LIMITED)]
    resultado = construir_resultado(
        coleccion_id=COLECCION,
        urls=URLS,
        configuracion={"workers": [1, 2], "modo_429": "estricto"},
        ejecuciones=ejecuciones,
    )
    assert resultado["coleccion_id"] == COLECCION
    assert resultado["numero_urls"] == len(URLS)
    assert resultado["urls"] == list(URLS)
    assert len(resultado["ejecuciones"]) == 2
    assert "porcentajes_por_etapa" in resultado["ejecuciones"][0]
    assert "resumen" in resultado
    # REQUISITO 9: salida reproducible y serializable a JSON.
    json.dumps(resultado)


async def test_historico_observacional_se_convierte_en_conteos():
    cliente = emb_con_historial(
        (200, False), (429, True), (200, False), (500, False)
    )
    conteos = extraer_conteos(cliente)
    assert conteos == {
        "lotes_enviados": 4,
        "lotes_exitosos": 2,
        "reintentos": 2,
        "eventos_429": 1,  # solo la entrada (429, True) cuenta como evento
    }

    class ClienteSinHistorial:
        pass

    assert extraer_conteos(ClienteSinHistorial()) == {
        "lotes_enviados": 0,
        "lotes_exitosos": 0,
        "reintentos": 0,
        "eventos_429": 0,
    }


# ---------------------------------------------------------------------------
# Dataset y helpers puros
# ---------------------------------------------------------------------------


async def test_dataset_carga_las_dos_categorias():
    datos = cargar_dataset(RUTA_DATASET)
    assert len(datos["financiero"]) == 5
    assert len(datos["tributario"]) == 5

    financiero = seleccionar_urls(datos, "financiero")
    tributario = seleccionar_urls(datos, "tributario")
    todas = seleccionar_urls(datos, "todas")
    assert len(financiero) == 5 and len(tributario) == 5
    assert len(todas) == 10
    assert todas == financiero + tributario  # carga identica en 'todas'
    assert not set(financiero) & set(tributario)


async def test_dataset_errores_claros():
    with pytest.raises(FileNotFoundError):
        cargar_dataset(RAIZ / "benchmarks" / "no_existe.json")
    with pytest.raises(FileNotFoundError):
        cargar_dataset(RAIZ / "benchmarks")  # directorio, no archivo JSON


async def test_validar_urls_rechaza_vacias_e_invalidas():
    with pytest.raises(ValueError):
        validar_urls([])
    with pytest.raises(ValueError):
        validar_urls(["https://ok.bo/a", "archivo://nope", "sin-scheme"])
    assert validar_urls(["https://ok.bo/a", "http://ok.bo/b"]) == [
        "https://ok.bo/a",
        "http://ok.bo/b",
    ]


async def test_seleccionar_categoria_desconocida_error_claro():
    datos = {"financiero": [], "tributario": []}
    with pytest.raises(ValueError) as error:
        seleccionar_urls(datos, "agricultura")
    assert "financiero" in str(error.value)


# ---------------------------------------------------------------------------
# Graficas: helpers puros (sin matplotlib en estas pruebas)
# ---------------------------------------------------------------------------


async def test_cargar_resultados_y_preparar_series(tmp_path):
    resumen = [
        {"workers": 1, "tiempo_total_s": 8.0, "speedup": 1.0, "eficiencia": 1.0,
         "estado": COMPLETED, "afectado_429": False},
        {"workers": 4, "tiempo_total_s": None, "speedup": None, "eficiencia": None,
         "estado": RATE_LIMITED, "afectado_429": True},
    ]
    ruta = tmp_path / "result.json"
    ruta.write_text(
        json.dumps({"ejecuciones": [], "resumen": resumen}), encoding="utf-8"
    )
    datos = cargar_resultados(ruta)
    series = preparar_series(datos)
    # Solo las COMPLETED entran en las graficas (no se inventa nada).
    assert series["workers"] == [1]
    assert series["tiempo_total_s"] == [8.0]
    assert series["speedup"] == [1.0]
    assert series["estados"][4] == RATE_LIMITED


async def test_cargar_resultados_rechaza_json_invalido(tmp_path):
    ruta = tmp_path / "mal.json"
    ruta.write_text(json.dumps({"clave": "suelt"}), encoding="utf-8")
    with pytest.raises(ValueError):
        cargar_resultados(ruta)
    with pytest.raises(FileNotFoundError):
        cargar_resultados(tmp_path / "ausente.json")


# ---------------------------------------------------------------------------
# Modo --solo-scraping: mide SOLO la descarga (sin Gemini ni PostgreSQL)
# ---------------------------------------------------------------------------


class EmbProhibido:
    """Falla si el modo solo-scraping llega a pedir embeddings."""

    async def generar(self, textos, cliente=None):
        raise AssertionError("solo-scraping no debe llamar a embeddings")


class InsercionProhibida:
    """Falla si el modo solo-scraping llega a insertar."""

    async def __call__(self, sesion, *, coleccion_id, registros):
        raise AssertionError("solo-scraping no debe insertar en PostgreSQL")


def abrir_prohibido():
    """Falla si el modo solo-scraping llega a abrir sesion de base de datos."""
    raise AssertionError("solo-scraping no debe abrir sesion de PostgreSQL")


async def ejecutar_solo_scraping(
    monkeypatch, descarga=None, *, urls=URLS, workers=1
):
    """Ejecuta el benchmark en modo solo-scraping con dependencias prohibidas."""
    if descarga is None:
        descarga = DescargaFalsa()
    monkeypatch.setattr(benchmark_svc, "insertar_fragmentos", InsercionProhibida())
    return await ejecutar_benchmark(
        list(urls),
        coleccion_id=COLECCION,
        workers=workers,
        chunk_size=1200,
        chunk_overlap=200,
        emb_client=EmbProhibido(),
        descargar=descarga,
        abrir=abrir_prohibido,
        solo_scraping=True,
    )


async def test_solo_scraping_termina_sin_chunking_embeddings_ni_insercion(monkeypatch):
    descarga = DescargaFalsa(textos=URLS)
    registro = await ejecutar_solo_scraping(monkeypatch, descarga=descarga)

    assert registro.estado == COMPLETED
    assert registro.modo_carga == "scraping"
    assert registro.tiempo_scraping_s >= 0.005  # la descarga SI se midio
    assert registro.tiempo_chunking_s == 0.0
    assert registro.tiempo_embeddings_s == 0.0
    assert registro.tiempo_insercion_s == 0.0
    assert registro.fragmentos_generados == 0
    assert registro.embeddings_generados == 0
    assert registro.registros_insertados == 0
    assert registro.lotes_enviados == 0
    assert registro.eventos_429 == 0


async def test_solo_scraping_propaga_workers_a_la_concurrencia(monkeypatch):
    descarga = DescargaFalsa(textos=URLS)
    await ejecutar_solo_scraping(monkeypatch, descarga=descarga, workers=8)
    assert descarga.kwargs["concurrency"] == 8


async def test_solo_scraping_con_errores_no_es_completed(monkeypatch):
    descarga = DescargaFalsa(
        textos=URLS[:1], errores=[(URLS[1], "timeout"), (URLS[2], "HTTP 503")]
    )
    registro = await ejecutar_solo_scraping(monkeypatch, descarga=descarga)

    assert registro.estado == PARTIAL
    assert registro.estado != COMPLETED
    assert registro.urls_con_error == 2


async def test_solo_scraping_no_es_compatible_con_corpus():
    with pytest.raises(ValueError):
        await ejecutar_benchmark(
            URLS,
            coleccion_id=COLECCION,
            workers=1,
            chunk_size=1200,
            chunk_overlap=200,
            corpus={"documentos": [], "hash_corpus": "x"},
            solo_scraping=True,
        )


async def test_solo_scraping_speedup_solo_con_urls_comparables(monkeypatch):
    reg_1 = await ejecutar_solo_scraping(
        monkeypatch, descarga=DescargaFalsa(textos=URLS), workers=1
    )
    reg_2 = await ejecutar_solo_scraping(
        monkeypatch, descarga=DescargaFalsa(textos=URLS), workers=2
    )
    assert reg_1.hash_carga == reg_2.hash_carga  # misma lista de URLs

    reg_1.tiempo_total_s = 8.0
    reg_2.tiempo_total_s = 4.0
    resumen = construir_resumen([reg_1, reg_2])
    fila_2 = next(f for f in resumen if f["workers"] == 2)
    assert fila_2["carga_identica"] is True
    assert fila_2["speedup"] == 2.0
    assert fila_2["eficiencia"] == 1.0

    # Con OTRA lista de URLs la carga deja de ser comparable: sin S/E.
    reg_3 = await ejecutar_solo_scraping(
        monkeypatch,
        descarga=DescargaFalsa(textos=URLS[:2]),
        urls=URLS[:2],
        workers=2,
    )
    reg_3.tiempo_total_s = 1.0
    assert reg_3.hash_carga != reg_1.hash_carga
    resumen_mixto = construir_resumen([reg_1, reg_3])
    fila_3 = next(f for f in resumen_mixto if f["workers"] == 2)
    assert fila_3["carga_identica"] is False
    assert fila_3["speedup"] is None
    assert fila_3["eficiencia"] is None


def _cargar_cli_benchmark():
    """Carga `scripts/benchmark_concurrencia.py` sin ejecutarlo como script."""
    ruta = RAIZ / "scripts" / "benchmark_concurrencia.py"
    spec = importlib.util.spec_from_file_location("benchmark_concurrencia_cli", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_solo_scraping_no_sobrescribe_los_resultados_originales(tmp_path):
    cli = _cargar_cli_benchmark()

    original_json = tmp_path / "benchmark_resultados.json"
    original_csv = tmp_path / "benchmark_resumen.csv"
    original_json.write_text('{"original": true}', encoding="utf-8")
    original_csv.write_text("original", encoding="utf-8")

    resumen = [
        {
            "workers": 1,
            "tiempo_total_s": 2.0,
            "speedup": 1.0,
            "eficiencia": 1.0,
            "estado": COMPLETED,
            "ejecuciones": 1,
            "ejecuciones_validas": 1,
            "afectado_429": False,
            "carga_identica": True,
            "hash_carga": "h",
            "T1_base_s": 2.0,
        }
    ]
    datos = {"ejecuciones": [], "resumen": resumen}

    ruta_json, ruta_csv = cli._guardar_resultados(
        datos, tmp_path, resumen, solo_scraping=True
    )

    assert ruta_json.name == "benchmark_scraping_resultados.json"
    assert ruta_csv.name == "benchmark_scraping_resumen.csv"
    assert original_json.read_text(encoding="utf-8") == '{"original": true}'
    assert original_csv.read_text(encoding="utf-8") == "original"


def test_guardar_resultados_integral_conserva_nombres(tmp_path):
    cli = _cargar_cli_benchmark()
    resumen = []
    ruta_json, ruta_csv = cli._guardar_resultados({}, tmp_path, resumen)
    assert ruta_json.name == "benchmark_resultados.json"
    assert ruta_csv.name == "benchmark_resumen.csv"