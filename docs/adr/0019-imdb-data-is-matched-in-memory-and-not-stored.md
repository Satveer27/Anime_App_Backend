# IMDb data is filtered and matched in memory, and only the match decisions are stored

**Status: draft.** Not accepted until the owner has reviewed it. Supersedes the landing-table parts of ADR 0018 and the `pg_trgm` candidate query of ADR 0002.

## Decision

- Spark reads the three archived IMDb files, filters them to animation early (as ADR 0018 describes), and collects the small result to the driver. The matcher runs in Python on that in-memory data. Nothing about IMDb titles or episodes is written to Postgres.
- Postgres keeps `imdb_snapshot` (what was downloaded) and `anime_imdb_match` (the decisions: score, method, status, top candidates, the snapshot used). The Review queue and Overrides are rows of `anime_imdb_match`, as before.
- Candidates are blocked by year and type and ranked by title similarity in Python, with the same floor and top-5 rule as ADR 0018. The accept thresholds are still calibrated on the labelled set.
- `imdb_score` for `accepted` Matches and the per-episode scores are written in the same task, from the same in-memory data.
- **Is a run needed?** The `match` task runs when the download produced a new `archived` snapshot, or when the most recent `ingestion_run` with job `imdb_match` failed. A run left `running` by a crash is marked `failed` first.
- **Monitoring:** each run records a summary in `ingestion_run`: counts of matched, review and unmatched anime, the matched fraction, counts of IMDb titles and episodes kept, IMDb-only titles with no MAL anime, and peak memory. Unmatched anime are rows with status `unmatched`, so they are queryable and not only in logs.

## Why

The IMDb files are reproducible from the S3 archive, so a database copy is only a second copy. Writing it, swapping it and reading it back adds three steps that can fail and take time, with no gain in what can be audited: the decisions and their candidates are what is kept. The animation subset is small once filtered early, so memory is bounded by it, not by the dataset.

## Considered and rejected

- **Landing tables with staging and swap (ADR 0018, first draft).** More failure points (a failed load, a failed swap, a FK that follows a renamed table), more time per run, and SQL access to IMDb data that the decisions table makes mostly unnecessary.
- **A parquet handoff in S3 between tasks.** Not needed while filtering and matching are one task.

## Consequences

- Memory is the risk. Filtering early keeps it small; the peak is measured and recorded in `docs/METRICS.md`, and the VM can be raised (ADR 0017) if it is not enough.
- The IMDb side cannot be queried in SQL when debugging a Match. The stored `candidates` and the structured logs stand in for it, and a run can be repeated from the S3 archive.
- Re-matching with new thresholds means re-running the task (re-reading the archive), not a SQL query.

## Out of scope / known limitations

- IMDb titles and episodes that have no MAL anime are counted in the run summary and not added to the catalogue: `anime` is keyed by `mal_id` and driven by MAL.
- `title.akas`, an admin API for the Review queue and score history stay out of scope (ADR 0018).

## Revisit if

- The peak memory does not fit the box, or matching is too slow: store the filtered data (S3 parquet or tables) and measure.
- A second matching consumer needs the IMDb data in SQL.
