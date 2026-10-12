-- ---------------------------------------------------------------------------
-- 003 - Funcion RPC `match_normativa_coleccion`
--
-- Busqueda vectorial sobre `normativa_bancaria` restringida SIEMPRE a una
-- unica coleccion. Esta funcion es el unico punto de entrada previsto para las
-- consultas RAG y es la pieza que garantiza el aislamiento vectorial.
--
-- Firma:
--   p_coleccion_id     varchar(50)      coleccion OBLIGATORIA
--   p_query_embedding  vector(768)      embedding de la consulta
--   p_match_threshold  double precision similitud minima aceptada (0..1)
--   p_match_count      integer          maximo de filas a devolver
--
-- Nota sobre el orden de los parametros:
--   PostgreSQL exige que todo parametro situado despues de uno con valor por
--   defecto tenga tambien valor por defecto. `p_coleccion_id` es el PRIMERO y
--   NO tiene default a proposito: asi la coleccion es de hecho obligatoria y
--   la funcion no puede invocarse sin ella. Un default (por ejemplo NULL)
--   permitiria una llamada incompleta que devolveria cero filas en silencio,
--   que es exactamente el modo de fallo que se quiere evitar.
--
-- Nota sobre el prefijo `p_` en los parametros:
--   En una funcion `LANGUAGE sql` con `RETURNS TABLE`, los nombres de las
--   columnas de salida y los de los parametros conviven en el mismo ambito.
--   Si un parametro se llamara `coleccion_id`, la expresion
--   `WHERE coleccion_id = coleccion_id` seria ambigua y podria compararse
--   consigo misma. El prefijo `p_` (parametro) elimina la ambiguedad de raiz.
--
-- Semantica del umbral:
--   pgvector expone la distancia coseno `<=>`, donde MENOR es mas cercano.
--   El parametro se expresa como SIMILITUD (MAYOR es mas parecido):
--       similitud   = 1 - distancia
--   de modo que el filtro `similitud >= p_match_threshold` es exactamente
--   equivalente a `distancia <= 1 - p_match_threshold`.
--
-- Orden de evaluacion:
--   El filtro por `coleccion_id` aparece en el `WHERE`, es decir ANTES del
--   `ORDER BY` y del `LIMIT`. Por eso nunca se seleccionan filas ajenas a la
--   coleccion solicitada, ni siquiera cuando `p_match_count` es mayor que el
--   numero de filas propias de la coleccion.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION match_normativa_coleccion(
    p_coleccion_id    VARCHAR(50),
    p_query_embedding VECTOR(768),
    p_match_threshold DOUBLE PRECISION DEFAULT 0.5,
    p_match_count     INTEGER           DEFAULT 10
)
RETURNS TABLE (
    id           BIGINT,
    coleccion_id VARCHAR(50),
    contenido    TEXT,
    origen_url   TEXT,
    distancia    DOUBLE PRECISION,
    similitud    DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        nb.id,
        nb.coleccion_id,
        nb.contenido,
        nb.origen_url,
        nb.embedding <=> p_query_embedding AS distancia,
        1 - (nb.embedding <=> p_query_embedding) AS similitud
    FROM normativa_bancaria AS nb
    WHERE nb.coleccion_id = p_coleccion_id          -- aislamiento obligatorio
      AND 1 - (nb.embedding <=> p_query_embedding) >= p_match_threshold
    ORDER BY nb.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- ---------------------------------------------------------------------------
-- Endurecimiento opcional (NO se aplica por defecto).
--
-- Revocar el acceso directo a la tabla obliga a que TODAS las lecturas
-- vectoriales pasen por la funcion, de modo que el filtro por `coleccion_id`
-- deja de depender de la buena fe de quien escribe la consulta. Se deja
-- documentado y NO se ejecuta en la migracion para no alterar permisos sin
-- una decision explicita.
--
--   REVOKE ALL ON normativa_bancaria FROM PUBLIC;
--   REVOKE ALL ON FUNCTION match_normativa_coleccion(VARCHAR, VECTOR, DOUBLE PRECISION, INTEGER) FROM PUBLIC;
--   GRANT EXECUTE ON FUNCTION match_normativa_coleccion(VARCHAR, VECTOR, DOUBLE PRECISION, INTEGER) TO rag_user;
-- ---------------------------------------------------------------------------
