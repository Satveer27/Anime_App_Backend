# IMDb ratings come from a weekly Spark job, matched to MAL in memory and recorded in one Match table

**Status: draft.** Not accepted until the owner has reviewed it. **Superseded in part by ADR 0019**: the IMDb data is filtered and matched in memory and is not stored in Postgres, so there are no `imdb_title` or `imdb_episode` tables, no staging or swap, and no `pg_trgm` candidate query. Where this text still says otherwise, ADR 0019 wins.

IMDb's bulk files are filtered to animation with PySpark and matched to MAL Anime in memory, and the Matches write `imdb_score` into `anime` and `episode`. This fills the IMDb part left open by ADR 0014 and refines the matching of ADR 0002 and the season handling of ADR 0003. The matching and scoring code is written by the owner; this ADR fixes the design only.

## Decisions

**Files and archive**
- **Files:** `title.basics`, `title.episode`, `title.ratings`. `title.akas` is excluded: it is by far the largest file and the first fix to try if recall is low (revisit below).
- **Download:** the first task streams the three `.tsv.gz` files from `datasets.imdbws.com` (HTTPS, that host only, with a size cap) to local temp disk, then uploads the unaltered files to the raw bucket under `imdb/{fetched_at}/{file}.tsv.gz`, where `fetched_at` is the run's fetch time in UTC with a fixed format (for example `imdb/2026-11-09T030000Z/title.basics.tsv.gz`). Keys are built from the snapshot, never found by listing the bucket. Temp files are deleted after the run.
- **Retention, no versioning:** an S3 lifecycle rule expires snapshots after about 28 days. There is no object versioning and no score history (ADR 0004, ADR 0016). Snapshots exist only to re-run or debug a recent run.
- **Skip rule:** if the `ETag` of all three files equals that of the latest `archived` snapshot, the run is recorded as `skipped` and the rest of the DAG is skipped. Failed, skipped and still-running snapshots are ignored when looking for the latest.
- **`imdb_snapshot`:** one row per **run** (idempotent per Airflow `run_id`, so a retry finds its own row and the history of runs is kept): `id` (PK), per-file `etag` and `size_bytes` (`basics_`, `episode_`, `ratings_`), `status` (`running`, `archived`, `skipped`, `failed`), `finished_at`. It describes the downloaded files only; what Spark does with them is not recorded here. `archived` means the three files are in S3. No checksums. The table lives in RDS, the files in S3.
- **Is a load needed?** Load state is read from `ingestion_run` (job `imdb_match`), not from the snapshot. The load runs when this run's download produced a new `archived` snapshot, or when the most recent `imdb_match` run `failed`. A `skipped` download after a `succeeded` load does nothing. A failed load is therefore retried on the next run even if the download is `skipped`. The load reads the latest `archived` snapshot. A load run left `running` by a crash is marked `failed` before the check, so it cannot block retries.
- **Security:** the pipeline identity gets `s3:PutObject` and `s3:GetObject` on the `imdb/` prefix only (as ADR 0016).

**Spark**
- `pyspark` is a dependency group of the app, run as a CLI entry point in the app virtualenv (ADR 0001). A headless arm64 JRE is added to `Dockerfile.prod`.
- **Local mode, one thread, 2 GB driver heap** (`local[1]`, `spark.driver.memory`), both settings, because the box is 1 OCPU / 6 GB (ADR 0017) and Airflow already uses about 2 GB. They are raised when the VM is raised to 12 GB. Peak memory is measured and recorded.
- Spark reads the local copy, not `s3a://`. Files are read with an explicit schema and `\N` as null. Malformed rows are dropped and counted.
- **Early filtering:** `basics` is filtered to titles whose genres contain `Animation` and whose type is `tvSeries`, `tvMiniSeries`, `movie`, `tvMovie`, `video` or `tvSpecial`. Episodes are selected through their **parent** (broadcast join on `title.episode`), never by their own genres, because `tvEpisode` rows often lack `Animation`. `ratings` is joined last, on the surviving ids.
- **Output:** Spark collects the small filtered result (animation titles, their episodes and ratings) to the driver. It is not written to Postgres (ADR 0019).

**Schema (four tables, primary keys and constraints only, ADR 0015)**
- `imdb_snapshot`: above. Spark's runs are recorded in `ingestion_run` (job `imdb_match`: status and summary with row counts), not here.
- `imdb_title` and `imdb_episode` do not exist (ADR 0019): the filtered IMDb data lives in memory for one run.
- `anime_imdb_match`: `anime_id` (PK, FK to `anime`), `imdb_tconst` (nullable), `season_number`, `episode_from`, `episode_to`, `score`, `method` (`override`, `title`, `chain`), `status` (`accepted`, `review`, `unmatched`), `candidates` (JSONB, top candidates with their feature scores), `snapshot_id` (FK to `imdb_snapshot.id`, nullable), `decided_at`. `imdb_tconst` references no table, because the IMDb data is not stored (ADR 0019).
- **Overrides are rows with `method = 'override'`.** They are checked before any scoring and the pipeline never overwrites them (a test guards this). Unmatched anime and the Review queue are statuses, not tables. A "confirmed no IMDb match" override has a null `imdb_tconst`.
- **Scores:** `anime.imdb_score` and `episode.imdb_score` are written for `accepted` rows only. If an anime has no MAL episode rows, episode rows are inserted with only IMDb-owned columns; a later MAL fetch fills the same `(anime_id, episode_number)` row. Each pipeline writes only its own columns (ADR 0014). A refresh overwrites the score; there is no history.

**Matching** (one flow for all MAL types; the owner implements it)
- **Order:** override, then candidates, then scoring.
- **Normalisation:** one pure function used on both sides (NFKC, lowercase, `&` to `and`, punctuation removed, noise words and season markers stripped; the season number is kept as a hint). No transliteration.
- **Title variants:** MAL `title`, `title_english`, `title_japanese` against IMDb `primary_title`, `original_title`. The similarity is the best across pairs.
- **Candidate generation:** in memory, blocked by a loose filter (IMDb `start_year` within ±1 of the MAL year, a type-compatibility table), then title similarity above a floor of about 0.4 to be tuned (ADR 0019). The top 5 are stored in `candidates`.
- **Scoring:** weighted title, year, episode count and type (ADR 0002), plus an episode-name overlap bonus where MAL has episode titles. Weights are a config dict calibrated on the labelled set.
- **Decision:** `accepted` only if the top score is at or above an accept threshold and leads the runner-up by a minimum margin. A score between the review floor and the accept threshold, or a near tie, is `review`. Below the floor or with no candidates it is `unmatched`. If two MAL roots select the same IMDb series, the lower-scoring one goes to `review`. All thresholds are placeholders until calibrated.
- **Movies, OVAs and specials** go through the same flow. Movies match IMDb `movie`, `tvMovie`, `video` and `tvSpecial` and add a runtime check.
- **Season problem (refines ADR 0003):** find the IMDb series by title, then walk its episodes sorted by `(season, episode)` with a cursor, taking the next `n` episodes for each entry in Main line order (`n` is the MAL episode count). Episode names are compared where MAL has them; cumulative counts are the fallback. If a slice ends within 2 episodes of an IMDb season boundary it snaps to the boundary. A larger mismatch sends that entry and the rest of the chain to `review` (ADR 0003). The season hint from the title is a cross-check, not a rule. The `episode` number written is the position within the slice. An entry that is still airing, or has no count, takes the IMDb episodes currently available and is re-sliced each week. Season 0 (specials) is searched only for OVA, Special and TV Special entries attached to a matched series, and is accepted only through episode-name overlap, otherwise `review`.

**Measuring quality**
- A hand-labelled set of 200 entries, growing towards 400, stratified by type and popularity and including entries with no IMDb match. Half is held out and never used to tune. It is a committed CSV; a script computes the metrics.
- **Decisive numbers:** precision of auto-accepted Matches (series and slice both correct; target at least 97%, ADR 0002) and coverage, reported separately. Recall, review rate and unmatched rate are tracked too, with confidence intervals. The review and unmatched rates are health signals on every run: they measure caution, not correctness.

**DAG `imdb_weekly`** (Monday 03:00 UTC, `catchup=False`, `max_active_runs=1`)
1. `download` (pool `imdb`): fetch, archive, write `imdb_snapshot`; skip the rest if unchanged and no load is pending (see "Is a load needed?"). Retries 3 times, 10 minute backoff.
2. `match` (pool `imdb`, then `tenrai` for the writes): Spark filters, the result is matched in memory, and `anime_imdb_match` and the `imdb_score` values are written. Scores anime with no Match yet and anime in `review` or `unmatched`. `accepted` and `override` rows are left alone unless their `tconst` is no longer in the data. Recorded as an `ingestion_run` with job `imdb_match`. Retries once.
- **New pool `imdb`** (one slot) for download and Spark, which do not touch the catalogue. `match` and `score` use the existing `tenrai` pool so they queue behind any fetch or clean (ADR 0017). No sensors. Each task logs a summary with structlog.
- **First run:** the same DAG, triggered by hand, matches the whole catalogue. Later runs handle the delta.

**Security and failure modes**
- No public endpoint, so no user AuthN/AuthZ surface. Overrides and the Review queue are changed by an admin through SQL or a script until an admin API exists.
- Secrets come from the environment, never the repository. SQL is parameterised. No personal data is involved.
- A truncated or corrupt download fails the gzip check. A Spark or memory failure fails the `match` task before any write; writes are idempotent upserts. The user sees the last good `imdb_score`, and an unmatched or review anime shows none.

## Considered and rejected

- **IMDb landing tables in Postgres (this ADR's first draft).** Replaced by ADR 0019: they added a write, a swap and a read that could each fail, for data that can be regenerated from the S3 archive.
- **Writing scores with no Match table.** The weekly refresh would have to re-match everything, and there would be nowhere for confidence, the Review queue or Overrides.
- **`title.akas` in v1.** Tens of millions of rows for an unmeasured gain.
- **S3 object versioning for the snapshots.** Not needed without history (ADR 0004).
- **Sensors between DAGs.** They waste a slot. The `tenrai` pool already serialises catalogue writers.

## Consequences

- The IMDb data and the catalogue change in different tables, and several pipelines write `anime` and `episode`, so each upsert writes only its own columns.
- A match depends on the Tenrai catalogue being current. A very new anime has no Match until the next Monday run after it is ingested.
- Without `akas`, alternative-title recall is lower, and the labelled set must show by how much.
- The weekly Spark run is CPU-heavy on 1 OCPU. It may slow a concurrent fetch; this is measured.
- Precision is measured on a small labelled set, so its interval is wide at first.

## Out of scope / known limitations

- `title.akas`; an admin API or UI for the Review queue; season-0 specials beyond episode-name overlap; score history (ADR 0004); non-animation titles; AniList.
- IMDb's datasets are for personal, non-commercial use. That is acceptable for this portfolio project and would need a licence for anything commercial.

## Revisit if

- Recall on the labelled set is poor: add `akas` filtered to the surviving series.
- Spark does not fit in the memory budget, or slows the fetch: raise the VM to 12 GB (ADR 0017), or give IMDb its own box.
- The plain-scan candidate query is too slow: add a trigram GIN index and record before and after (ADR 0015).
- A second source needs raw tracking of its own: decide whether the `imdb_snapshot` pattern generalises.
