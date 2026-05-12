-- =====================================================================
-- 01_account_setup.sql
-- Healthcare Analytics Platform — account-level objects
-- Run as ACCOUNTADMIN. Idempotent (safe to re-run).
-- =====================================================================

USE ROLE ACCOUNTADMIN;

-- ---------------------------------------------------------------------
-- 1. CUSTOM ROLES
-- ---------------------------------------------------------------------
-- WHY: Real enterprises never grant ACCOUNTADMIN to humans for daily work.
-- We build a role hierarchy that mirrors Snowflake's recommended pattern:
--
--   ACCOUNTADMIN
--        │
--   SYSADMIN ──────┐
--        │         │
--   HC_ADMIN ──────┤  (owns all HC_* objects)
--        │         │
--   HC_ENGINEER ───┤  (builds pipelines, can DDL in RAW/STAGING)
--        │         │
--   HC_ANALYST ────┘  (read-only on ANALYTICS, masked PHI)
--
-- INTERVIEW HOOK: "Why a role hierarchy instead of granting privileges
-- directly to users?" → Answer: future-proofing (add a user, grant them
-- a role, done); auditability; separation of duties; SOC2/HIPAA controls.
-- ---------------------------------------------------------------------

CREATE ROLE IF NOT EXISTS HC_ADMIN
  COMMENT = 'Owns all healthcare platform objects';

CREATE ROLE IF NOT EXISTS HC_ENGINEER
  COMMENT = 'Builds and maintains pipelines';

CREATE ROLE IF NOT EXISTS HC_ANALYST
  COMMENT = 'Read-only consumer of ANALYTICS layer; PHI masked';

-- Hierarchy: roles inherit privileges of roles granted to them.
GRANT ROLE HC_ADMIN    TO ROLE SYSADMIN;
GRANT ROLE HC_ENGINEER TO ROLE HC_ADMIN;
GRANT ROLE HC_ANALYST  TO ROLE HC_ADMIN;

-- Grant to yourself so you can switch roles in the UI.
-- Replace <YOUR_USERNAME> with the username you set during signup.
-- Find it with: SELECT CURRENT_USER();
GRANT ROLE HC_ADMIN    TO USER PAVITHRAPANDITHURAI;
GRANT ROLE HC_ENGINEER TO USER PAVITHRAPANDITHURAI;
GRANT ROLE HC_ANALYST  TO USER PAVITHRAPANDITHURAI;


-- ---------------------------------------------------------------------
-- 2. VIRTUAL WAREHOUSES
-- ---------------------------------------------------------------------
-- WHY THREE WAREHOUSES:
-- - Separation of concerns: ingestion, transformation, BI each have
--   different load patterns. If they share a warehouse, BI queries
--   slow down during nightly loads — a real production pain point.
-- - Cost attribution: finance can see exactly what each workload costs.
-- - Independent scaling: scale up TRANSFORM during backfills without
--   touching analyst-facing query performance.
--
-- AUTO_SUSPEND = 60: warehouse pauses after 60s of inactivity.
--   Trial credits are precious — never leave warehouses running.
-- INITIALLY_SUSPENDED: don't bill us the moment we run this script.
--
-- INTERVIEW HOOK: "How do you size a warehouse?"
-- → Start with XS. Measure with QUERY_HISTORY. Size UP for memory-bound
--   queries (spilling to remote storage); size OUT (multi-cluster) for
--   concurrency, not single-query speed.
-- ---------------------------------------------------------------------

CREATE WAREHOUSE IF NOT EXISTS WH_INGEST_XS
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Bulk COPY INTO + Snowpipe simulation';

CREATE WAREHOUSE IF NOT EXISTS WH_TRANSFORM_S
  WAREHOUSE_SIZE = 'SMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'dbt runs, Streams/Tasks, Dynamic Tables';

CREATE WAREHOUSE IF NOT EXISTS WH_ANALYTICS_XS
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Analyst / BI queries on Gold';


-- ---------------------------------------------------------------------
-- 3. DATABASES
-- ---------------------------------------------------------------------
-- WHY THREE DATABASES (not one DB with three schemas):
-- - Grant boundaries: easy to say "HC_ANALYST gets USAGE on ANALYTICS_DB
--   and nothing else." With one DB this requires schema-level juggling.
-- - Blast radius: a runaway DROP in dev RAW can't touch ANALYTICS.
-- - Replication / cloning units: databases are the natural unit for
--   zero-copy clones (we'll do this Day 3).
--
-- INTERVIEW HOOK: "How do you organize databases in Snowflake?"
-- → Layer (raw/staging/analytics) × Environment (dev/prod). We're doing
--   layer only for time, but I'll mention the prod/dev clone pattern.
-- ---------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS HC_RAW_DB
  COMMENT = 'Bronze — landing zone, immutable raw data';

CREATE DATABASE IF NOT EXISTS HC_STAGING_DB
  COMMENT = 'Silver — cleaned, conformed, deduplicated';

CREATE DATABASE IF NOT EXISTS HC_ANALYTICS_DB
  COMMENT = 'Gold — dims, facts, marts for BI';

-- Schemas inside each DB. We use a single schema per DB for simplicity;
-- a real org might have HC_RAW_DB.CLAIMS, HC_RAW_DB.PHARMACY, etc.
CREATE SCHEMA IF NOT EXISTS HC_RAW_DB.RAW;
CREATE SCHEMA IF NOT EXISTS HC_STAGING_DB.SILVER;
CREATE SCHEMA IF NOT EXISTS HC_ANALYTICS_DB.GOLD;


-- ---------------------------------------------------------------------
-- 4. OWNERSHIP TRANSFER + PRIVILEGES
-- ---------------------------------------------------------------------
-- WHY: Right now ACCOUNTADMIN owns everything we created. We transfer
-- ownership to HC_ADMIN so day-to-day work doesn't require ACCOUNTADMIN.
-- This is the single most common security misconfiguration I see in
-- portfolio projects — candidates demo with ACCOUNTADMIN and don't
-- realize that signals "I've never worked in a real Snowflake account."
-- ---------------------------------------------------------------------

-- Warehouses
GRANT OWNERSHIP ON WAREHOUSE WH_INGEST_XS    TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON WAREHOUSE WH_TRANSFORM_S  TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON WAREHOUSE WH_ANALYTICS_XS TO ROLE HC_ADMIN COPY CURRENT GRANTS;

-- Databases (and the schemas underneath them via cascade)
GRANT OWNERSHIP ON DATABASE HC_RAW_DB       TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON DATABASE HC_STAGING_DB   TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON DATABASE HC_ANALYTICS_DB TO ROLE HC_ADMIN COPY CURRENT GRANTS;

GRANT OWNERSHIP ON ALL SCHEMAS IN DATABASE HC_RAW_DB       TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SCHEMAS IN DATABASE HC_STAGING_DB   TO ROLE HC_ADMIN COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SCHEMAS IN DATABASE HC_ANALYTICS_DB TO ROLE HC_ADMIN COPY CURRENT GRANTS;

-- HC_ENGINEER gets the privileges needed to build pipelines.
GRANT USAGE ON WAREHOUSE WH_INGEST_XS    TO ROLE HC_ENGINEER;
GRANT USAGE ON WAREHOUSE WH_TRANSFORM_S  TO ROLE HC_ENGINEER;
GRANT USAGE ON WAREHOUSE WH_ANALYTICS_XS TO ROLE HC_ENGINEER;

GRANT USAGE ON DATABASE HC_RAW_DB       TO ROLE HC_ENGINEER;
GRANT USAGE ON DATABASE HC_STAGING_DB   TO ROLE HC_ENGINEER;
GRANT USAGE ON DATABASE HC_ANALYTICS_DB TO ROLE HC_ENGINEER;

GRANT USAGE ON ALL SCHEMAS IN DATABASE HC_RAW_DB       TO ROLE HC_ENGINEER;
GRANT USAGE ON ALL SCHEMAS IN DATABASE HC_STAGING_DB   TO ROLE HC_ENGINEER;
GRANT USAGE ON ALL SCHEMAS IN DATABASE HC_ANALYTICS_DB TO ROLE HC_ENGINEER;

GRANT CREATE TABLE, CREATE VIEW, CREATE STAGE, CREATE FILE FORMAT,
      CREATE STREAM, CREATE TASK, CREATE DYNAMIC TABLE,
      CREATE PROCEDURE, CREATE FUNCTION
  ON SCHEMA HC_RAW_DB.RAW TO ROLE HC_ENGINEER;

GRANT CREATE TABLE, CREATE VIEW, CREATE STREAM, CREATE TASK,
      CREATE DYNAMIC TABLE, CREATE PROCEDURE
  ON SCHEMA HC_STAGING_DB.SILVER TO ROLE HC_ENGINEER;

GRANT CREATE TABLE, CREATE VIEW, CREATE DYNAMIC TABLE, CREATE MATERIALIZED VIEW
  ON SCHEMA HC_ANALYTICS_DB.GOLD TO ROLE HC_ENGINEER;

-- HC_ANALYST gets read-only on Gold. PHI masking comes Day 3.
GRANT USAGE ON WAREHOUSE WH_ANALYTICS_XS TO ROLE HC_ANALYST;
GRANT USAGE ON DATABASE HC_ANALYTICS_DB  TO ROLE HC_ANALYST;
GRANT USAGE ON SCHEMA HC_ANALYTICS_DB.GOLD TO ROLE HC_ANALYST;

-- "Future grants" — anything created in this schema later auto-grants
-- SELECT to HC_ANALYST. This is critical for real systems where you
-- don't want to re-grant every time someone adds a table.
GRANT SELECT ON FUTURE TABLES         IN SCHEMA HC_ANALYTICS_DB.GOLD TO ROLE HC_ANALYST;
GRANT SELECT ON FUTURE VIEWS          IN SCHEMA HC_ANALYTICS_DB.GOLD TO ROLE HC_ANALYST;
GRANT SELECT ON FUTURE DYNAMIC TABLES IN SCHEMA HC_ANALYTICS_DB.GOLD TO ROLE HC_ANALYST;


-- ---------------------------------------------------------------------
-- 5. VERIFY
-- ---------------------------------------------------------------------
USE ROLE HC_ENGINEER;
USE WAREHOUSE WH_TRANSFORM_S;
USE DATABASE HC_STAGING_DB;
USE SCHEMA SILVER;

SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA();
-- Expected: HC_ENGINEER | WH_TRANSFORM_S | HC_STAGING_DB | SILVER