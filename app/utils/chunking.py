"""Troceado de texto en fragmentos solapados (chunking).

FASE 3, requisito C.

Estrategia: ventana deslizante sobre el texto ya limpio.

    inicio    = 0
    fin       = inicio + chunk_size
    siguiente = inicio + (chunk_size - chunk_overlap)

Invariantes que garantiza :func:`dividir_en_fragmentos`:

* todo fragmento mide ``<= chunk_size``;
* la union de los fragmentos cubre exactamente el texto de entrada;
* el avance de la ventana es fijo (``chunk_size - chunk_overlap``), por lo que
  dos ventanas consecutivas solapan ``chunk_overlap`` caracteres;
* siempre termina: el avance es ``chunk_size - chunk_overlap >= 1``.

Frontera de palabra: se recorta el final de un fragmento en el ultimo espacio
*unicamente si ese corte cae dentro de la zona de solape* (indice ``>= paso``).
Esa condicion es la que evita perder texto: todo lo que se descarta del
fragmento ``k`` vuelve a estar dentro de la ventana del fragmento ``k+1``.
Si el solape es 0 no existe zona de solape y no se recorta nunca, lo cual es
coherente: con solape 0 un recorte dejaria un hueco.
"""

from __future__ import annotations


class ConfiguracionChunkingError(ValueError):
    """Configuracion de troceado invalida (requisito C)."""


def validar_configuracion(chunk_size: int, chunk_overlap: int) -> None:
    """Valida la configuracion de troceado.

    Reglas:
      * ``chunk_size`` debe ser > 0;
      * ``chunk_overlap`` debe ser >= 0;
      * ``chunk_overlap`` debe ser < ``chunk_size``.

    Lanza :class:`ConfiguracionChunkingError` con un mensaje en castellano.
    """
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ConfiguracionChunkingError("chunk_size debe ser un entero.")
    if isinstance(chunk_overlap, bool) or not isinstance(chunk_overlap, int):
        raise ConfiguracionChunkingError("chunk_overlap debe ser un entero.")
    if chunk_size <= 0:
        raise ConfiguracionChunkingError(
            f"chunk_size debe ser mayor que 0 (recibido: {chunk_size})."
        )
    if chunk_overlap < 0:
        raise ConfiguracionChunkingError(
            f"chunk_overlap no puede ser negativo (recibido: {chunk_overlap})."
        )
    if chunk_overlap >= chunk_size:
        raise ConfiguracionChunkingError(
            "chunk_overlap debe ser estrictamente menor que chunk_size "
            f"(recibido: chunk_size={chunk_size}, "
            f"chunk_overlap={chunk_overlap})."
        )


def _cortar_en_frontera(fragmento: str, es_ultimo: bool, paso: int) -> str:
    """Recorta ``fragmento`` en la ultima frontera de palabra si es seguro.

    El corte se admite solo cuando su indice es ``>= paso``, es decir, cuando
    cae en la zona que el fragmento siguiente vuelve a cubrir. Asi no puede
    desaparecer ningun texto.
    """
    if es_ultimo:
        return fragmento
    corte = fragmento.rfind(" ")
    if corte >= paso:
        return fragmento[:corte]
    return fragmento


def dividir_en_fragmentos(
    texto: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Divide ``texto`` en fragmentos de ``chunk_size`` con ``chunk_overlap``.

    Devuelve una lista vacia para texto vacio o compuesto solo por espacios.
    Lanza :class:`ConfiguracionChunkingError` si la configuracion es invalida.
    """
    validar_configuracion(chunk_size, chunk_overlap)

    texto = texto.strip()
    if not texto:
        return []

    if len(texto) <= chunk_size:
        return [texto]

    paso = chunk_size - chunk_overlap
    fragmentos: list[str] = []
    inicio = 0
    total = len(texto)

    while inicio < total:
        fin = min(inicio + chunk_size, total)
        es_ultimo = fin >= total
        trozo = _cortar_en_frontera(texto[inicio:fin], es_ultimo, paso)
        trozo = trozo.strip()
        if trozo:
            fragmentos.append(trozo)
        if es_ultimo:
            break
        inicio += paso

    return fragmentos


def metricas_texto(fragmentos: list[str]) -> dict[str, int]:
    """Resumen de un troceado: nº de fragmentos y caracteres totales."""
    return {
        "fragmentos": len(fragmentos),
        "caracteres": sum(len(f) for f in fragmentos),
        "min_fragmento": min((len(f) for f in fragmentos), default=0),
        "max_fragmento": max((len(f) for f in fragmentos), default=0),
    }
