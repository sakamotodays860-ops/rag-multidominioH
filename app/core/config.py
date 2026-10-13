"""Configuracion de la aplicacion, cargada desde variables de entorno."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion tipada del proyecto.

    Los valores se leen de variables de entorno o de un archivo `.env`.
    Las claves sensibles nunca se exponen en la documentacion.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Aplicacion ---
    app_name: str = "Pipeline RAG Multi-Dominio"
    app_version: str = "0.1.0"
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True
    log_level: str = "INFO"

    # --- Base de datos ---
    database_url: str | None = Field(default=None)
    postgres_db: str = "rag_multidominio"
    postgres_user: str = "rag_user"
    postgres_password: str = ""
    postgres_port: int = 5432

    # --- pgvector ---
    pgvector_dimension: int = 768

    # --- Descarga web (FASE 3) ---
    http_timeout_s: float = 15.0
    http_max_concurrencia: int = 16
    http_user_agent: str = "rag-multidominio/0.1 (+ingesta)"
    # Limite de tamano por documento. Se subio de 2 MB a 20 MB en FASE 3: los
    # PDF normativos oficiales superan los 2 MB (Ley 2492 ~6.7 MB, Ley 843
    # ~9.8 MB, Ley 1990 ~4.1 MB) y con el limite antiguo se rechazaban.
    http_max_bytes: int = 20_000_000

    # --- Chunking (FASE 3) ---
    chunk_size_por_defecto: int = 1200
    chunk_overlap_por_defecto: int = 200
    urls_por_peticion_max: int = 50

    # --- Gemini / embeddings (FASE 3) ---
    # NO se guarda ninguna clave aqui: solo su nombre de variable de entorno.
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = "gemini-3.8-flash"
    # Modelo alternativo OPCIONAL de generacion. Vacio = deshabilitado (no
    # cambia el comportamiento). Si se define, y el modelo principal agota sus
    # reintentos con un 5xx (503 UNAVAILABLE), se intenta una vez con este
    # modelo, segun la recomendacion oficial de Gemini ante 503.
    gemini_model_fallback: str | None = Field(default=None)
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimension: int = 768
    # `batchEmbedContents` admite un maximo de 100 solicitudes por llamada.
    gemini_batch_size: int = 100
    # Reintentos ante 429 / RESOURCE_EXHAUSTED. Acotados: nunca bucle infinito.
    gemini_max_reintentos: int = 5
    gemini_backoff_base_s: float = 1.0
    gemini_backoff_max_s: float = 30.0
    gemini_timeout_s: float = 60.0

    # --- Consulta RAG (FASE 4) ---
    # `gemini_model` (GEMINI_MODEL) es el modelo de GENERACION ya existente.
    # Temperatura estricta del LLM: la respuesta debe ser determinista.
    gemini_temperatura: float = 0.0
    # `gemini_embedding_model`/`dimension` (FASE 3) se reutilizan para embeddir
    # la pregunta; aqui solo se fijan los parametros de la consulta.
    top_k_por_defecto: int = 4
    similarity_threshold_por_defecto: float = 0.5
    top_k_max: int = 50
    consulta_pregunta_max_caracteres: int = 2000

    # --- Recuperacion hibrida (mejora de recuperacion) ---
    # La busqueda VECTORIAL se conserva SIEMPRE; la busqueda LEXICA (PostgreSQL
    # nativo) la COMPLEMENTA. Con `True` la capa HTTP combina ambas por RRF.
    recuperacion_lexica: bool = True
    # Si el embedding de la pregunta falla (p.ej. cuota 429 agotada), recurrir a
    # la busqueda lexica en lugar de abortar la consulta (que no requiere
    # embeddings). Con `False` un fallo de embeddings sigue abortando.
    recuperacion_lexica_sin_embedding: bool = True
    # Parametro k de Reciprocal Rank Fusion. Mayor k => menor peso del primer
    # puesto. 60 es el valor habitual y estable.
    hibrido_rrf_k: int = 60

    # --- Chat conversacional (fase H) ---
    # Mapeo explicito y verificable dominio -> coleccion indexada. Se puede
    # sobreescribir por entorno si se ingiere normativa real en otra coleccion.
    coleccion_financiera: str = "financiero_bolivia_2026"
    coleccion_tributaria: str = "tributario_bolivia_2026"


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia unica de Settings (cacheada)."""
    return Settings()


settings = get_settings()