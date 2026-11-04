-- Day 6 stage 2: author columns on paper_meta. Re-runnable at any time.
--
-- The papers live in the database called docpulse_embed, not docpulse:
--
--   docker cp scripts/day6_add_authors.sql docpulse-postgres-1:/tmp/day6_add_authors.sql
--   docker cp /tmp/authors.tsv            docpulse-postgres-1:/tmp/authors.tsv
--   dc exec postgres sh -c 'psql -U "$POSTGRES_USER" -d docpulse_embed -f /tmp/day6_add_authors.sql'
--
-- Additive and idempotent: three nullable columns, three indexes, two UPDATEs.
-- Nothing is dropped and the Day-5 fts column is not touched.

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE paper_meta ADD COLUMN IF NOT EXISTS authors text[];

-- Plain columns, not GENERATED ALWAYS: array_to_string() is STABLE, not
-- IMMUTABLE, so PostgreSQL rejects it in a generation expression. Both are
-- filled in the same statement that writes the array, and the loader must keep
-- them in step from now on.
ALTER TABLE paper_meta ADD COLUMN IF NOT EXISTS authors_tsv tsvector;

-- Case-insensitive exact match lives in its own column. users type
-- 'yann lecun'; the display array preserves 'Yann LeCun'. Phrase-matching
-- authors_tsv instead would sometimes match one author's first name next to
-- another author's surname.
ALTER TABLE paper_meta ADD COLUMN IF NOT EXISTS authors_ci text[];

CREATE INDEX IF NOT EXISTS paper_meta_authors_idx
  ON paper_meta USING GIN (authors);
CREATE INDEX IF NOT EXISTS paper_meta_authors_tsv_idx
  ON paper_meta USING GIN (authors_tsv);
CREATE INDEX IF NOT EXISTS paper_meta_authors_ci_idx
  ON paper_meta USING GIN (authors_ci);

-- One row per (paper, position, name). Aggregating here means the TSV carries no
-- array literals, so no quoting or escaping rule can be got wrong on the way in.
CREATE TEMP TABLE stage_authors (
  arxiv_id text NOT NULL,
  ord      int  NOT NULL,
  name     text NOT NULL
);

\copy stage_authors FROM '/tmp/authors.tsv'

\echo 'stage rows:'
SELECT count(*) AS rows, count(DISTINCT arxiv_id) AS papers FROM stage_authors;

-- 'simple', never 'english': stemming turns Harris into harri and mangles
-- hyphenated and accented names into lexemes no document contains.
UPDATE paper_meta AS m
   SET authors     = s.names,
       authors_tsv = to_tsvector('simple', array_to_string(s.names, ' '))
  FROM (
        SELECT arxiv_id, array_agg(name ORDER BY ord) AS names
          FROM stage_authors
         GROUP BY arxiv_id
       ) AS s
 WHERE m.arxiv_id = s.arxiv_id;

-- lower() only: folding accents here would need the unaccent extension, and a
-- name typed without its accent is a different failure to measure separately.
UPDATE paper_meta
   SET authors_ci = ARRAY(SELECT lower(part) FROM unnest(authors) AS part)
 WHERE authors IS NOT NULL
   AND authors_ci IS NULL;

COMMIT;

-- Gates. The four zeros are structural; the two acceptance names must match what
-- the bronze probe reported (LeCun 1, Hinton 1) or the join key is wrong.
\echo 'checks:'
SELECT 'null authors'          AS gate, count(*) AS n FROM paper_meta WHERE authors IS NULL
UNION ALL SELECT 'empty array',            count(*) FROM paper_meta WHERE authors = '{}'
UNION ALL SELECT 'authors but no tsv',     count(*) FROM paper_meta
         WHERE authors IS NOT NULL AND authors_tsv IS NULL
UNION ALL SELECT 'authors but no ci',      count(*) FROM paper_meta
         WHERE authors IS NOT NULL AND authors_ci IS NULL
UNION ALL SELECT 'Yann LeCun (exact)',     count(*) FROM paper_meta
         WHERE authors @> ARRAY['Yann LeCun']
UNION ALL SELECT 'yann lecun (lowercase)', count(*) FROM paper_meta
         WHERE authors_ci @> ARRAY['yann lecun']
UNION ALL SELECT 'lecun (last name)',      count(*) FROM paper_meta
         WHERE authors_tsv @@ plainto_tsquery('simple', 'lecun')
ORDER BY gate;

\echo 'plans:'
EXPLAIN (COSTS OFF) SELECT arxiv_id FROM paper_meta WHERE authors_ci @> ARRAY['yann lecun'];
EXPLAIN (COSTS OFF) SELECT arxiv_id FROM paper_meta WHERE authors_tsv @@ plainto_tsquery('simple', 'lecun');
