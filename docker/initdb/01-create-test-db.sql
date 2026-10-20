-- Runs once, when Postgres starts with an empty data directory (a fresh db_data volume).
-- Database for the tests marked `postgres`; point TEST_DB_URL at it. The test fixtures empty
-- every table in it, so never point TEST_DB_URL at anime_database.
CREATE DATABASE anime_test;
