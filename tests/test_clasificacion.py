"""Pruebas de la clasificacion de dominio (fase H).

Deterministas y offline: no usan Gemini ni base de datos. Verifican que el
enrutado del chat es seguro: dominios claros se resuelven y las preguntas
ambiguas o ajenas NO eligen coleccion.
"""

from app.services.clasificacion import (
    DOMINIO_FINANCIERO,
    DOMINIO_TRIBUTARIO,
    clasificar,
)


def test_pregunta_financiera_clara():
    resultado = clasificar("Cual es la tasa de interes del sistema financiero?")
    assert resultado.dominio == DOMINIO_FINANCIERO
    assert resultado.ambigua is False


def test_pregunta_tributaria_clara():
    resultado = clasificar("Como se declara el IVA de las facturas?")
    assert resultado.dominio == DOMINIO_TRIBUTARIO
    assert resultado.ambigua is False


def test_pregunta_con_acentos_y_mayusculas():
    resultado = clasificar("\u00bfCu\u00e1l es la regulaci\u00f3n de ASFI?")
    assert resultado.dominio == DOMINIO_FINANCIERO


def test_pregunta_ambigua_pide_aclaracion():
    resultado = clasificar("Cual es la tasa del impuesto?")
    assert resultado.dominio is None
    assert resultado.ambigua is True


def test_pregunta_fuera_de_dominio():
    resultado = clasificar("Como esta el clima hoy en La Paz?")
    assert resultado.dominio is None
    assert resultado.ambigua is False
    assert "financieros ni tributarios" in resultado.motivo


def test_pregunta_vacia_no_se_clasifica():
    resultado = clasificar("   ")
    assert resultado.dominio is None
    assert resultado.ambigua is False


def test_banco_central_es_financiero():
    assert clasificar("Que hace el Banco Central de Bolivia?").dominio == DOMINIO_FINANCIERO


def test_aduanas_es_tributario():
    assert clasificar("Aranceles de importacion de la Aduana").dominio == DOMINIO_TRIBUTARIO


# --- términos de la normativa realmente ingerida (RD BCB 138/2026) -----------


def test_posicion_de_cambios_es_financiero():
    resultado = clasificar("\u00bfQu\u00e9 establece el Reglamento de Posici\u00f3n de Cambios?")
    assert resultado.dominio == DOMINIO_FINANCIERO
    assert resultado.ambigua is False


def test_encaje_legal_es_financiero():
    assert clasificar("\u00bfQu\u00e9 es el encaje legal?").dominio == DOMINIO_FINANCIERO


def test_operaciones_cambiarias_es_financiero():
    resultado = clasificar("\u00bfCu\u00e1les son las operaciones cambiarias permitidas?")
    assert resultado.dominio == DOMINIO_FINANCIERO


def test_tipo_de_cambio_es_financiero():
    assert clasificar("Como se determina el tipo de cambio oficial?").dominio == DOMINIO_FINANCIERO


def test_plural_financieras_es_financiero():
    assert clasificar("Obligaciones de las entidades financieras").dominio == DOMINIO_FINANCIERO
