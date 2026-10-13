"""Fusion de resultados vectoriales y lexicos (busqueda hibrida).

Principio: la busqueda VECTORIAL existente se CONSERVA; la busqueda LEXICA la
COMPLEMENTA. Cuando ambas estan disponibles se combinan; si solo hay una, se
devuelve esa sin inventar nada.

La combinacion usa *Reciprocal Rank Fusion* (RRF), un metodo de fusion por
rangos que no exige normalizar puntuaciones heterogeneas (similitud coseno vs.
relevancia textual)::

    rrf(d) = sum_r 1 / (k + rango_r(d))

donde ``rango_r(d)`` es la posicion (base 0) del documento ``d`` en la lista
del recuperador ``r`` y ``k`` es una constante (60 por defecto). A las
coincidencias exactas (por ejemplo, el numero de resolucion aparece literal en
el fragmento) se les suma un bono modesto ``1/k`` para priorizarlas, tal y como
justifica el requisito de priorizar referencias exactas.

La puntuacion ``puntuacion_rrf`` que se expone NO es similitud coseno. Se
etiqueta y documenta como tal para no presentarla como si lo fuera.
"""

from __future__ import annotations

from collections.abc import Callable

from app.models.consulta import EvidenciaConsulta
from app.models.normativa import NormativaMatch, NormativaMatchLexico
from app.utils.referencias import extraer_pagina, extraer_referencia

TIPO_VECTORIAL = "vectorial"
TIPO_LEXICA = "lexica"
TIPO_HIBRIDA = "hibrida"

RRF_K_POR_DEFECTO = 60


def fusionar_resultados(
    vectoriales: list[NormativaMatch],
    lexicas: list[NormativaMatchLexico],
    *,
    limite: int,
    es_prueba: Callable[[str | None], bool],
    rrf_k: int = RRF_K_POR_DEFECTO,
) -> list[EvidenciaConsulta]:
    """Combina (o elige) resultados vectoriales y lexicos; deduplica por id.

    - deduplica por ``id`` de fragmento (el mismo fragmento en ambas listas
      aparece UNA sola vez, marcado como ``hibrida``);
    - orden reproducible: puntuacion RRF descendente y, como desempate, ``id``
      ascendente;
    - ``limite`` acota el numero final (configurable via ``top_k``);
    - conserva la coleccion de origen de cada fragmento (el llamador verifica
      antes el aislamiento).
    """
    if limite < 1:
        raise ValueError("limite debe ser mayor o igual a 1")
    if rrf_k < 1:
        raise ValueError("rrf_k debe ser mayor o igual a 1")

    acumulado: dict[int, dict] = {}

    for rango, fila in enumerate(vectoriales):
        estado = acumulado.setdefault(fila.id, {"vectorial": None, "lexica": None})
        if estado["vectorial"] is None:
            estado["vectorial"] = (rango, fila)

    for rango, fila in enumerate(lexicas):
        estado = acumulado.setdefault(fila.id, {"vectorial": None, "lexica": None})
        if estado["lexica"] is None:
            estado["lexica"] = (rango, fila)

    evidencias: list[EvidenciaConsulta] = []
    for ident, estado in acumulado.items():
        vectorial = estado["vectorial"]
        lexica = estado["lexica"]

        puntuacion = 0.0
        if vectorial is not None:
            puntuacion += 1.0 / (rrf_k + vectorial[0])
        if lexica is not None:
            puntuacion += 1.0 / (rrf_k + lexica[0])

        exacta = bool(lexica is not None and lexica[1].coincidencia_exacta)
        if exacta:
            puntuacion += 1.0 / rrf_k

        if vectorial is not None and lexica is not None:
            tipo = TIPO_HIBRIDA
        elif vectorial is not None:
            tipo = TIPO_VECTORIAL
        else:
            tipo = TIPO_LEXICA

        base = vectorial[1] if vectorial is not None else lexica[1]
        similitud = vectorial[1].similitud if vectorial is not None else None
        contenido = str(base.contenido)

        evidencias.append(
            EvidenciaConsulta(
                id=ident,
                coleccion_id=base.coleccion_id,
                origen_url=base.origen_url,
                similitud=similitud,
                contenido=contenido,
                es_datos_prueba=bool(es_prueba(base.origen_url)),
                tipo_busqueda=tipo,
                puntuacion_rrf=round(puntuacion, 10),
                puntuacion_lexica=(
                    float(lexica[1].rank_fts) if lexica is not None else None
                ),
                coincidencia_exacta=exacta,
                terminos_coincidentes=(
                    int(lexica[1].terminos) if lexica is not None else 0
                ),
                pagina=extraer_pagina(contenido),
                referencia=extraer_referencia(contenido),
            )
        )

    evidencias.sort(key=lambda ev: (-ev.puntuacion_rrf, ev.id))
    return evidencias[:limite]
