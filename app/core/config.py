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
    http_max_bytes: int = 2_000_000

    # --- Chunking (FASE 3) ---
    chunk_size_por_defecto: int = 1200
    chunk_overlap_por_defecto: int = 200
    urls_por_peticion_max: int = 50

    # --- Gemini / embeddings (FASE 3) ---
    # NO se guarda ninguna clave aqui: solo su nombre de variable de entorno.
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = "gemini-2.0-flash"
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


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia unica de Settings (cacheada)."""
    return Settings()


settings = get_settings()