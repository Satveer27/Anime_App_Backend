# Mock-only tests, with one real-Postgres exception

Unit and controller tests use mocks. The exception is a real Postgres container in CI, used only for what mocks cannot verify: materialised views, pgvector queries, idempotent upserts and text-to-SQL execution. Evals on pull requests replay recorded model outputs; real-model evals run manually or nightly under a spend cap, so CI is deterministic and free.
