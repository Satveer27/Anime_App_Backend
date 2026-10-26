# Metrics

Measured numbers for AnimePlanet, with how each was obtained. Projections are labelled as projections. Anything not yet measured is listed under [Open questions](#open-questions).

## 1. MAL data probe (Tenrai API), 13-14 Oct 2026

Issue #27. Goal: find out what the MAL data source returns, what it costs per anime, and whether a sustained request rate of 100/min is safe, before building the ingestion pipeline.

### Why Tenrai

The public Jikan API was discontinued on 1 Oct 2026 (jikan-rest issue #613) and `api.jikan.moe` no longer answers. [Tenrai](https://tenrai.org/) (`https://api.tenrai.org/v1`) is a Jikan v4-compatible service. The adapter keeps the Jikan v4 response shapes, so the data source can be swapped by changing the base URL.

### Headline results

| Question | Answer |
|---|---|
| Can we sustain 100 requests/min? | Yes for the 29-minute probe: 2,852 requests, **0 errors, 0 x 429, 0 timeouts** |
| Did we stay inside the 4 requests/s cap? | Yes: **at most 2 requests in any 1 s window**, at most 100 in any 60 s window |
| How many anime are in scope? | **28,497** (after the rating filter; 285 list pages of 100) |
| Requests per anime | **4.03** on average (details, statistics, reviews p1, episodes p1, plus extra episode pages) |
| Backfill size and time (projection) | about **115,000 requests, about 19 hours** at 100/min |
| Raw layer size (projection) | **0.55-1.13 GB** of JSON |
| Is latency a problem? | No: **median 0.05 s** per request against 0.6 s spacing |
| Do we need concurrency? | No: the rate limit, not latency, sets throughput |

### Method

A throwaway notebook (`analysis/tenrai_probe/jikan_ingestion.ipynb`) fetched a sample of anime through the same four calls the real pipeline will use, one request at a time, and logged every request.

- **Pacing.** A request may only start 0.6 s after the previous one started (60 / 100 requests per minute). One client, sequential, no concurrency.
- **Request log.** Per request: start time (monotonic clock), endpoint, status, latency, decoded response bytes, `Retry-After`, cache header and any rate-limit headers. Failed attempts such as timeouts are logged too, because the server counts them.
- **Rate check.** From the logged start times, for each request count how many requests started within the next 1 s and the next 60 s. The maximum of those counts is compared with the limits (4/s and 120/min). Start times are used because the server counts requests as they arrive, not when responses finish.
- **Retries.** 429 sleeps for `Retry-After` (30 s if absent), 5xx and network errors back off exponentially, up to 5 attempts. None were triggered.
- **Settings.** `httpx` client, 30 s timeout, identifying `User-Agent`, list filter `rating=g,pg,pg13,r17,r` with `limit=100`.

### Sample

| Group | Selection | Anime fetched |
|---|---|---|
| `page1`, `page75`, `page150`, `page225` | Two list pages each, in `mal_id` order (pages p and p+1), 150 anime per group | 600 |
| `top_100` | The 100 most-followed anime (`order_by=members&sort=desc`) | 100 |
| **Total** | | **700** (692 distinct; 8 anime appear in both `page1` and `top_100`) |

The groups are spread across the ID range to include old, mid-range and recent anime. The sample is not random, and it is 99% finished anime (692 Finished Airing, 7 Currently Airing, 1 Not yet aired). Figures for airing shows are therefore not yet measured.

### Rate limiting and pacing

| Group | Requests | Minutes | Avg per min | Smallest gap | Max in any 1 s | Max in any 60 s |
|---|---|---|---|---|---|---|
| page1 | 618 | 6.17 | 100.1 | 0.60 s | 2 | 100 |
| page75 | 605 | 6.04 | 100.1 | 0.60 s | 2 | 100 |
| page150 | 602 | 6.03 | 99.8 | 0.60 s | 2 | 100 |
| page225 | 602 | 6.01 | 100.1 | 0.60 s | 2 | 100 |
| top_100 | 425 | 4.24 | 100.2 | 0.60 s | 2 | 100 |

- Status codes: 2,852 of 2,852 were **200**. No 404, 429, 5xx or network error.
- The API sent **no rate-limit headers** (`X-RateLimit-*`, `Retry-After`) on any response.
- One gap of 1.81 s occurred (group `page150`), after an episodes call and before the next anime's first call. Its cause was not identified; it is probably local work (writing the result) between anime. A wider gap is harmless.

### Latency and size per call

| Endpoint | Calls | Median | p95 | Max | Avg response | Max response |
|---|---|---|---|---|---|---|
| `/anime` (list, 100 per page) | 10 | 0.15 s | 0.25 s | 0.26 s | 356.6 KB | 511.2 KB |
| `/anime/{id}/full` | 700 | 0.05 s | 0.05 s | 0.09 s | 6.1 KB | 88.1 KB |
| `/anime/{id}/statistics` | 700 | 0.05 s | 0.06 s | 0.09 s | 0.5 KB | 0.6 KB |
| `/anime/{id}/reviews` (page 1) | 700 | 0.05 s | 0.08 s | 0.28 s | 38.1 KB | 239.8 KB |
| `/anime/{id}/episodes` (all pages) | 742 | 0.05 s | 0.06 s | 0.22 s | 15.8 KB | 234.8 KB |

Sizes are decoded response bodies, not the gzip size on the wire.

**Caching.** Responses carry an `x-nginx-cache` header: 714 HIT, 1,944 MISS, 194 STALE. Median latency is 0.04 s for HIT and 0.05 s for MISS, so caching does not distort the latency numbers. Whether a cache HIT still uses quota is not known.

### Cost per anime

| Group | Anime | Avg calls | Max calls | Avg size | Max size | Anime with >1 episode page | Max episode pages |
|---|---|---|---|---|---|---|---|
| page1 | 150 | 4.11 | 15 | 104.9 KB | 1,771.8 KB | 3% | 12 |
| page75 | 150 | 4.02 | 6 | 28.3 KB | 227.9 KB | 1% | 3 |
| page150 | 150 | 4.00 | 4 | 14.4 KB | 129.3 KB | 0% | 1 |
| page225 | 150 | 4.00 | 4 | 18.4 KB | 123.6 KB | 0% | 1 |
| top_100 | 100 | 4.23 | 15 | 181.5 KB | 1,771.8 KB | 7% | 12 |
| **Random groups** | 600 | **4.03** | | **41.5 KB** | | | |

- The most expensive anime is mal_id 21 (One Piece): 15 calls, 12 episode pages, 1.77 MB. Long-running shows add a few extra calls; they are rare.
- Within an anime, the four calls take about 1.8 s from first to last request; with the 0.6 s gap before the next anime, one anime occupies about 2.4 s of the budget.

### What the data looks like

**Fields.** The four resources carry what the data model needs:

- `/full`: titles and synonyms, type, status, `aired`, `season`, `year`, `episodes`, `score`, `scored_by`, `members`, `rank`, `popularity`, `synopsis`, `genres`/`themes`/`demographics`, `studios`, `producers`, `images`, and **`relations`**, which the list endpoint does not return (nor `external`, `streaming`, `moreinfo`). The pipeline therefore needs `/full` per anime, not just the list.
- `/statistics`: `watching`, `completed`, `on_hold`, `dropped`, `plan_to_watch`, `total`, and `scores` (the score-vote distribution).
- `/reviews`: `mal_id` (a stable review ID), `score`, `review`, `date`, `tags`, `is_spoiler`, `is_preliminary`, `episodes_watched`, `reactions` (`overall`, `nice`, `love_it`, `funny`, ...), `user`.
- `/episodes`: `mal_id`, `title`, `aired`, `score`, `filler`, `recap`, `synopsis`.

**Reviews.**
- The first page is **already ordered most helpful first**: in all 368 anime with three or more reviews, reviews are in descending order of `reactions.overall`. No sort parameter is needed.
- Up to **20 reviews** per page (average 8.6 per anime); 265 of the 700 anime (38%) have none.
- Review IDs are stable. 6,034 review rows hold 5,894 distinct IDs; the 140 duplicates are consistent with the 8 anime fetched in two groups.

**Episodes.**
- 11,468 episodes in the sample; none has a null score, and the scale is **0-5**.
- **335 of them (2.9%) score 0**, which is most likely "no score yet". Effective score coverage is about 97%.
- **54% of anime (379 of 700) have an empty episode list.** By type: Movie 87 of 88, Music 89 of 89, PV 7 of 7, CM 6 of 6, ONA 75 of 85, Special 31 of 33, TV Special 20 of 22, OVA 30 of 44, TV 34 of 326. We still request episodes for every anime so as not to miss data; the cost of that choice is below.

**Catalogue contents.**
- Ratings in the sample: PG-13 388, G 132, R-17+ 98, PG 44, R+ Mild Nudity 38. No Rx (hentai) and no unrated titles.
- Types in the sample: TV 326, Music 89, Movie 88, ONA 85, OVA 44, Special 33, TV Special 22, PV 7, CM 6. The rating filter does **not** remove **Music, PV and CM** (102 of 700 anime, 14.6%), which the catalogue excludes.
- Unfiltered, the list has 30,650 entries; with the rating filter 28,497. The 2,153 removed are Rx titles and probably unrated ones; the split is not measured.

**Images.** Image URLs are on `cdn.myanimelist.net`. Hotlinking from another origin was not tested.

### Reviews on page 1 (re-analysed from the probe results, 17 Oct 2026)

Computed from the git-ignored `probe_results_*.jsonl` (692 distinct anime, 6,034 reviews). These are measurements on the sample, which is 99% finished anime.

| Measure | Value |
|---|---|
| Anime with no reviews | 264 of 692 (38.2%) |
| Anime whose page 1 is full (20 reviews) | 241 of 692 (34.8%) |
| Average reviews per anime | 8.5 (13.8 among anime that have any) |
| Reviews with `is_spoiler = true` | 0 of 6,034 |
| Reviews with `is_preliminary = true` | 0 of 6,034 |
| Review text length | median 2,660 chars, p95 10,132, max 61,839 |
| Most common years among the top 3 reviews of an anime | 2008 (158), 2014 (125), 2015 (99), 2007 (87), 2024 (73) |

- No spoiler or preliminary review appears, so the server excludes them by default. No `spoilers` parameter was sent, and `spoilers=true` has not been tested. See ADR 0014.
- The most helpful reviews skew old, because reactions accumulate over time. Page 1 says little about recent opinion.

### Projections from the probe

These extrapolate the sample to the catalogue; they are estimates, not measurements.

| Quantity | Calculation | Estimate |
|---|---|---|
| Backfill requests | 28,497 x 4.03 + 285 list pages | about 115,000 |
| Backfill time at 100/min | 115,000 / 100 / 60 | about 19 hours |
| Backfill time if Music/CM/PV are excluded (about 14.6% of anime in the sample) | about 24,300 anime | about 16 hours |
| If episodes are skipped for types that return empty lists | removes most of the 54% empty calls | saves about 2-3 hours |
| Raw layer size | 28,497 x 20-41 KB per anime | 0.55-1.13 GB |
| Monthly refresh (this year's anime + top ~1,000, ~6,000 anime, 3 calls each) | 18,000 requests | about 3 hours |
| Weekly refresh (Active tier, ~300 anime, ~4.2 calls each) | about 1,300 requests | about 13 minutes |

The raw size range comes from the sample: 41.5 KB per anime is the average of all four groups and is pulled up by `page1`, which holds long-running, heavily reviewed classics; 20 KB is the average of the other three groups. The real figure will come from the first full run.

### Decisions this evidence supports

1. **Run at 100 requests/min.** It is 83% of the secondhand 120/min limit, peaks at 2 requests/s against a 4/s cap, and ran clean for 29 minutes.
2. **No concurrency is needed.** One request takes about 0.05 s against a 0.6 s budget per request, so a single sequential worker spends only about 8% of each interval waiting on the API. Concurrent workers could not go faster than the limiter allows.
3. **Use an in-memory spacing limiter, not a Redis one** (ADR 0012). With one worker there is nothing to share, and an Airflow pool with one slot for Tenrai tasks keeps the weekly and monthly jobs from overlapping. A shared Redis limiter is only needed if a second process has to call Tenrai.
3. **Reviews: fetch page 1 only, no sort parameter.** Page 1 is already ordered by helpfulness.
4. **Fetch `/full` for every anime.** Relations are not in the list endpoint.
5. **Filter Music, PV and CM out of the catalogue.** They are 14.6% of the sample and nearly all have empty episode lists.
6. **Episode scores are 0-5 and 0 means missing.** The parser must treat 0 as null.

### Open questions

| Question | Why it matters | Next step |
|---|---|---|
| What is the real rate ceiling, and what does a 429 look like (`Retry-After`, recovery time)? | No 429 was triggered. 100/min is validated for 29 minutes, not a 19-hour run. | Watch the first long run with the circuit breaker on; run a deliberate controlled test if needed. |
| Does the rating filter drop unrated titles, including upcoming anime? | Not-yet-aired anime often have no rating, and the weekly Active tier depends on them. | Compare counts with `rating=rx` and a query for unrated titles. |
| How to filter by type at the list level? | Avoids spending requests on Music/PV/CM. | Test the `type` parameter (one query per type) against filtering after the list call. |
| How do airing and not-yet-aired anime behave? | The sample was 99% finished anime. | Probe a sample of airing and upcoming anime. |
| What happens on a 404 (`gone` handling)? | No 404 was seen. | Request an ID that does not exist. |
| Do MAL image URLs load from another origin? | Decides hotlink vs. downloading to S3. | Test in a browser from a different origin. |
| Tenrai terms of use, key requirement, uptime, bulk-ID endpoints | The data source is a third-party service. | Read the terms; note the fallback of self-hosting Jikan. |
| Do list items match `/full` for shared fields? | Decides whether the list pass can supply any fields. | Compare a sample. |
| Does paging reach the last list page (285)? | Confirms discovery covers the whole catalogue. | Fetch the last page. |

### Reproduce

The probe notebook is `analysis/tenrai_probe/jikan_ingestion.ipynb`. It writes `probe_metrics_*.json` (one row per request) and `probe_results_*.jsonl` (one line per anime) next to itself; both are git-ignored. The analysis cells read those files and generate the tables above. The raw files are not committed because they hold third-party API data.

## 2. Task enqueue when the Celery broker is down, 17 Oct 2026

How long `enqueue()` (a `.delay()` in a worker thread) takes to give up with a 503 when Redis is unreachable. Measured on the developer's Windows machine with no Redis running, three runs per case, `uv run python` against the real Celery app. Broker settings: `broker_connection_timeout=2`, socket connect/read timeouts 2s, publish retry `max_retries=2` (interval 0 to 0.2s).

| Case | Before `task_ignore_result=True` | After |
|---|---|---|
| Broker port closed (127.0.0.1:1) | Did not return in over 180s (killed) | 503 after 2.2s (2.23, 2.23, 2.20) |
| Broker host unroutable (10.255.255.1) | Not measured separately | 503 after 2.2s (2.20, 2.21, 2.22) |

**Cause of the hang:** every `.delay()` made the result backend subscribe to the result channel. With Redis down, the backend's own reconnect loop (long default retry policy) blocked the call, so the publish retry policy never mattered. The app never reads task results, so results are now ignored.

**Why 2.2s and not three attempts:** kombu runs the publish retry with an overall time budget (`timeout=2`, from `broker_connection_timeout`). In both cases the first attempt used the whole budget (2.01s), so kombu's `retry_over_time` raised without sleeping or retrying (it checks `time() > end` after a failed attempt). The remaining ~0.2s is overhead outside the attempt (thread start, connection setup); it was not investigated. `max_retries=2` only applies when attempts fail faster than the 2s budget. A closed port takes about 2s to fail on Windows; on Linux it should be refused immediately, which would allow the retries, but that was not measured.

Reproduce: point `REDIS_BROKER_URL` at the target and time `await enqueue(send_email_verification_task, "a@example.com", "code")`.

## 3. Cleaning the raw layer and the claim query (issue #24), 18-20 Oct 2026

### Parser speed
Micro-benchmark on a development laptop, on the 700 anime of the probe sample (657 have a synopsis, 574 bytes on average):

| Step | Time |
|---|---|
| `clean_full` for one anime | 54 µs |
| SHA-256 of one synopsis | 1.2 µs |
| All 26,000 anime | about 1.4 s, of which hashing is about 31 ms |

Parsing is not the bottleneck: reading the JSON and writing the rows is. The parser runs inline, not in a thread pool.

### What the probe sample looks like to the parsers
Counted over the same 700 anime and their episodes, statistics and reviews:

| Finding | Count |
|---|---|
| Anime with no synopsis (null) | 43 of 700 (6.1%) |
| Synopses ending in `[Written by MAL Rewrite]` | 283 of 700 (40.4%) |
| Anime with no `year` | 30 of 700 (4.3%); 29 of them have it in `aired.prop.from.year` |
| Anime with no score | 98 of 700 (14.0%) |
| Duration formats | 8 shapes (`24 min per ep`, `1 hr 55 min`, `45 sec`, `Unknown`, ...) |
| Anime with no studio | 174 of 700 (24.9%) |
| Distinct tag ids / studio ids | 74 / 187 (no id has two names) |
| Anime with no relations | 153 of 700 (21.9%) |
| Relation edges to an anime / to a manga | 1,517 / 466 |
| Anime edges whose target is in the sample | 202 of 1,517 (13.3%), which is why the target has no foreign key |
| Most anime relations on one anime | 92 |
| Anime with episodes / episodes in all | 321 / 11,468 |
| Episodes with score 0 (no score) | 335 (2.9%) |
| Episodes with no duration / no air date / no synopsis | 2,119 (18.5%) / 970 (8.5%) / 2,212 (19.3%) |
| Anime whose episode numbers are not 1..n | 2 (a gap at 45, and one that starts at 0) |
| Reviews (on 435 anime) / with HTML entities | 6,034 / 125 (2.1%) |
| Text fields with HTML entities: anime / episodes | 0 of 1,541 / 16 of 20,724 (0.08%) |

### Discovery on the real API, 18 Oct 2026
Two list pages with `type=tv,movie,ova,special,ona,tv_special`: 200 anime seen, 0 skipped by the type safety net, 800 tasks inserted (four per anime). The `type` parameter takes a comma-separated list: `type=Music, CM, PV` returned only those types, in any case and with spaces.

### Claim query against a large task table (synthetic)
`claim_next` takes the first claimable task in `(mal_id, resource)` order. Finished tasks pile up at the start of that order, so the scan has to skip more of them each time. Measured with `EXPLAIN ANALYZE` on a synthetic table of 115,988 tasks (28,997 anime × 4), local Postgres 16 with everything in memory, one run each. **This is not the ADR 0015 measurement**: it is a synthetic table on a laptop, not the real backfill on the real database. That one is done in #25.

| Index in place | Start (all pending) | Middle (50% done) | End (95% done) | Index size | Marking 100,000 tasks done |
|---|---|---|---|---|---|
| Primary key only | 0.06 ms | 23.9 ms | 33.5 ms | none | 743 ms |
| `(status, next_attempt_at)` | 0.08 ms | 16.6 ms | 33.8 ms | 4.6 MB | 966 ms |
| Partial `(mal_id, resource)` where status is pending or failed | 0.06 ms | 0.07 ms | 0.24 ms | 9.6 MB* | 682 ms |

- Without the partial index the plan was an index scan on the primary key that removed 58,000 rows by filter in the middle and 110,000 at the end.
- The planner **did not use** `(status, next_attempt_at)`: its plan stayed on the primary key scan. The query orders by `(mal_id, resource)`, which that index cannot provide.
- The partial index holds only unfinished tasks, in the order the query wants, so finished tasks cost nothing.
- *The size is after this test's bulk updates; the same index built when 95% of the tasks were done was 208 kB.
- The worker waits about 0.6 s per task for the rate limiter, so a claim of 30 ms most likely fits inside that wait and does not slow the backfill. Not measured on a real run.


## 4. Clean job batch size, per-row versus batched writes (issue #25), 24 Oct 2026

**Question.** The clean job reads `batch_size` raw rows, parses them, writes them with bulk statements and commits. How much does the batch size matter, and where do the gains stop?

**Method.** `analysis/bench_clean_batch_size.py` runs the real `CleanAnimeJob.full_clean_loop(full_pass=True)` over a fixed sample of **500 anime = 2,000 raw rows** (500 per resource; anime chosen by `md5(mal_id)` order, so the sample is repeatable; 11.1 MB of JSON), at batch sizes 1, 10, 50, 100, 250, 500 and 1000. The responses are loaded into memory first, so S3 is not part of the timing. All writes go to **temporary tables** that shadow the real ones on one pinned connection, so nothing in the real catalogue changed. Statements are counted with SQLAlchemy's `before_cursor_execute` event and commits with its `commit` event. Every run produced the same rows (500 anime, 2,219 episodes, 1,858 reviews, 0 parse failures), so the runs did identical work. One run per cell, on RDS and locally; not repeated, except the temporary-versus-real comparison below (four runs each).

### Against RDS (db.t4g.micro, London, from the developer's laptop; round trip 16.6 ms median)
| Batch size | Statements | Commits | Statements per row | Round trips per row | Seconds | ms per row |
|---|---|---|---|---|---|---|
| 1 | 8,900 | 2,000 | 4.450 | 5.450 | 228.8 | 114.4 |
| 10 | 2,898 | 200 | 1.449 | 1.549 | 64.4 | 32.2 |
| 50 | 654 | 40 | 0.327 | 0.347 | 21.2 | 10.6 |
| 100 | 336 | 20 | 0.168 | 0.178 | 15.3 | 7.7 |
| 250 | 140 | 8 | 0.070 | 0.074 | 9.0 | 4.5 |
| **500** | 74 | 4 | **0.037** | 0.039 | 4.8 | **2.4** |
| 1000 | 45 | 2 | 0.022 | 0.024 | 3.8 | 1.9 |

### Against local Postgres 16 (Docker; round trip 1.0 ms)
| Batch size | Statements per row | ms per row |
|---|---|---|
| 1 | 4.450 | 14.8 |
| 10 | 1.449 | 6.1 |
| 50 | 0.327 | 3.5 |
| 100 | 0.168 | 2.9 |
| 250 | 0.070 | 2.2 |
| 500 | 0.037 | 1.7 |
| 1000 | 0.022 | 1.5 |

### What it shows
- **Batch 1 to 500 on RDS:** statements per row 4.450 to 0.037 (**-99.2%, 120x fewer**); time per row 114.4 ms to 2.4 ms (**-97.9%, 47x**).
- **It is latency bound.** At batch size 1, 5.45 round trips x 16.6 ms is about 90 ms of the 114 ms per row (79%) spent waiting on the network. The same change gains less locally (14.8 to 1.7 ms, -88.5%), where a round trip costs 1 ms. At batch 500 the network share is about 27% of the time.
- **Extrapolation to the whole catalogue (92,236 rows), database writes only:** batch 1 about **2.9 hours**; batch 500 about **3.7 minutes**. The batch-1 figure leaves out S3 reads, which would be sequential at batch size 1, so the true per-row time would be higher.
- **Gains flatten past 500 in absolute terms.** 500 to 1000 saves 0.5 ms per row (-22%), about 49 s on 92,236 rows, while a batch holds twice as many payloads in memory and a failed batch redoes twice as much. 500 stays the default.
- **The real backfill's writes were slower than the benchmark.** The measured clean (22 Oct, against RDS from the same laptop) took 822 s in total: 414.5 s of writes (**4.49 ms per row**) and 405.7 s of reads (S3 payloads plus the index query, 4.40 ms per row; the benchmark leaves S3 out). Writes alone are **1.86x** the benchmark's 2.42 ms per row at batch 500, so the benchmark cannot predict absolute production times. What was checked:
  - **Temporary versus real tables** (no write-ahead log, no foreign keys): measured on the local test database, batch 500, four runs each: median 1.49 ms per row (temporary) against 1.71 ms (real), **+15%**.
  - **Sample weight:** the sample has 4.44 episodes and 3.72 reviews per anime against 4.86 and 3.79 for the whole catalogue, so it is about 9% lighter on episodes.
  - **Still unexplained, about 1.5x.** Not tested: RDS write cost on its network-attached storage, CPU-credit throttling on the t4g.micro (the balance was about 1 when checked), larger tables and indexes by the end of the run, and the Airflow container's slower client side compared with running natively on the laptop.

  The batch-size **ratios** (statements per row, relative time) are the result to quote. Absolute times are only trustworthy for the real run.

**Not measured.** The 2.9 h figure is an extrapolation from a 2,000-row sample, not a run of the whole catalogue at batch size 1. Batch size 1 is the same code with one raw row per batch, not a naive one-INSERT-per-child-row implementation, which would use more statements still.

Reproduce: build the sample with `sample`, then `run <name> [sizes]` with `BENCH_DIR` and `BENCH_URL` set. `BENCH_REAL=1` writes to the real tables instead of temporary ones, and only runs on a database whose name contains "test". See the docstring in the script.

## 5. The first full backfill and the first refresh runs (issue #25), 22-24 Oct 2026

Run from a developer laptop (Airflow in Docker, `ingestion_one_pass` DAG, run id `backfill_1_2026-10-22`) against Tenrai, S3 (eu-west-2) and RDS (db.t4g.micro, London). All figures come from the run summaries (`ingestion_run`) and the Airflow task logs.

### Backfill (22 Oct)
| | Result |
|---|---|
| Discovery | 231 list pages, 139.3 s, 23,059 anime, **92,236 tasks** created (4 per anime); 99.5 requests/min; 0 rate-limited |
| Fetch | **15 h 26 min** (55,572 s), 92,236 tasks, 92,394 requests (4.007 calls per anime; 158 extra episode pages), **99.755 requests/min**, **0 rate-limited, 0 retries, 0 failed, 0 dead, 0 gone**, 92,236 of 92,236 done on the first try |
| Fetch time per task | average 602 ms; slowest task 10.4 s (a 12-page episode list) |
| Raw data stored in S3 | **179,554,931 bytes** (171 MiB) in 92,236 gzipped objects, 7.8 KB per anime |
| Clean | 822 s, 185 batches of 500 raw rows, 112 raw rows/s, **0 parse failures**; of the 822 s, 406 s were reads (S3 plus the index query) and 414 s were writes |
| Catalogue written | 23,059 anime, 112,058 episodes, 87,313 reviews, 23,059 statistics rows, 14,483 relation links, 1,165 studios, 78 tags, 246,732 rows in all |

Against the projections of ADR 0012: 23,059 anime instead of the projected 28,497 (the rating and type filters remove Rx, music, PV and CM), 4.007 calls per anime against 4.03, and 15.4 h against about 19 h. The shorter run comes from the smaller catalogue, not from a faster pipeline. **The gate in ADR 0012 is met** (zero 429s over a full run at 100 requests/min), so the rate may now be raised to 110/min.

### Raw size against the probe
ADR 0012 projected about 41.5 KB of uncompressed JSON per anime. The stored objects average 7.8 KB per anime compressed, **81% smaller**. (Different measures: the projection was uncompressed raw JSON.)

### Episode coverage
23,062 anime hold 112,065 episode rows, against 399,300 announced in the anime records. 5,874 anime (25%) have any episode entry at Tenrai: 51% of TV anime, 14% of ONA, and almost no movies, OVAs or specials (31 of 4,954 movies). A check on 300 random anime and on One Piece (1,180 episodes, 12 pages) found the raw data and the table identical, so the number reflects the source and not a parsing loss.

### Claim query on the real table (96% of tasks done)
`EXPLAIN ANALYZE` of the claim query on `ingestion_task` (92,248 rows, 2,554 pending): **21.6 ms**, an index scan on the primary key that removes 88,662 rows by filter. The partial index measured on the synthetic table in section 3 (0.24 ms at the same stage) was **not added**: at 0.6 s per task the worker never waits on it. ADR 0015 still applies.

### First refresh runs (24 Oct, after unpausing the weekly and monthly DAGs)
| | Result |
|---|---|
| Monthly lists | `recent_years`: 14 list requests, 8.1 s; `popular`: 5 requests, 3.0 s (stopped at the 5-page cap, 500 anime) |
| Weekly lists | `airing`: 3 requests, 1.5 s; `upcoming`: 3 requests, 1.6 s |
| Fetch | **6,746 tasks in 4,093 s (68 min)**, 6,798 requests, 31,656,805 bytes stored, 0 rate-limited, 0 failed. That is **7.3% of the backfill's tasks** (-92.7%) and 7.4% of its time (-92.6%). The monthly fetch took all the pending tasks, including those the weekly discovery had reset |
| Clean | incremental: **6,746 raw rows parsed** (not 92,236), 2,075 anime changed, 0 parse failures, 113 s |
| Defect found | The two clean tasks ran at the same time (they were outside the pool). Fixed by putting clean in the one-slot pool |

`requests_per_minute` above 100 on very short runs (101 to 121 on the discovery lists) is an artefact: the first request has no wait, so a run of two or three requests overstates the rate. Judge the rate only over runs longer than a minute.
