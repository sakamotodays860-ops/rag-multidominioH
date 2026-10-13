"""Constantes y constructores de vectores para las pruebas de la FASE 2.

Este modulo es el origen unico de verdad de los datos de prueba dentro del
suite. Las pruebas lo importan en lugar de duplicar las colecciones.

Las pruebas NO insertan datos: solo leen y exigen que estos registros existan
con el tamano documentado.
"""

from app.database.repository import VECTOR_DIMENSION

COLECCION_FINANCIERO = "financiero_bolivia_2026"
COLECCION_TRIBUTARIO = "tributario_bolivia_2026"

# Tamano de los datos de demostracion cargados por seeds/aislamiento_demo.sql.
# Es el numero de filas SEMILLA por coleccion de demostracion. Desde la FASE 6
# pueden coexistir documentos reales (origen_url http(s)://) en esas mismas
# colecciones, por lo que el total de una coleccion es >= FRAGMENTOS_ESPERADOS.
FRAGMENTOS_ESPERADOS = 4
TOTAL_ESPERADO = FRAGMENTOS_ESPERADOS * 2

# Las filas sinteticas de demostracion se identifican por este prefijo.
PREFIJO_SEMILLA = "seed://"


def vector_de_dominio(indice_eje: int) -> list[float]:
    """Vector concentrado en la componente `indice_eje`.

    Replica la distribucion usada por `seeds/aislamiento_demo.sql`, donde la
    distancia coseno dentro de un mismo dominio es cercana a 0 y entre dominios
    cercana a 1.0.
    """
    if not 0 <= indice_eje < VECTOR_DIMENSION:
        raise ValueError("indice_eje fuera de rango")
    vector = [0.01] * VECTOR_DIMENSION
    vector[indice_eje] = 1.0
    return vector


def vector_financiero() -> list[float]:
    """Consulta orientada al dominio financiero (componente 0)."""
    return vector_de_dominio(0)


def vector_tributario() -> list[float]:
    """Consulta orientada al dominio tributario (componente 1)."""
    return vector_de_dominio(1)


def construir_pdf(texto: str) -> bytes:
    """Construye un PDF minimo de una pagina con texto extraible.

    Se genera a mano (sin dependencias de autoría) para que las pruebas de
    extraccion de PDF sean deterministas y no dependan de la red. ``pypdf``
    reconoce el resultado y extrae ``texto`` de la pagina.
    """
    return construir_pdf_paginas([texto])


def construir_pdf_paginas(textos: list[str]) -> bytes:
    """Construye un PDF de ``len(textos)`` paginas (una por cada texto).

    La primera pagina usa el objeto de fuente ``5 0 R``; las paginas siguientes
    reutilizan la misma fuente, de modo que el PDF es valido y cada pagina
    aporta su propio texto extraible.
    """
    if not textos:
        raise ValueError("se requiere al menos una pagina")

    objetos: list[bytes] = []
    # 1: catalogo, 2: arbol de paginas, 3..(2+n): paginas, luego contenidos y fuente.
    num_paginas = len(textos)
    ids_paginas = [3 + indice for indice in range(num_paginas)]
    id_fuente = 3 + num_paginas + num_paginas  # despues de las paginas y contenidos
    ids_contenidos = [3 + num_paginas + indice for indice in range(num_paginas)]

    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in ids_paginas)
    objetos.append(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(num_paginas).encode() + b" >>"
    )
    for indice, pid in enumerate(ids_paginas):
        objetos.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 "
            + str(id_fuente).encode()
            + b" 0 R >> >> /Contents "
            + str(ids_contenidos[indice]).encode()
            + b" 0 R >>"
        )
    for texto in textos:
        stream = ("BT /F1 12 Tf 72 720 Td (" + texto + ") Tj ET").encode("latin-1")
        objetos.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    salida = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for indice, objeto in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += str(indice).encode() + b" 0 obj\n" + objeto + b"\nendobj\n"
    xref = len(salida)
    salida += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n"
    salida += b"0000000000 65535 f \n"
    for desplazamiento in offsets:
        salida += ("%010d 00000 n \n" % desplazamiento).encode()
    salida += (
        b"trailer\n<< /Size "
        + str(len(objetos) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF"
    )
    return bytes(salida)