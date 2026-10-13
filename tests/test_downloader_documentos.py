"""Pruebas de descarga/extraccion de documentos (fase C).

Usan ``httpx.MockTransport`` (via el parametro ``transporte`` de
``descargar_y_limpiar``): nunca hay red real. Cubren:

* HTML con titulo y limpieza;
* PDF por Content-Type y por firma binaria (MIME incorrecto);
* PDF corrupto / sin texto;
* contenido binario no soportado;
* HTTP no exitoso, contenido excesivo y redirecciones.
"""

import httpx
import pytest

from helpers import construir_pdf

from app.services.downloader import descargar_y_limpiar

pypdf = pytest.importorskip("pypdf")

_KW = dict(concurrency=2, timeout_s=5.0, user_agent="test", max_bytes=1_000_000)


def _cliente(respuestas: dict) -> httpx.MockTransport:
    """Transporte que responde segun el path de la URL solicitada."""

    def manejador(request: httpx.Request) -> httpx.Response:
        respuesta = respuestas.get(request.url.path)
        if respuesta is None:
            return httpx.Response(404, text="no encontrado")
        if callable(respuesta):
            return respuesta(request)
        return respuesta

    return httpx.MockTransport(manejador)


async def _descargar(urls, respuestas, **overrides):
    kw = {**_KW, **overrides}
    return await descargar_y_limpiar(
        urls, transporte=_cliente(respuestas), **kw
    )


async def test_html_se_limpia_y_conserva_titulo():
    html = (
        "<html><head><title>Resolucion ASFI 001/2026</title></head>"
        "<body><nav>menu</nav><h1>Articulo 1</h1><p>Contenido normativo.</p>"
        "<script>var x=1;</script></body></html>"
    )
    resultado = await _descargar(
        ["https://x.org/ley.html"],
        {"/ley.html": httpx.Response(200, text=html, headers={"content-type": "text/html"})},
    )
    assert resultado.errores == []
    assert len(resultado.textos) == 1
    _, texto = resultado.textos[0]
    assert "Contenido normativo." in texto
    assert "menu" not in texto and "var x" not in texto
    doc = resultado.documentos[0]
    assert doc.titulo == "Resolucion ASFI 001/2026"
    assert doc.mime == "text/html"
    assert doc.paginas is None


async def test_pdf_por_content_type_extrae_texto():
    pdf = construir_pdf("Texto oficial de la ley")
    resultado = await _descargar(
        ["https://x.org/ley.pdf"],
        {
            "/ley.pdf": httpx.Response(
                200, content=pdf, headers={"content-type": "application/pdf"}
            )
        },
    )
    assert resultado.errores == []
    _, texto = resultado.textos[0]
    assert "Texto oficial de la ley" in texto
    doc = resultado.documentos[0]
    assert doc.mime == "application/pdf"
    assert doc.paginas == 1


async def test_pdf_por_firma_con_mime_incorrecto():
    """PDF servido como text/html: la firma manda."""
    pdf = construir_pdf("Norma servida con mime malo")
    resultado = await _descargar(
        ["https://x.org/raro"],
        {
            "/raro": httpx.Response(
                200, content=pdf, headers={"content-type": "text/html"}
            )
        },
    )
    assert resultado.errores == []
    _, texto = resultado.textos[0]
    assert "Norma servida con mime malo" in texto


async def test_pdf_corrupto_se_reporta_sin_insertar():
    resultado = await _descargar(
        ["https://x.org/bad.pdf"],
        {
            "/bad.pdf": httpx.Response(
                200,
                content=b"%PDF-1.4 basura ilegible",
                headers={"content-type": "application/pdf"},
            )
        },
    )
    assert resultado.textos == []
    assert resultado.documentos == []
    assert len(resultado.errores) == 1
    assert "PDF" in resultado.errores[0].motivo


async def test_pdf_sin_texto_se_reporta():
    from pypdf import PdfWriter

    import io

    escritor = PdfWriter()
    escritor.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    escritor.write(buffer)
    resultado = await _descargar(
        ["https://x.org/scan.pdf"],
        {
            "/scan.pdf": httpx.Response(
                200,
                content=buffer.getvalue(),
                headers={"content-type": "application/pdf"},
            )
        },
    )
    assert resultado.textos == []
    assert "texto extraible" in resultado.errores[0].motivo


async def test_binario_no_soportado_se_rechaza():
    resultado = await _descargar(
        ["https://x.org/img.bin"],
        {
            "/img.bin": httpx.Response(
                200,
                content=b"\x00\x01\x02\x03binary",
                headers={"content-type": "application/octet-stream"},
            )
        },
    )
    assert resultado.textos == []
    assert "binario" in resultado.errores[0].motivo


async def test_http_no_exitoso_se_reporta():
    resultado = await _descargar(
        ["https://x.org/e"],
        {"/e": httpx.Response(500, text="error")},
    )
    assert resultado.textos == []
    assert "HTTP 500" in resultado.errores[0].motivo


async def test_contenido_excesivo_se_reporta():
    grande = b"a" * 5000
    resultado = await _descargar(
        ["https://x.org/g"],
        {"/g": httpx.Response(200, content=grande, headers={"content-type": "text/html"})},
        max_bytes=100,
    )
    assert resultado.textos == []
    assert "excede" in resultado.errores[0].motivo


async def test_redireccion_se_sigue():
    def redirige(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://x.org/b"})
        return httpx.Response(200, text="<p>final</p>", headers={"content-type": "text/html"})

    resultado = await descargar_y_limpiar(
        ["https://x.org/a"], transporte=httpx.MockTransport(redirige), **_KW
    )
    assert resultado.errores == []
    assert "final" in resultado.textos[0][1]


async def test_url_invalida_se_reporta_sin_red():
    resultado = await descargar_y_limpiar(["ftp://x.org/a"], transporte=httpx.MockTransport(lambda r: httpx.Response(200)), **_KW)
    assert resultado.textos == []
    assert resultado.errores[0].motivo == "URL invalida"


async def test_documentos_duplicados_se_deduplican():
    html = "<p>Contenido normativo identico publicado en dos rutas.</p>"
    resultado = await _descargar(
        ["https://x.org/a", "https://x.org/b"],
        {
            "/a": httpx.Response(200, text=html, headers={"content-type": "text/html"}),
            "/b": httpx.Response(200, text=html, headers={"content-type": "text/html"}),
        },
    )
    assert resultado.errores == []
    assert len(resultado.documentos) == 1
    assert len(resultado.duplicados) == 1
