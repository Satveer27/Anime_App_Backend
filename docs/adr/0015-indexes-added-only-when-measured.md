# Secondary indexes are added only when `EXPLAIN ANALYZE` shows a benefit

Tables are created with their primary keys, unique constraints and foreign keys, and nothing else. A secondary index is added in a later migration only after `EXPLAIN ANALYZE` on a populated table shows that it helps, and the before and after numbers are recorded in `docs/METRICS.md`. This keeps the project's rule of recording every measured number, and it avoids paying write and storage cost for indexes that no query uses.

Primary keys and constraints are not optional and are never deferred: changing them later on a full table is hard. Only secondary indexes are measured.

## Decisions

- **When to measure:** during the ingestion analysis phase, after the first full backfill (#25), on populated tables. For the task table, measure twice: at the start of a run (mostly `pending` rows) and near the end (mostly `done`), because the cost of a scan grows as finished rows pile up.
- **What to record per index:** the query, the plan before and after, the time before and after, the index size, and the write cost (insert and update throughput with and without it).
- **Candidates:**
  - `ingestion_task (status, next_attempt_at)` for the worker's claim query (`FOR UPDATE SKIP LOCKED`). Compare it with the primary key alone. A partial index on only claimable rows is the alternative.
  - `review (anime_id)`, for "reviews of this anime" and for `ON DELETE CASCADE`.
  - `anime_tag (tag_id)` and `anime_studio (studio_id)`, for "all anime with this tag or studio".
  - Indexes for the filters and sorts the analysis queries use, once those exist (`status`, `year`, `type`).
- **Not candidates:** the leading column of any composite primary key already has an index. `episode`, `anime_tag (anime_id)`, `anime_studio (anime_id)`, `anime_relation` and `anime_statistics_mal` are covered for lookups by anime.

## Considered and rejected

- **Indexing upfront by intuition.** Postgres does not index foreign key columns automatically, so there is a real chance some of these will be needed. But the guess costs writes and storage with no evidence, and there is no number to show for it.

## Consequences

- Until an index is added, `ON DELETE CASCADE` from `anime` scans `review` and the lookups listed above scan their tables. Deleting an anime is rare, so this is acceptable.
- Each added index is its own migration, with its numbers in `docs/METRICS.md` and a reference to this ADR.

## Revisit if

- A query on a user-facing path is slow before the analysis phase. Then measure it right away and add the index.
- Write throughput during the backfill drops after an index is added.
