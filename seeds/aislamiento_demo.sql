-- ---------------------------------------------------------------------------
-- Datos de prueba para demostrar el aislamiento entre colecciones.
--
-- Crea dos colecciones claramente separadas en el espacio vectorial:
--
--   financiero_bolivia_2026 -> concentrada en la componente 1
--   tributario_bolivia_2026 -> concentrada en la componente 2
--
-- Cada coleccion tiene 4 fragmentos. La distancia coseno entre un vector
-- "financiero" y un vector "tributario" es aproximadamente 1.0, mientras que
-- dentro de una misma coleccion es inferior a 0.05. Por eso una consulta
-- orientada a un dominio NUNCA debe devolver filas del otro.
--
-- Es idempotente: si la coleccion ya tiene filas, no inserta nada. No borra
-- datos existentes.
-- ---------------------------------------------------------------------------

-- --- financial ------------------------------------------------------------
WITH vectores AS (
    SELECT
        n,
        array_agg(
            CASE
                WHEN s = 1 THEN 1.0 - 0.02 * n
                WHEN s = 2 THEN 0.02 * n
                ELSE 0.01 + 0.0001 * n
            END
            ORDER BY s
        )::vector AS embedding
    FROM generate_series(1, 4) AS n
    CROSS JOIN generate_series(1, 768) AS s
    GROUP BY n
)
INSERT INTO normativa_bancaria (coleccion_id, contenido, origen_url, embedding)
SELECT
    'financiero_bolivia_2026',
    'Normativa financiera Bolivia 2026 - resolucion ' || n,
    'seed://aislamiento/financiero_bolivia_2026/' || n,
    embedding
FROM vectores
WHERE NOT EXISTS (
    SELECT 1 FROM normativa_bancaria AS nb
    WHERE nb.coleccion_id = 'financiero_bolivia_2026'
);

-- --- tributario -----------------------------------------------------------
WITH vectores AS (
    SELECT
        n,
        array_agg(
            CASE
                WHEN s = 1 THEN 0.02 * n
                WHEN s = 2 THEN 1.0 - 0.02 * n
                ELSE 0.01 + 0.0001 * n
            END
            ORDER BY s
        )::vector AS embedding
    FROM generate_series(1, 4) AS n
    CROSS JOIN generate_series(1, 768) AS s
    GROUP BY n
)
INSERT INTO normativa_bancaria (coleccion_id, contenido, origen_url, embedding)
SELECT
    'tributario_bolivia_2026',
    'Normativa tributaria Bolivia 2026 - resolucion ' || n,
    'seed://aislamiento/tributario_bolivia_2026/' || n,
    embedding
FROM vectores
WHERE NOT EXISTS (
    SELECT 1 FROM normativa_bancaria AS nb
    WHERE nb.coleccion_id = 'tributario_bolivia_2026'
);

-- --- Comprobacion ---------------------------------------------------------
SELECT coleccion_id, count(*) AS fragmentos
FROM normativa_bancaria
GROUP BY coleccion_id
ORDER BY coleccion_id;