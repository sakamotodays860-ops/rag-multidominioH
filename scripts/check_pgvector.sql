-- ---------------------------------------------------------------------------
-- Inicialización y comprobación de pgvector
-- ---------------------------------------------------------------------------

-- Habilitar pgvector en la base de datos.
CREATE EXTENSION IF NOT EXISTS vector;

-- Comprobar que quedó instalada.
SELECT extname AS extension,
       extversion AS version,
       'instalada' AS estado
FROM pg_extension
WHERE extname = 'vector';

-- Comprobar versión de PostgreSQL.
SELECT version();
