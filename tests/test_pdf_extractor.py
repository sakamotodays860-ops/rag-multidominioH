"""Pruebas de extraccion de PDF (fase C).

Sin red: se construye un PDF minimo con texto extraible (``helpers.construir_pdf``)
y se comprueban deteccion por firma/MIME, extraccion pagina a pagina y los
modos de fallo (PDF corrupto, PDF sin texto, binario no soportado).

Requiere ``pypdf`` (dependencia declarada en ``requirements.txt``); si no esta
instalado, las pruebas que lo necesitan se saltan de forma explicita en lugar
de fingir un resultado.
"""

import pytest

from helpers import construir_pdf, construir_pdf_paginas

from app.services.pdf_extractor import (
    PdfInvalido,
    PdfRequiereDependencia,
    PdfSinTexto,
    detectar_pdf,
    extraer_paginas_pdf,
    extraer_texto_pdf,
    parece_binario,
)

pypdf = pytest.importorskip("pypdf")


def test_pdf_valido_extrae_texto_y_paginas():
    datos = construir_pdf("Norma ASFI de prueba 2026")
    texto, paginas = extraer_texto_pdf(datos)
    assert "Norma ASFI de prueba 2026" in texto
    assert paginas == 1
    # Un PDF de una sola pagina no lleva marcador.
    assert "[página 1]" not in texto


def test_pdf_multipagina_conserva_marcadores_de_pagina():
    datos = construir_pdf_paginas(["Contenido de la pagina uno", "Contenido de la pagina dos"])
    texto, paginas = extraer_texto_pdf(datos)
    assert paginas == 2
    assert "[página 1]" in texto
    assert "[página 2]" in texto
    assert texto.index("pagina uno") < texto.index("pagina dos")


def test_extraer_paginas_devuelve_numero_y_texto():
    datos = construir_pdf_paginas(["Alfa", "Beta", "Gamma"])
    paginas, total = extraer_paginas_pdf(datos)
    assert total == 3
    assert [numero for numero, _ in paginas] == [1, 2, 3]
    assert "Beta" in paginas[1][1]


def test_pdf_cifrado_sin_cryptography_se_reporta(monkeypatch):
    """Sin backend `cryptography`, pypdf lanza DependencyError -> mensaje claro."""
    from pypdf.errors import DependencyError

    class _ReaderCifrado:
        def __init__(self, *args, **kwargs) -> None:
            self.is_encrypted = True

        def decrypt(self, *args, **kwargs) -> int:
            raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(pypdf, "PdfReader", _ReaderCifrado)
    with pytest.raises(PdfRequiereDependencia) as error:
        extraer_texto_pdf(b"%PDF-1.7 documento cifrado")
    assert "cryptography" in str(error.value)


def test_detectar_por_firma_binaria():
    datos = construir_pdf("x")
    assert detectar_pdf(datos, content_type=None) is True


def test_detectar_por_firma_con_espacios_iniciales():
    datos = b"   \n" + construir_pdf("x")
    assert detectar_pdf(datos, content_type=None) is True


def test_detectar_por_content_type_pdf():
    assert detectar_pdf(b"no-magic", content_type="application/pdf; charset=x") is True


def test_detectar_falso_para_html():
    assert detectar_pdf(b"<html></html>", content_type="text/html") is False


def test_mime_incorrecto_pero_firma_pdf_se_detecta_como_pdf():
    """Servidores que entregan PDF con Content-Type text/html."""
    datos = construir_pdf("Ley 2026")
    assert detectar_pdf(datos, content_type="text/html") is True


def test_pdf_corrupto_lanza_invalido():
    with pytest.raises(PdfInvalido):
        extraer_texto_pdf(b"%PDF-1.4 esto no es un PDF real")


def test_pdf_sin_texto_lanza_sin_texto():
    """PDF con paginas pero sin texto (escaneado) -> PdfSinTexto."""
    from pypdf import PdfWriter

    import io

    escritor = PdfWriter()
    escritor.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    escritor.write(buffer)
    with pytest.raises(PdfSinTexto):
        extraer_texto_pdf(buffer.getvalue())


def test_parece_binario_por_nul():
    assert parece_binario(b"\x00\x01\x02", content_type=None) is True


def test_parece_binario_por_mime():
    assert parece_binario(b"PK\x03\x04", content_type="application/zip") is True


def test_html_no_parece_binario():
    assert parece_binario(b"<html><body>hola</body></html>", "text/html") is False
