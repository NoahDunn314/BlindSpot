-- Entity resolution and relationship derivation, expressed as SQL views.
-- These rules mirror blindspot/resolution.py and blindspot/discovery.py.
-- Run scripts/check_parity.py to confirm that the two implementations agree.

USE SCHEMA BLINDSPOT.CORE;

-- Mirrors blindspot.resolution.normalize_name: lowercase, non-alphanumerics to spaces,
-- trim, then remove one trailing legal-form suffix.
CREATE OR REPLACE FUNCTION NORMALIZE_NAME(S VARCHAR)
RETURNS VARCHAR
AS
$$
    TRIM(REGEXP_REPLACE(
        TRIM(REGEXP_REPLACE(LOWER(S), '[^a-z0-9]+', ' ')),
        '(^| )(inc|llc|ltd|co|corp|corporation|company|limited)$', ''))
$$;

-- Reference entities, deduplicated by ENTITY_ID (first record wins, as in Python).
CREATE OR REPLACE VIEW V_ENTITIES AS
SELECT
    PAYLOAD:entity_id::STRING                 AS ENTITY_ID,
    PAYLOAD:kind::STRING                      AS ENTITY_KIND,
    PAYLOAD:name::STRING                      AS NAME,
    NORMALIZE_NAME(PAYLOAD:name::STRING)      AS NORMALIZED_NAME,
    PAYLOAD:registry_id::STRING               AS REGISTRY_ID,
    PAYLOAD:state::STRING                     AS STATE_CODE,
    RECORD_ID                                 AS SOURCE_RECORD_ID
FROM SOURCE_RECORDS
WHERE RECORD_TYPE = 'entity_reference'
QUALIFY ROW_NUMBER() OVER (PARTITION BY PAYLOAD:entity_id::STRING ORDER BY RECORD_ID) = 1;

-- One row per mention. Each relationship record has a subject and an object.
CREATE OR REPLACE VIEW V_MENTIONS AS
SELECT
    r.RECORD_ID,
    r.SOURCE_ID,
    r.OBSERVED_AT,
    r.SOURCE_URL,
    r.PAYLOAD:relation::STRING                             AS RELATION,
    COALESCE(r.PAYLOAD:status::STRING, 'documented')       AS STATUS,
    f.KEY                                                  AS ROLE,
    f.VALUE:name::STRING                                   AS MENTION_NAME,
    NORMALIZE_NAME(f.VALUE:name::STRING)                   AS NORM_NAME,
    -- Empty strings count as missing, as in Python where "" is falsy.
    NULLIF(f.VALUE:registry_id::STRING, '')                AS REGISTRY_ID,
    NULLIF(f.VALUE:state::STRING, '')                      AS STATE_CODE
FROM SOURCE_RECORDS r,
     LATERAL FLATTEN(INPUT => OBJECT_CONSTRUCT('subject', r.PAYLOAD:subject, 'object', r.PAYLOAD:object)) f
WHERE r.RECORD_TYPE = 'relationship';

-- Resolve each mention. Each branch applies one rule. A mention with a registry ID is
-- never matched by name. A mention with a state needs the state to match. A mention with
-- neither must match exactly one entity by name. Anything with two or more candidates is ambiguous.
CREATE OR REPLACE VIEW V_RESOLVED_MENTIONS AS
WITH CANDIDATES AS (
    SELECT m.RECORD_ID, m.ROLE, e.ENTITY_ID, 'registry_id' AS MATCH_METHOD
    FROM V_MENTIONS m
    JOIN ENTITIES e ON m.REGISTRY_ID IS NOT NULL AND e.REGISTRY_ID = m.REGISTRY_ID
    UNION ALL
    SELECT m.RECORD_ID, m.ROLE, e.ENTITY_ID, 'name_and_state'
    FROM V_MENTIONS m
    JOIN ENTITIES e
      ON m.REGISTRY_ID IS NULL AND m.STATE_CODE IS NOT NULL
     AND e.NORMALIZED_NAME = m.NORM_NAME AND e.STATE_CODE = m.STATE_CODE
    UNION ALL
    SELECT m.RECORD_ID, m.ROLE, e.ENTITY_ID, 'name_only'
    FROM V_MENTIONS m
    JOIN ENTITIES e
      ON m.REGISTRY_ID IS NULL AND m.STATE_CODE IS NULL AND e.NORMALIZED_NAME = m.NORM_NAME
)
SELECT
    m.RECORD_ID,
    m.ROLE,
    m.MENTION_NAME,
    m.RELATION,
    m.STATUS,
    m.SOURCE_ID,
    m.OBSERVED_AT,
    m.SOURCE_URL,
    CASE WHEN COUNT(c.ENTITY_ID) = 1 THEN MIN(c.ENTITY_ID) END AS ENTITY_ID,
    MIN(c.MATCH_METHOD)                                        AS MATCH_METHOD,
    COUNT(c.ENTITY_ID)                                         AS CANDIDATE_COUNT
FROM V_MENTIONS m
LEFT JOIN CANDIDATES c ON c.RECORD_ID = m.RECORD_ID AND c.ROLE = m.ROLE
GROUP BY m.RECORD_ID, m.ROLE, m.MENTION_NAME, m.RELATION, m.STATUS, m.SOURCE_ID, m.OBSERVED_AT, m.SOURCE_URL;

-- Edges between resolved entities. Both sides must resolve. The weaker match method wins.
-- Co-occurrence edges are included here and flagged by STATUS. Discovery excludes them.
CREATE OR REPLACE VIEW V_RELATIONSHIPS AS
SELECT
    s.RECORD_ID                     AS RELATIONSHIP_ID,
    s.ENTITY_ID                     AS FROM_ENTITY_ID,
    o.ENTITY_ID                     AS TO_ENTITY_ID,
    s.RELATION                      AS RELATION_TYPE,
    s.STATUS,
    s.SOURCE_ID,
    s.RECORD_ID                     AS SOURCE_RECORD_ID,
    s.SOURCE_URL,
    s.OBSERVED_AT,
    CASE
        WHEN 'name_only' IN (s.MATCH_METHOD, o.MATCH_METHOD) THEN 'name_only'
        WHEN 'name_and_state' IN (s.MATCH_METHOD, o.MATCH_METHOD) THEN 'name_and_state'
        ELSE 'registry_id'
    END                             AS MATCH_METHOD,
    'subject: ' || s.MATCH_METHOD || '; object: ' || o.MATCH_METHOD AS MATCH_RATIONALE
FROM V_RESOLVED_MENTIONS s
JOIN V_RESOLVED_MENTIONS o ON o.RECORD_ID = s.RECORD_ID AND o.ROLE = 'object'
WHERE s.ROLE = 'subject'
  AND s.ENTITY_ID IS NOT NULL
  AND o.ENTITY_ID IS NOT NULL;

-- Mentions that could not be linked to exactly one entity.
CREATE OR REPLACE VIEW V_REJECTED_MENTIONS AS
SELECT
    RECORD_ID,
    MENTION_NAME,
    CASE WHEN CANDIDATE_COUNT = 0 THEN 'no matching reference entity' ELSE 'ambiguous match' END AS REASON
FROM V_RESOLVED_MENTIONS
WHERE ENTITY_ID IS NULL;
