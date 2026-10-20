# Ingestion run metrics

Every ingestion job that records metrics writes one row to the `ingestion_run` table per run. This page explains the columns and every key in the `summary`, so a run can be read without opening the code. Only two jobs record metrics today: **discovery** (`DiscoveryJob`) and **fetch** (`FetchAnimeJob`). The numbers worth keeping long-term go into `docs/METRICS.md`.

If you add a metric to a job, add it here in the same change.

## The `ingestion_run` table

| Column | Meaning |
|---|---|
| `id` | Auto-incrementing run number (1, 2, 3, ...). |
| `job` | Which job ran (see below). The history of one job is `WHERE job = '...'`. |
| `status` | `running`, `succeeded` or `failed`. |
| `started_at` / `finished_at` | When the run began and ended. `finished_at` is empty while `running`. |
| `summary` | JSONB with the metrics below. Empty while `running`. Floats are rounded to 3 decimals. |
| `error` | Only on a `failed` run: `ExceptionType: message`, cut to 500 characters. `NULL` on success. |

A run row is created when the job **starts** and filled in when it **ends**, so:
- `succeeded`: the job ran to the end.
- `failed`: the job stopped on an error. `error` says why, and `summary` holds the partial numbers up to that point.
- `running` for a long time: the process was killed (Ctrl+C, container stopped, out of memory) before it could record the end. Nothing cleans these up yet, so treat an old `running` row as an interrupted run.

Run records use their own database session, so a failure inside the job's work does not erase them.

### Job names

| `job` value | Written by |
|---|---|
| `discovery_overall` | `DiscoveryJob.run_job_overall`, the whole list, existing tasks untouched |
| any name you pass, e.g. `discovery_weekly` | `DiscoveryJob.run_job_refresh`, a filtered list that resets known anime to `pending` |
| `fetch_tasks` | `FetchAnimeJob.fetch_from_tasks`, the worker loop |
| `clean_anime` | `CleanAnimeJob.full_clean_loop`, raw rows parsed into the catalogue |

## Discovery runs (`discovery_overall`, and refresh runs)

Discovery pages through Tenrai's anime list and creates (or, in a refresh, resets) tasks. One "page" is one list request of up to 100 anime, and the job commits once per page.

| Key | Meaning |
|---|---|
| `start_page` | The page the run started on (1 unless it was resumed). |
| `pages` | Pages completed **in this run**, not the last page number. A run resumed from page 5 that finishes 3 pages reports 3. |
| `anime_seen` | Anime in the pages fetched, before any skipping. |
| `anime_skipped` | Anime skipped because the list item had no `mal_id`. Each one is logged as `anime_missing_mal_id` with its data so you can see why. |
| `anime_skipped_type` | Anime skipped by the safety-net type check (Music, CM, PV). Should be `0`: the list is already filtered by type when requested. Above 0 means Tenrai is ignoring the type filter, and a `discovery_type_filter_leaked` warning is logged. |
| `tasks_inserted` | Tasks that did not exist and were created. In `discovery_overall` this is the only task counter. |
| `tasks_reset` | Refresh runs only: existing `done` or `failed` tasks sent back to `pending`. Always `0` in `discovery_overall`. |
| `tasks_unchanged` | Refresh runs only: tasks left as they were because they are `dead`, `gone` or already `pending`. A high number of `dead` ones means refreshed anime are being skipped on purpose. Always `0` in `discovery_overall`. |
| `list_seconds` | Total time spent inside the list requests. This includes the rate limiter's wait and any retries, so it is **not** pure network time. |
| `max_list_seconds` | The slowest single list request. A spike here is usually a retry or a 429 wait. |
| `avg_list_ms` | `list_seconds` per page, in milliseconds. With the 0.6 s limiter spacing, expect about 600 or a bit under; much higher means real latency or retries. |
| `db_seconds` | Total time spent inserting the tasks and committing. |
| `avg_db_ms` | `db_seconds` per page, in milliseconds. |
| `elapsed_seconds` | Wall time of the whole run. |
| `requests_per_minute` | `pages / elapsed_seconds * 60`. It counts list requests only and includes database time, so it reads slightly under the true request rate. It should stay at or below 100 (ADR 0012). The first request is not rate limited, so a 2-page run reads high. |
| `resume_page` | `null` on success. On a failed run, the page to pass as `page` to continue (see below). |
| `failed_page` | Failed runs only: the page that was being fetched when the run stopped. |

`avg_list_ms`, `avg_db_ms` and `requests_per_minute` are missing when no page finished.

### Resuming a failed discovery run

`run_job_overall(page=resume_page)` continues from there. Re-running `discovery_overall` from page 1 is also safe (existing tasks are untouched), it just repeats about 3 minutes of requests. For a refresh job, resume instead of restarting from page 1 only if the worker has already started, because a restart would send finished tasks back to `pending`.

## Fetch runs (`fetch_tasks`)

The fetch job claims tasks one at a time, fetches the Resource from Tenrai, stores the raw response and marks the task. One run processes tasks until none is claimable, or until it has to stop.

| Key | Meaning |
|---|---|
| `tasks` | Tasks processed in this run. Always `done + failed + dead + gone`. |
| `done` | Fetched and stored. |
| `first_try_tasks` | Tasks in this run that had never failed before: the first attempt of each task. A task that failed and is claimed again later is counted in `tasks` again but not here. It is the denominator of the first-try success rate. |
| `done_first_try` | The part of `done` that had no failed attempt before: the task succeeded the first time it was claimed. `done - done_first_try` succeeded only after one or more earlier `failed` attempts. It counts task attempts, not requests: the adapter's own retries inside one attempt (a 503 retried after 1 s) are not included. |
| `gone` | Tenrai answered 404: the anime or resource no longer exists. |
| `dead` | A permanent problem: a 4xx other than 404, a response that was not valid JSON, a Resource with no fetcher, or episodes still paging after 100 pages. Never retried automatically. |
| `failed` | Marked `failed` this run: a temporary error (5xx, 429, network) that outlasted the adapter's own retries, or an unexpected error in our code. The task backs off and is retried later, and becomes `dead` after 5 attempts. This counts task outcomes in this run, so a task that fails again in a later run is counted again. |
| `task_seconds` | Total time spent on tasks (fetching, storing and committing). |
| `max_task_seconds` | The slowest single task. Episodes with many pages, retries and 429 waits show up here. |
| `avg_task_ms` | `task_seconds` per task, in milliseconds. It includes the limiter's wait and any retry backoff, so it is **not** pure network latency. |
| `tasks_per_minute` | `tasks / elapsed_seconds * 60`. |
| `by_resource` | For each of `details`, `statistics`, `reviews`, `episodes` that ran: `count`, `seconds` (total) and `avg_ms`. Compare them to see which Resource costs the most. |
| `elapsed_seconds` | Wall time of the whole run. |
| `stopped_by` | `null` when the run ended because nothing was claimable. Otherwise the name of the exception that stopped it (see below). |
| `queue_after` | Tasks in each state **in the whole table** at the end of the run: `pending`, `done`, `failed`, `dead`, `gone`. It is empty (`{}`) if the table could not be read. |

`avg_task_ms` and `tasks_per_minute` are missing when no task was processed.

### Why a run stops (`stopped_by`)
A stopped run has `status = 'failed'`, `error` set, and `stopped_by` naming the cause:

| `stopped_by` | What happened | What to do |
|---|---|---|
| `CircuitOpenError` | Tenrai failed repeatedly, so the circuit breaker is open. The task in hand was released untouched and used no attempt. | Wait, then re-run. |
| a SQLAlchemy error (e.g. `OperationalError`) | The database failed, or Postgres rejected a payload. The task was rolled back and stays `pending`. | Find the cause; the log line `task_aborted_database` has the task key. A rejected payload will stop every run at that task until fixed. |
| `RepeatedUnexpectedErrors` | 10 tasks in a row failed in our own code (each was marked `failed`). Probably a bug, not one bad anime. | Read the `task_failed_unexpected` logs, which include the response, then fix and re-run. |

### Success rates
`tasks` mixes first attempts with retries: a task that failed is claimed again once its backoff has passed, usually inside the same run, so it appears in `tasks` twice. So use these:

| Rate | Formula |
|---|---|
| First-try success | `done_first_try / first_try_tasks` |
| Retry success | `(done - done_first_try) / (tasks - first_try_tasks)` |
| Attempt outcome mix | `done`, `failed`, `dead`, `gone` as shares of `tasks` |

`failed` is not final: a failed task is retried later and may still end `done`. The final result per task is in `queue_after` (and in `ingestion_task`): the share of tasks that ended `done`, `dead` or `gone`.

### Reading `queue_after`
"No task is claimable" is not the same as "everything is done": a `failed` task is waiting out its backoff and is not claimable until its retry time. So a succeeded run can end with `failed` above 0. `queue_after["failed"]` is exactly that number of tasks still waiting. A later run picks them up once their time has passed. `dead` and `gone` stay where they are for you to inspect.

### Reading the numbers for the rate-limit question
ADR 0012 spaces requests 0.6 s apart (100 a minute). A fetch task makes one request, except episodes, which make one per page, so the whole backfill averages about 1.01 requests per task (`docs/METRICS.md`). If the run is limited only by the rate limiter, expect `tasks_per_minute` to be close to 99. If it is clearly lower and there are few `failed` tasks and no 429s in the logs, latency or the database has become the bottleneck, and `by_resource` shows which Resource is slow. These figures do not show the gap between individual requests; that needs request timestamps in the adapter.

## Clean runs (`clean_anime`)

The clean job reads raw rows in batches of 500 (about 125 anime) and writes the catalogue. Each batch is one transaction. `since` is the start of the last succeeded clean run, so a weekly run only reads rows the fetch job refreshed since then.

| Key | Meaning |
|---|---|
| `since` | The cut-off used, as an ISO timestamp, or `null` for a full pass (the first run, or `full_pass=True`). |
| `batches` | Batches that read at least one row. |
| `rows_read` | Raw rows read (details, statistics, reviews, episodes). |
| `anime_changed` | Anime rows inserted or whose data changed. An unchanged anime writes nothing. |
| `tags_upserted`, `studios_upserted` | New or renamed tags and studios. Near zero after the first batches. |
| `tag_links_changed`, `studio_links_changed`, `relation_links_changed` | Anime whose tag, studio or relation links were rewritten. This counts anime, not link rows. |
| `statistics_written`, `episodes_written`, `reviews_written` | Rows written. They are overwritten on every pass, changed or not (reviews keep `updated_at` as "last seen"). |
| `rows_written` | `anime_changed + tags_upserted + studios_upserted + statistics_written + episodes_written + reviews_written`. The rows written to the catalogue tables, links not included. |
| `parse_failed` | Details or statistics responses that could not be parsed. Each one is logged as `clean_parse_failed` with its `mal_id`, resource and the start of the response, and is not retried until it is re-fetched or a full pass is run. |
| `skipped_no_anime` | Statistics, episodes or reviews rows skipped because their anime has no row (its details failed or were never fetched). Logged once per batch with a sample of ids. |
| `skipped_unsupported` | Rows of a resource with no parser (such as `characters`). |
| `read_seconds` | Total time reading batches from `raw_tenrai`. |
| `write_seconds` | Total time parsing, writing and committing the batches. |
| `elapsed_seconds` | Wall time of the whole run. |
| `max_batch_seconds` | The slowest batch (read plus write). |
| `avg_batch_ms` | Average batch time in milliseconds. A batch far above this average is the one to look at. |
| `rows_read_per_second` | `rows_read / elapsed_seconds`. |
| `resume_after` | `null` on success. On a failed run, `[mal_id, "resource"]` of the last committed batch: pass `after=(mal_id, Resource("resource"))` to continue from there. |

The per-batch timings are also logged for every batch as `clean_batch_done` (`read_seconds`, `write_seconds` and the counts), so a slow batch can be found by its `last_mal_id`.

`read_seconds` against `write_seconds` says where the time goes: a large `read_seconds` means the keyset query over `raw_tenrai` or the size of the JSON payloads, a large `write_seconds` means the upserts. Compare `rows_written` with `rows_read` to see how much of a pass actually changed anything.
