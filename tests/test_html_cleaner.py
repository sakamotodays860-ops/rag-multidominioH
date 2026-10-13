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


def test_elimina_boilerplate_del_portal():
    html = (
        '<a href="#main">Pasar al contenido principal</a>'
        "<p>Uso de .gob.bo Los portales oficiales del Estado usan este dominio.</p>"
        "<p>Articulo 1. La presente Ley regula el sistema financiero.</p>"
    )
    limpio = limpiar_html(html)
    assert "Pasar al contenido principal" not in limpio
    assert "Uso de .gob.bo" not in limpio
    assert "La presente Ley regula" in limpio


def test_elimina_menu_de_enlaces_consecutivos():
    html = (
        "<ul>"
        '<li><a href="/">Inicio</a></li>'
        '<li><a href="/normativa">Normativa</a></li>'
        '<li><a href="/contacto">Contacto</a></li>'
        "</ul>"
        "<p>Articulo 2. El ambito de aplicacion de la Ley.</p>"
    )
    limpio = limpiar_html(html)
    for item in ("Inicio", "Normativa", "Contacto"):
        assert item not in limpio
    assert "ambito de aplicacion" in limpio


def test_conserva_un_enlace_aislado_corto():
    """Un unico enlace corto no es un menu y se conserva."""
    limpio = limpiar_html('<a href="/x">Ver anexo</a>')
    assert "Ver anexo" in limpio


def test_elimina_aviso_gob_bo_insensible_a_acentos():
    html = (
        "<p>Esto significa que estás conectado a un dominio .gob.bo de manera "
        "segura.</p><p>Articulo 3. Vigencia de la norma.</p>"
    )
    limpio = limpiar_html(html)
    assert "conectado" not in limpio
    assert "Articulo 3. Vigencia" in limpio