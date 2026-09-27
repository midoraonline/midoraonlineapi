-- Read-only inventory of public tables, views, and columns.
-- Paste this whole file into the Supabase SQL Editor and run it once.
-- It does not create, update, or drop anything, and it does not use temp tables.
--
-- Compare the result to db/migrations (through 046) and supabase_schema.sql.
-- A table or column that is missing here was never migrated (038 was skipped
-- in production before 046 added categories.metadata).
--
-- estimated_rows is pg_stat_user_tables.n_live_tup when Postgres has collected
-- it, otherwise pg_class.reltuples. estimated_non_null uses pg_stats.null_frac
-- from the last ANALYZE. Both are estimates, not exact counts. NULL
-- estimated_non_null means that column has not been analyzed.
-- Run ANALYZE on public first if the estimates look empty or stale.

SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized_view'
        ELSE c.relkind::text
    END AS kind,
    COALESCE(st.n_live_tup, c.reltuples)::bigint AS estimated_rows,
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    a.attnotnull AS not_null,
    pg_get_expr(d.adbin, d.adrelid) AS column_default,
    CASE
        WHEN ps.null_frac IS NULL THEN NULL
        ELSE round(
            (1.0 - ps.null_frac) * GREATEST(COALESCE(st.n_live_tup, c.reltuples), 0)
        )::bigint
    END AS estimated_non_null
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a
    ON a.attrelid = c.oid
   AND a.attnum > 0
   AND NOT a.attisdropped
LEFT JOIN pg_catalog.pg_attrdef d
    ON d.adrelid = a.attrelid
   AND d.adnum = a.attnum
LEFT JOIN pg_catalog.pg_stat_user_tables st
    ON st.relid = c.oid
LEFT JOIN pg_catalog.pg_stats ps
    ON ps.schemaname = n.nspname
   AND ps.tablename = c.relname
   AND ps.attname = a.attname
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'v', 'm')
ORDER BY c.relname, a.attnum;
