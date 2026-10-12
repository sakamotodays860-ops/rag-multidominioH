-- ---------------------------------------------------------------------------
-- 002 - Tabla `normativa_bancaria` con aislamiento por `coleccion_id`
--
-- Requisitos de la FASE 2:
--   1. La tabla incluye `coleccion_id VARCHAR(50) NOT NULL`.
--   2. Existe un indice B-tree sobre `coleccion_id`.
--   3. `embedding` es del tipo `vector(768)`, compatible con pgvector.
--
-- La dimension 768 debe coincidir con la variable `PGVECTOR_DIMENSION`
-- declarada en `app/core/config.py`. La prueba `tests/test_schema.py`
-- comprueba que ambas siguen siendo coherentes.
--
-- Todo el script es idempotente (`IF NOT EXISTS`).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS normativa_bancaria (
    -- Identificador interno de la fila.
    id            BIGSERIAL PRIMARY KEY,

    -- Dominio logico al que pertenece el fragmento. Es el eje del aislamiento:
    -- ninguna busqueda puede devolver filas de otra coleccion.
    coleccion_id  VARCHAR(50) NOT NULL,

    -- Texto del fragmento ("chunk") que se indexo.
    contenido     TEXT        NOT NULL,

    -- Vector de embedding. DimENSION FIJA en 768: cambiarla exigiria
    -- regenerar todos los embeddings.
    embedding     VECTOR(768) NOT NULL,

    -- Trazabilidad del fragmento (se llena en fases posteriores de ingesta).
    origen_url    TEXT        NULL,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indice B-tree sobre `coleccion_id`.
--
-- Es el indice que hace posible el aislamiento de forma eficiente: la funcion
-- `match_normativa_coleccion` filtra SIEMPRE por esta columna, y este indice
-- permite descartar el resto de colecciones antes de calcular distancias.
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_normativa_bancaria_coleccion_id
    ON normativa_bancaria (coleccion_id);

-- Restriccion de seguridad del dominio: la coleccion nunca puede quedar vacia
-- ni superar los 50 caracteres declarados por el tipo VARCHAR(50).
ALTER TABLE normativa_bancaria
    DROP CONSTRAINT IF EXISTS normativa_bancaria_coleccion_id_no_vacia;

ALTER TABLE normativa_bancaria
    ADD CONSTRAINT normativa_bancaria_coleccion_id_no_vacia
    CHECK (btrim(coleccion_id) <> '');