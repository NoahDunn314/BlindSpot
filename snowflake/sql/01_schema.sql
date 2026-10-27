-- Blindspot schema. Run once with a role that can create a database and schema.
-- Snowflake does not enforce PRIMARY KEY constraints. They document intent and help BI tools.
-- Re-running this file is safe: every statement uses IF NOT EXISTS.

CREATE DATABASE IF NOT EXISTS BLINDSPOT;
CREATE SCHEMA IF NOT EXISTS BLINDSPOT.CORE;
USE SCHEMA BLINDSPOT.CORE;

-- One row per source. Carries the access and licensing notes from fixtures/demo/sources.json.
CREATE TABLE IF NOT EXISTS SOURCES (
    SOURCE_ID            VARCHAR PRIMARY KEY,
    SOURCE_NAME          VARCHAR,
    ACCESS_NOTES         VARCHAR,
    LICENSING            VARCHAR,
    UPDATE_CADENCE       VARCHAR,
    MATCHING_LIMITATIONS VARCHAR,
    IS_SYNTHETIC         BOOLEAN,
    LOADED_AT            TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Current normalized record for each source observation. Keyed by RECORD_ID, so reloads are idempotent.
CREATE TABLE IF NOT EXISTS SOURCE_RECORDS (
    RECORD_ID    VARCHAR PRIMARY KEY,
    SOURCE_ID    VARCHAR NOT NULL,
    RECORD_TYPE  VARCHAR NOT NULL,      -- 'entity_reference' or 'relationship'
    OBSERVED_AT  TIMESTAMP_NTZ,
    SOURCE_URL   VARCHAR,
    PAYLOAD      VARIANT NOT NULL,
    IS_SYNTHETIC BOOLEAN,
    LOADED_AT    TIMESTAMP_NTZ
);

-- Append-only history. A row is added only when a record's payload changes, so changes over time stay visible.
CREATE TABLE IF NOT EXISTS SOURCE_RECORD_HISTORY (
    RECORD_ID     VARCHAR NOT NULL,
    PAYLOAD_SHA256 VARCHAR NOT NULL,
    PAYLOAD       VARIANT NOT NULL,
    LOADED_AT     TIMESTAMP_NTZ
);

-- Staging area for the loader. Truncated at the start of every load.
CREATE TABLE IF NOT EXISTS STG_SOURCE_RECORDS (
    RECORD_ID    VARCHAR,
    SOURCE_ID    VARCHAR,
    RECORD_TYPE  VARCHAR,
    OBSERVED_AT  TIMESTAMP_NTZ,
    SOURCE_URL   VARCHAR,
    PAYLOAD_JSON VARCHAR,
    IS_SYNTHETIC BOOLEAN
);

-- Derived tables. Rebuilt by 03_refresh.sql from the views in 02_views.sql.
CREATE TABLE IF NOT EXISTS ENTITIES (
    ENTITY_ID          VARCHAR PRIMARY KEY,
    ENTITY_KIND        VARCHAR,          -- 'company', 'facility', or 'event'
    NAME               VARCHAR,
    NORMALIZED_NAME    VARCHAR,
    REGISTRY_ID        VARCHAR,
    STATE_CODE         VARCHAR,
    SOURCE_RECORD_ID   VARCHAR
);

CREATE TABLE IF NOT EXISTS RELATIONSHIPS (
    RELATIONSHIP_ID    VARCHAR PRIMARY KEY,  -- equals the source record ID
    FROM_ENTITY_ID     VARCHAR,
    TO_ENTITY_ID       VARCHAR,
    RELATION_TYPE      VARCHAR,
    STATUS             VARCHAR,              -- 'documented' or 'co_occurrence'
    SOURCE_ID          VARCHAR,
    SOURCE_RECORD_ID   VARCHAR,
    SOURCE_URL         VARCHAR,
    OBSERVED_AT        TIMESTAMP_NTZ,
    MATCH_METHOD       VARCHAR,              -- 'registry_id', 'name_and_state', or 'name_only'
    MATCH_RATIONALE    VARCHAR
);

CREATE TABLE IF NOT EXISTS REJECTED_MENTIONS (
    RECORD_ID     VARCHAR,
    MENTION_NAME  VARCHAR,
    REASON        VARCHAR
);

-- Investigation output. Written by the app when Snowflake mode is active.
CREATE TABLE IF NOT EXISTS INVESTIGATIONS (
    INVESTIGATION_ID  VARCHAR PRIMARY KEY,
    TARGET_ENTITY_ID  VARCHAR,
    REPOSITORY_MODE   VARCHAR,
    CREATED_AT        TIMESTAMP_NTZ,
    NOTES             VARIANT
);

CREATE TABLE IF NOT EXISTS FINDINGS (
    INVESTIGATION_ID  VARCHAR,
    FINDING_ID        VARCHAR,
    PATTERN           VARCHAR,
    TITLE             VARCHAR,
    SCORE             NUMBER,
    CONFIDENCE        VARCHAR,
    EVIDENCE_IDS      VARIANT,
    EXPLANATION       VARIANT,
    PRIMARY KEY (INVESTIGATION_ID, FINDING_ID)
);
