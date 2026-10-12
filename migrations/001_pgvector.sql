-- ---------------------------------------------------------------------------
-- 001 - Extension pgvector
--
-- Se asegura de que la extension `vector` este disponible en la base de
-- datos. Es idempotente: se puede aplicar varias veces sin efecto adicional.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;