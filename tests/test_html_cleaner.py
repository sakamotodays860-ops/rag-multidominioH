"""Pruebas de limpieza HTML (FASE 3, requisito 3).

Son pruebas puras sobre texto: no usan red, base de datos ni dependencias
externas, igual que el propio `app.utils.html_cleaner`.
"""

from app.utils.html_cleaner import limpiar_html


def test_elimina_script_style_y_noscript():
    html = (
        "<html><head>"
        "<title>Norma</title>"
        "<style>.oculto{display:none}</style>"
        "<script>alert('x')</script>"
        "</head><body><p>Texto que si importa</p>"
        "<noscript>Necesitas JS</noscript></body></html>"
    )
    limpio = limpiar_html(html)
    assert "alert" not in limpio
    assert ".oculto" not in limpio
    assert "Necesitas JS" not in limpio
    assert "Texto que si importa" in limpio
    assert "Norma" in limpio


def test_elimina_navegacion_cabecera_pie_y_aside():
    html = (
        "<nav><a href='/'>Inicio</a><a href='/contacto'>Contacto</a></nav>"
        "<header><h1>Cabecera</h1></header>"
        "<main><p>Contenido principal</p></main>"
        "<aside>Publicidad</aside>"
        "<footer>Pie de pagina</footer>"
    )
    limpio = limpiar_html(html)
    for fragmento in ("Inicio", "Contacto", "Cabecera", "Publicidad", "Pie de pagina"):
        assert fragmento not in limpio
    assert "Contenido principal" in limpio


def test_elimina_comentarios():
    html = "<!-- comentario confidencial --><p>Hola</p>"
    limpio = limpiar_html(html)
    assert "comentario" not in limpio
    assert "Hola" in limpio


def test_normaliza_espacios_y_parrafos():
    html = "<div>linea 1\n\n   linea   2</div>"
    limpio = limpiar_html(html)
    assert "linea 1" in limpio
    assert "  linea" not in limpio


def test_separa_parrafos_para_el_chunking():
    html = "<p>Primer parrafo.</p><p>Segundo parrafo.</p>"
    limpio = limpiar_html(html)
    assert "\n\n" in limpio
    assert limpio.index("Primer") < limpio.index("Segundo")


def test_cadena_vacia_o_solo_etiquetas():
    assert limpiar_html("") == ""
    assert limpiar_html("<script>xx</script><style>yy</style>") == ""


def test_html_mal_formado_no_lanza():
    # Etiquetas sin cerrar y etiquetas raras no deben tumbar el parser.
    limpio = limpiar_html("<p>hola<div>mundo</p></div>" + "<")
    assert "hola" in limpio
    assert "mundo" in limpio


def test_ok_url_relative_no_se_emite():
    html = '<a href="/ruta_relativa">enlace</a><p>si</p>'
    limpio = limpiar_html(html)
    assert "enlace" in limpio
    assert "/ruta_relativa" not in limpio