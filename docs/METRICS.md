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

## 6. The first IMDb download and archive (issue #40), 3 Nov 2026

Run from a developer laptop (Airflow in Docker, `imdb_weekly` DAG, task `download`) against `datasets.imdbws.com`, S3 and a local Postgres. The figures come from the task log and the `imdb_snapshot` row. This is a first run, so there is nothing to compare against. The download and upload times are from a home connection and say little about the Oracle box. `download_seconds` includes the gzip integrity check.

| File | Bytes (compressed) | MiB | Download | Upload |
|---|---|---|---|---|
| `title.basics` | 229,636,410 | 219.0 | 18.6 s | 16.38 s |
| `title.episode` | 55,297,201 | 52.7 | 4.62 s | 6.05 s |
| `title.ratings` | 8,733,444 | 8.3 | 0.44 s | 0.81 s |
| **Total** | **293,667,055** | **280.1** | 23.7 s | 23.2 s |

The whole run took **47.6 s**. The three objects are the snapshot's size in S3: 293,667,055 bytes under `imdb/2026-11-03T052847Z/`, and the `imdb_snapshot` row holds the same three sizes and the ETag of each file.

Second run, straight after (the files had not changed): the three `HEAD` requests matched the ETags of the archived snapshot, so the run recorded a `skipped` row, downloaded and uploaded nothing, and exited with code 99 in **0.33 s**.

## 7. Per-anime statistics endpoint, service timing and query plans (issue #51), 17 Nov 2026

**Setup.** `analysis/bench_anime_statistics.py` runs the real `StatisticsService` (no HTTP layer) against RDS (db.t4g.micro, London) from the developer's laptop on a pooled connection. Read-only. Round trip `SELECT 1`: **16.0 ms median** (15.5 to 18.1). Tables: 23,086 anime, 23,086 statistics rows, 87,493 reviews, 68,081 `anime_tag`, 16,922 `anime_studio`. One warm-up call, then 15 timed calls per anime, one batch of runs, not repeated. The service makes **5 queries**, and `pool_pre_ping` adds one more round trip when a connection is checked out, so the first query shows about 2 round trips.

**Service time per request, from the laptop (includes about 6 round trips of 16 ms)**

| Anime | Response | Service median | p95 | Max | anime+stats / tags / studios / reviews / cohort (median ms) |
|---|---|---|---|---|---|
| Most popular in the biggest cohort (31964) | 102.5 KiB | **301.7 ms** | 338.8 | 439.2 | 32.0 / 16.0 / 16.1 / 153.0 / 26.8 |
| Least popular in the biggest cohort (64594) | 2.2 KiB | 196.3 ms | 320.9 | 322.4 | 32.0 / 16.0 / 15.7 / 45.6 / 26.7 |
| Most reviews (61990) | 70.1 KiB | 185.5 ms | 196.1 | 196.5 | 31.8 / 16.0 / 16.0 / 41.8 / 25.2 |
| A cohort under 30 (80) | 110.1 KiB | 185.7 ms | 198.1 | 300.0 | 32.2 / 15.8 / 16.5 / 43.6 / 23.9 |

JSON serialisation is 0.1 to 0.5 ms. The 153 ms reviews figure for the first anime is **unexplained**: it was the first sample run and was not repeated, and the other three anime with similarly large responses took 42 to 46 ms.

**`EXPLAIN (ANALYZE, BUFFERS)` on the real tables, anime 31964 (cohort: TV 2016, 378 anime)**

| Query | Execution time | Plan |
|---|---|---|
| anime + statistics | 0.074 ms | two primary-key index scans |
| tags | 0.116 ms | `anime_tag` primary key, hash join to `tag` |
| studios | 0.051 ms | `anime_studio` primary key, nested loop |
| reviews | **23.127 ms** | `Seq Scan on review`, 87,473 rows removed by the filter, 11,633 buffers |
| cohort | **11.460 ms** | `Seq Scan on anime` (7.5 ms of it) for `type = 'TV' AND year = 2016`, 378 rows, 2,425 buffers, then the window functions |

The database spends about **35 ms** on the five queries. The rest of the 185 to 300 ms from the laptop is network.

**Candidate indexes (ADR 0015).** `analysis/bench_statistics_indexes.py` copies `review` and `anime` into temporary tables on one connection, builds the index on the copy, and re-runs the exact statements the service sends, median of 9 `EXPLAIN ANALYZE` runs. The temporary copies are not comparable to the real tables for the reviews query: with no index the copy took 636 ms (temporary tables use small session-local buffers, so an 81.5 MiB scan goes to disk) against 23.1 ms on the real table. Only the cohort baseline matched (13.5 ms on the copy against 11.5 ms real).

| Index | Query | Before (real table) | After (temporary copy) | Index size |
|---|---|---|---|---|
| `review (anime_id)` | reviews | 23.1 ms | **0.06 to 0.07 ms** | 824 KiB (table 81.5 MiB) |
| `anime (type, year)` | cohort | 11.5 ms (copy 13.5 / 11.75) | **3.79 ms** (31964), **2.13 ms** (61990) | 184 KiB (table 17.0 MiB) |

**Not measured:** the write cost of either index (ADR 0015 asks for insert and update throughput with and without), so neither index has been added. Timings from inside the same VPC. Behaviour under concurrent load. A Redis cache hit.

**What this says about caching.** From the laptop, a request is 185 to 300 ms and most of it is round trips, which a deployed app next to RDS would not pay. The database's own work is about 35 ms, of which 34 ms is two sequential scans that the two indexes would turn into well under 1 ms and about 2 to 4 ms. That is a decision for the load test (#55), measured from the deployed app.

## 8. Per-season "gets good" verdict, thresholds checked on real series (issue #52), 17 Nov 2026

**Method.** `analysis/check_trend_thresholds.py` runs `compute_trend` (`app/statistics/trend.py`) over every anime on RDS that has a scored episode: **5,626 anime, 92,058 scored episodes** (112,257 episode rows, 20,199 with a null score). Read-only, one run per setting. 4,588 anime have the 6 scored episodes the verdict needs; 1,038 have 1 to 5 and get `insufficient_data`. Constants at the time: baseline = first 20% of scored episodes (at least 3, no cap), threshold = baseline + 0.5, moving average over 5% of the scored episodes (at least 3, odd), up to 10% of the points after the turning point may dip, `consistently_good` = baseline at least 7.5 and every smoothed point within 0.5, `declines` = last 20% of scored episodes at least 0.5 below the baseline.

**First run, turning point allowed anywhere after the baseline:**

| Verdict | Anime | Share |
|---|---|---|
| flat | 1,420 | 25.2% |
| gets_good | 1,407 | 25.0% |
| consistently_good | 1,392 | 24.7% |
| insufficient_data | 1,038 | 18.5% |
| declines | 369 | 6.6% |

The `gets_good` turning point sat at a median of **0.67** of the way through the series (quartiles 0.38 / 0.86), and Cowboy Bebop turned at episode 24 of 26 and Code Geass at 24 of 25. A smoothed score that ends high "stays" above the line trivially, so a good finale alone counted as "gets good".

**Change:** the turning point must leave at least as many episodes after it as the baseline has (`min_after = baseline size`). Second run:

| Verdict | Anime | Share |
|---|---|---|
| flat | 1,928 | 34.3% |
| consistently_good | 1,392 | 24.7% |
| insufficient_data | 1,038 | 18.5% |
| gets_good | 886 | **15.7%** |
| declines | 382 | 6.8% |

The turning point now sits at a median of **0.48** of the series (quartiles 0.27 / 0.67), with a median rise of +0.82 over the baseline. By length, `gets_good` is 16% for 1 to 13 scored episodes, 18% for 14 to 26, 12% for 27 to 100 and 5% for 100 or more; the 100+ group (61 anime) is 79% `flat`.

**Well-known series (second run)**

| Series | Scored | Verdict | Baseline (episodes) | End mean | Turning point |
|---|---|---|---|---|---|
| Steins;Gate | 24 | gets_good | 8.65 (5) | 9.56 | 8 |
| Fullmetal Alchemist: Brotherhood | 64 | gets_good | 8.26 (13) | 9.10 | 40 |
| Mob Psycho 100 | 12 | gets_good | 8.55 (3) | 9.28 | 8 |
| Shingeki no Kyojin (S1) | 25 | consistently_good | 9.13 (5) | 9.37 | none |
| Vinland Saga | 24 | consistently_good | 8.92 (5) | 9.34 | none |
| Naruto | 220 | declines | 8.32 (44) | 7.58 | none |
| One Piece | 1,176 | flat | 8.55 (235) | 8.92 | none |
| Bleach | 366 | flat | 8.55 (73) | 8.28 | none |
| Death Note | 35 | flat | 8.94 (7) | 8.71 | none |
| Cowboy Bebop | 26 | flat (was gets_good, turning 24) | 8.64 (5) | 9.17 | none |
| Code Geass | 21 | flat (was gets_good, turning 24) | 8.65 (4) | 9.10 | none |
| Hunter x Hunter (2011) | 148 | flat (was gets_good, turning 123) | 8.51 (30) | 9.18 | none |

**Findings and limits.**
- The verdicts match what is commonly said about Steins;Gate, Brotherhood and Naruto, but this is a spot check by the author, not a labelled test set, so there is no precision figure.
- A series that is only stronger in its last fifth (Hunter x Hunter, Cowboy Bebop, Code Geass) is now `flat`. Their `end_mean` is returned with the baseline, so the gap is visible, but the verdict does not say it.
- With a baseline of 20% and no cap, a long series is mostly `flat` (One Piece's baseline is its first 235 episodes). For series over 200 episodes the `segments` block (200 episodes each) carries the information the verdict cannot.
- Episode scores are coarse and missing for about 18% of the rows, so a verdict on a short season rests on few points.

## 9. Cohort over the years an anime aired (issue #52), 17 Nov 2026

The cohort used to be "same `type` and same start `year`", so One Piece (1999 to now, still airing) was compared with 110 TV anime from 1999. It is now every anime of the same `type` that started between the anime's start year and the year its run ended (the year of `aired_to`, the current year while it is airing, otherwise the start year). A one-season show still gets a single year.

`analysis/bench_cohort_range.py` runs the real repository query on RDS (db.t4g.micro, London, from the developer's laptop, round trip about 16 ms). Both indexes from section 7 (`ix_anime_type_year`, `ix_review_anime_id`) were present on RDS, so these are with them. Warm-up, then 15 timed runs for the round trip and the median of 5 `EXPLAIN ANALYZE` runs for the database execution time; one batch of runs, not repeated. All five plans were a bitmap heap scan on `anime` using `ix_anime_type_year`.

| Anime | Years | Cohort | Query round trip | Database execution |
|---|---|---|---|---|
| Steins;Gate | 2011 | 322 | 20.7 ms | 3.46 ms |
| Boku no Hero Academia | 2016 | 378 | 21.5 ms | 3.94 ms |
| Naruto | 2002 to 2007 | 978 | 28.0 ms | 9.98 ms |
| Gintama | 2006 to 2010 | 1,195 | 28.2 ms | 11.82 ms |
| One Piece | 1999 to 2026 | 7,080 | 79.9 ms | 70.25 ms |

A one-season show is unchanged (the 3.5 to 3.9 ms matches the 2.1 to 3.8 ms measured on temporary copies in section 7). The cost grows with the cohort: One Piece's query sorts 7,080 rows through eight window functions and takes 70 ms in the database, about twice the whole endpoint's earlier database time. Its position is more meaningful than before: #37 of 7,080 on score (99th percentile) and #16 on popularity, against #1 of 110 before. This is the strongest case so far for caching the per-anime response (#55), since the long runners are the most visited and the slowest.

**Filler and recap flags (same day, RDS, one query).** Of 112,257 stored episodes across 5,880 anime, **2,741 (2.4%) are flagged filler** and **298 (0.27%) recap**; none is both. Only **176 anime** (3.0% of those with episodes) have any filler flag and 161 any recap flag, so the flags are rare and concentrated in long shonen series. Examples: One Piece 97 of 1,180 filler (8.2%) and 7 recap (0.6%), Naruto 89 of 220 filler (40.5%), Steins;Gate none. The flags come from MAL's episode list, so an unflagged episode is not proven canon.

**Cohort with drop-rate and completion-rate percentiles (same day, same method as above).** The cohort query now joins `anime_statistics_mal` and ranks five metrics (score, popularity, favorites per member, drop rate, completion rate) instead of three. The first version sorted every metric twice (a rank and a percent rank) and took **11.07 ms** for Steins;Gate and **152.12 ms** for One Piece in the database, against 3.46 ms and 70.25 ms with three metrics. The plan was an index scan on `anime` (0.3 ms for 322 rows), a hash join that reads all 23,086 statistics rows (about 2 ms), then the window sorts. Deriving the percentile from the rank, `(count - rank) / (count - 1)`, halves the sorts, and the query takes:

| Anime | Cohort | 3 metrics (section 9) | 5 metrics, two sorts each | **5 metrics, one sort each** |
|---|---|---|---|---|
| Steins;Gate | 322 | 3.46 ms | 11.07 ms | **8.58 ms** |
| Boku no Hero Academia | 378 | 3.94 ms | 12.52 ms | **9.40 ms** |
| Naruto | 978 | 9.98 ms | 24.69 ms | **17.99 ms** |
| Gintama | 1,195 | 11.82 ms | 28.42 ms | **20.31 ms** |
| One Piece | 7,080 | 70.25 ms | 152.12 ms | **105.12 ms** |

The two extra metrics cost about 5 ms for a one-season show and 35 ms for One Piece. Percentiles are unchanged for these anime; the difference is only in ties, where tied anime now share one rank and one percentile (the percentile is the share of the cohort not ranked above the anime, so ties count as beaten). The per-anime response is the next thing to cache or precompute (#55).

## 10. The anime list and the platform-wide aggregates (issue #53), 18 Nov 2026

**What was measured.** `analysis/bench_aggregates.py` runs the real repository queries (`app/catalogue/read_repository.py`) on RDS (db.t4g.micro, London, from the developer's laptop, round trip about 16 ms): one warm-up, 15 timed runs for the round trip, then the median of 5 `EXPLAIN ANALYZE` runs for each statement. Data: 23,086 anime, 68,081 `anime_tag` rows, 16,922 `anime_studio` rows. Every list request runs two statements (the count and the page), shown as `count + page`. One batch of runs, not repeated.

**Before any new index (only `ix_anime_type_year` and `ix_review_anime_id` from section 7 existed):**

| Request | Round trip | Database (ms) | Plan |
|---|---|---|---|
| list, all anime, by members | 71 ms | 5.7 + 18.5 | seq scan `anime` |
| list, by members, page 500 (offset 10,000) | 90 ms | 5.6 + 39.6 | seq scan |
| list, all anime, by title | 87 ms | 5.7 + 36.3 | seq scan |
| list, airing, by members | 63 ms | 6.5 + 6.7 | seq scan (271 rows match) |
| list, TV 2019, by score | 50 ms | 0.4 + 0.5 | bitmap scan on `ix_anime_type_year` |
| list, tag 1 (Action), by score | 80 ms | 15.3 + 16.9 | seq scan `anime`, seq scan `anime_tag` |
| list, tag 1 + year 2019, by score | 54 ms | 1.7 + 1.7 | bitmap scan on `ix_anime_type_year` |
| list, one studio, newest | 54 ms | 1.6 + 2.1 | seq scan `anime_studio` |
| **list, search "titan"** | **263 ms** | **105.8 + 106.1** | seq scan, six ILIKEs per row |
| **list, search "one piece"** | **272 ms** | **110.1 + 110.2** | seq scan |
| list, search "a" (5,058 matches) | 250 ms | 90.5 + 108.2 | seq scan |
| by-year | 53 ms | 22.9 | seq scan |
| by-year, TV only | 45 ms | 12.7 | bitmap scan on `ix_anime_type_year` |
| by-type | 54 ms | 24.6 | seq scan |
| by-season | 52 ms | 26.5 | seq scan |
| by-tag | 120 ms | 110.8 | seq scans on `anime_tag`, `anime`, `tag` |
| by-tag, TV 2015 to 2025 | 54 ms | 27.4 | bitmap scan on `ix_anime_type_year` |
| by-studio | 101 ms | 27.5 + 39.8 | seq scans on `anime_studio`, `anime`, `studio` |

**Search was the problem**: a search took 105 ms of database time per statement, 210 ms with the count, with `ILIKE` over three title columns and two patterns. Rewriting it as one `ILIKE` on the combined title text (the start of any word, in any of the three titles) took 49.8 ms for the same 25 matches (104.7 ms before), and `lower()` with `LIKE` 43.2 ms, `position()` 38.3 ms: all still scans, because ILIKE under `en_US.UTF-8` is slow on this CPU.

**Index candidates, measured on rolled-back temporary copies** (the method of section 7; the copies sit in small session-local buffers, so their no-index baselines are inflated against the real tables and only the before/after on the copy is meaningful). `CREATE EXTENSION pg_trgm` ran inside a transaction that was rolled back, so nothing was left installed on RDS; the app's database user was allowed to create it.

| Candidate | Query | Before (copy) | After (copy) | Index size | Added? |
|---|---|---|---|---|---|
| GIN trigram on the combined title text | search "titan" | 51.6 ms | **0.26 ms** | 8,896 KiB | **yes** |
| the same | search "one piece" | n/a | **0.47 ms** | | |
| the same | search "ti" (2 letters) | n/a | 1.77 ms | | |
| the same | search "a" (1 letter) | n/a | 19.6 ms | | |
| `anime (members_mal DESC NULLS LAST, mal_id)` | list by members, page query | 20.5 ms | **0.07 ms** | 528 KiB | **yes** |
| `anime (mal_score DESC NULLS LAST, mal_id)` | list by score, page query | 20.8 ms | **0.06 ms** | 728 KiB | **yes** |
| `anime (lower(title), mal_id)` | list by title, page query | 38.5 ms | 0.10 ms | 1,224 KiB | no (expression index; sorting by title stays at about 36 ms) |
| `anime_tag (tag_id)` | list, tag 1 by score | 15.3 + 17.5 ms | 15.3 + 17.5 ms (not used) | 488 KiB | **no**: Action is 20% of the table, so the planner scans |
| `anime_studio (studio_id)` | list, studio 1 newest | 11.9 + 12.1 ms | 10.8 + 11.0 ms | 160 KiB | **no**: about 1 ms |

No index was added for favorites or newest, which were not measured. Write cost of the three added indexes was not measured: `anime` is written only by the weekly and manual refreshes (hundreds of rows).

**After the migrations, checked on a throwaway local Postgres 16** (Docker, a copy of the 23,086 real titles, not RDS, so the milliseconds are not comparable with the table above; the point is that the application's own queries use the indexes, including with bound parameters):

| Request | Database (ms) | Plan |
|---|---|---|
| search "titan" | 0.84 + 0.81 | bitmap scan on `ix_anime_search_trgm` |
| search "one piece" | 0.92 + 1.15 | `ix_anime_search_trgm` |
| search "a" | 9.9 + 20.7 | `ix_anime_search_trgm`, 5,058 matches |
| search "zzzz" (no match) | 0.31 + 0.34 | `ix_anime_search_trgm` |
| list by members | 2.8 + 0.08 | index scan `ix_anime_members_mal` for the page |
| list by members, page 500 | 2.8 + 5.3 | |
| list by title | 2.6 + 20.8 | seq scan (no index, as decided) |

`alembic upgrade head`, `alembic downgrade b3d8f50e6c17` and `upgrade head` again, and `alembic check` all passed on that database. A first attempt that left the trigram index out of the model made `alembic check` fail with "Detected removed index", so the index is declared on the model (Alembic then reports it cannot compare an expression index and assumes it equal).

**Still to do:** re-measure on RDS after the migrations are applied (the migrate job runs when this merges), and fill in the real-table numbers for the list and search rows above.

### Release status and several-value filters (same day)

The list can now filter by whether an anime is really releasing episodes. MAL's status says "Currently Airing" for a show on a long break, so `release_status` looks at the air dates of the episodes: `releasing` = an episode aired in the last 10 days (an episode dated in the future is a schedule and does not count), `on_break` = airing, has released episodes, none in that window, `no_episode_data` = airing with no episode rows. Data on RDS on 18 Nov 2026: **271 airing anime, of which only 111 have any episode rows** (160 have none, so `no_episode_data`); **23 released an episode in the last 10 days**, 87 are on a break (One Piece's last episode, 1180, aired on 27 Sep), and 37 had one in the last 15 days. 112,257 episodes, 16,383 of them with no air date, 7 dated in the future.

Same method as above (RDS, round trip about 16 ms, median of 5 `EXPLAIN ANALYZE` runs, `count + page`), before the `episode (aired)` index:

| Request | Round trip | Database (ms) | Plan |
|---|---|---|---|
| list, airing + releasing, by members | 115 ms | 28.9 + 27.3 | seq scan `episode` |
| list, releasing only (no status), by members | 117 ms | 27.7 + 27.7 | seq scan `episode` |
| list, airing + on break | 83 ms | 12.5 + 8.7 | seq scan `anime` |
| list, no episode data | 74 ms | 7.4 + 7.6 | seq scan `anime` |
| list, airing, by latest episode | 68 ms | 6.4 + 11.6 | |
| list, all anime, by latest episode | 158 ms | 5.6 + **121.6** | correlated subquery for every anime |
| list, tags 1 AND 4 (1,140 match) | 88 ms | 13.0 + 14.6 | seq scan `anime_tag` |
| list, tags 1 OR 4 (11,121 match) | 99 ms | 19.4 + 36.4 | |
| the latest episode date of the 20 anime on a page | 18 ms | 1.2 | `episode_pkey` |

The `releasing` filter scanned all 112,257 episodes. On rolled-back temporary copies (the baselines on the copies are inflated, 414 ms against 28 ms on the real table, so only the after is compared with the real number):

| Candidate | `releasing` query | After (copy) | Index size | Added? |
|---|---|---|---|---|
| `episode (aired)` | 28 ms (real table) | **0.15 + 0.19 ms** | 1,208 KiB (the table's primary key is 2,488 KiB) | **yes** (`ix_episode_aired`) |
| `episode (anime_id, aired)` | 28 ms (real table) | 2.6 + 2.6 ms | 3,064 KiB | no |

Sorting **all** anime by their latest episode (121.6 ms) is not helped by either index (the copy went 3,170 ms to 3,168 ms with `(aired)` and to 1,700 ms with `(anime_id, aired)`); the sort is only meant to be used with a filter such as `status=airing` (6.4 + 11.6 ms), so it is documented as such and no index was added for it. Write cost of `ix_episode_aired` was not measured: `episode` is written only by the weekly and manual refreshes.

`alembic upgrade head`, `alembic check` and the new migration `f6c1e4a9b3d0` passed on a throwaway local Postgres, which was dropped afterwards.

### A `released_days` filter, built and removed, and the filter options (same day)

A `released_days=N` filter (released an episode in the last N calendar days, for a Sunday "this week's releases" view) was built and measured, then **removed** at the owner's decision: `release_status=releasing` (an episode in the last 10 days) is enough, and the weekly and seasonal episode charts were dropped because MAL's episode data is too patchy for them (episode rows exist for only 5,880 of 23,086 anime, 160 of the 271 airing anime have none, 16,383 of 112,257 episodes have no air date, and One Piece's episode response alone is 1,343 KiB). What it measured, which still applies to `release_status=releasing`, since both are an `EXISTS` on `episode.aired`: on RDS on Wed 18 Nov 2026 it returned 20 anime for 7 days and 35 for 14 (the same 20 as a direct SQL count), and on rolled-back temporary copies with `ix_episode_aired` the query took **0.13 + 0.35 ms** for 7 days and 0.33 + 0.39 ms for 30 days (`count + page`), against about 28 ms per statement without the index on the real table (the temporary copy's own no-index baseline, 403 ms, is inflated and not comparable). `ix_episode_aired` is 1,208 KiB and stays for `releasing`.

The two endpoints that fill the filter controls, `GET /filters` (every tag with its anime count, the year range and the fixed choices) and `GET /studios` (search and paginate), measured with the same method before any new index:

| Request | Round trip | Database (ms) | Plan |
|---|---|---|---|
| filters: the 78 tags with counts | 53 ms | 31.3 | seq scans on `anime_tag`, `tag` |
| filters: the year range | 41 ms | 9.1 | seq scan `anime` |
| studios, by anime count (1,165 match) | 62 ms | 8.7 + 9.1 | seq scans `anime_studio`, `studio` |
| studios, search "toei" | 165 ms | 4.8 + 4.8 | same |
| studios, search "a" | 58 ms | 5.8 + 6.1 | same |
| studios, by name, page 20 | 63 ms | 8.7 + 10.6 | same |

Both are cheap, so no Redis cache was added: `/filters` is the same for everyone and changes only when the catalogue refreshes, so it is sent with `Cache-Control: public, max-age=3600` and the browser or nginx can keep it for an hour. A server-side cache is a #55 decision.

**What this says about caching (#55).** After the indexes the list and search are cheap, but the aggregates are not and need no per-user input: by-tag takes 111 ms in the database and by-studio about 67 ms, by-year, by-type and by-season 23 to 27 ms. They change only when the catalogue refreshes, so they are the best candidates for a per-parameter cache.

## 11. Cache candidates and known inefficiencies (issues #51 to #53), 18 Nov 2026

`analysis/bench_endpoints.py` calls every public endpoint through the real app against RDS (db.t4g.micro, London, from the developer's laptop, round trip about 16 ms per statement): one warm-up, then the median of 5 calls, with the number of SQL statements each call runs and the size of the response. The "Database (ms)" column repeats the `EXPLAIN ANALYZE` execution times from sections 7, 9 and 10, which were measured **before** the new indexes were applied on RDS (they are applied when the migrations run on merge), so "after" figures are still to be taken on RDS. Everything from the laptop is dominated by round trips: a call with 4 statements costs about 4 x 16 ms before any database work.

| Request | Statements | From the laptop | Size | Database (ms), before the new indexes |
|---|---|---|---|---|
| list, default (20 anime) | 4 | 174 ms | 9.3 KiB | count 5.7 + page 18.5 |
| list, 100 anime | 4 | 182 ms | 47.6 KiB | not measured separately |
| list, airing + releasing, by members | 4 | 215 ms | 10.9 KiB | 28.9 + 27.3 |
| list, fall 2025, by score | 4 | 155 ms | 9.8 KiB | not measured separately |
| list, tag 1 (Action), by score | 4 | 196 ms | 9.8 KiB | 15.3 + 16.9 |
| list, search "titan" | 4 | 265 ms | 10.2 KiB | 105.8 + 106.1 (51.8 + 52.0 with the combined-text query, no index; 0.8 + 0.8 with the trigram index, on local Postgres) |
| list, sort by title | 4 | 193 ms | 8.8 KiB | 5.7 + 36.3 |
| `/filters` | 4 | 175 ms | 6.0 KiB | tags 31.3 + year range 9.1 |
| `/studios` (20) | 3 | 147 ms | 1.1 KiB | 8.7 + 9.1 |
| by-year | 2 | 141 ms | 11.7 KiB | 22.9 |
| by-type | 2 | 144 ms | 0.8 KiB | 24.6 |
| by-season | 2 | 139 ms | 0.5 KiB | 26.5 |
| by-tag | 2 | 219 ms | 10.2 KiB | **110.8** |
| by-studio (20) | 3 | 191 ms | 2.8 KiB | 27.5 + 39.8 |
| anime statistics, Steins;Gate | 5 | 187 ms | **123.8 KiB** | about 35 in all (section 7), the cohort query 3.5 to 8.6 |
| anime statistics, One Piece | 5 | 280 ms | 3.6 KiB (no reviews stored) | cohort query **105.1** |
| anime episodes, Steins;Gate | 2 | 125 ms | 30.9 KiB | a primary-key read |
| anime episodes, One Piece | 2 | 195 ms | **1,343 KiB** | a primary-key read |

Sizes over a random sample: the per-anime statistics response is **avg 11.0 KiB, median 3.0, p95 52.5, max 88.5** (60 anime), the episodes response **avg 12.9, median 7.7, p95 29.5, max 110.4** (40 anime with episodes). The statistics size is almost all reviews (up to 20 full texts).

**Inefficiencies known and not fixed.** The queries are not as fast as they could be. What is left, and what it would take:
1. **Sorting the whole catalogue by title takes 36 ms** (a scan and sort). Only an expression index would fix it, which was left out to avoid another expression index in the models.
2. **`sort=latest_episode` over all anime takes 121.6 ms** (a correlated subquery for every anime); it is fast only with a filter such as `status=airing` (6.4 + 11.6 ms). It is documented as such, not limited.
3. **by-tag takes 110.8 ms** and by-year, by-type and by-season 23 to 27 ms: they aggregate 23,086 anime (68,081 tag links for by-tag) on every call. An index does not help a full aggregation.
4. **by-studio and `/studios` run the grouped query twice** (once to count, once for the page): 27.5 + 39.8 ms and 8.7 + 9.1 ms. Counting with a window function in the same query would roughly halve it (an estimate, not measured).
5. **Statements per request**: every list runs 4 (`SET LOCAL statement_timeout`, count, page, the latest episode dates of the page), and `/filters` runs 4 because each of its two reads sets its own timeout. From a laptop each costs 16 ms; inside the VPC about 1 ms. The timeout could be set once per request.
6. **Deep offset pages**: offset 10,000 on the default list took 5.6 + 39.6 ms, against 5.7 + 18.5 ms for the first page.
7. **The per-anime statistics response has no cap on reviews** (123.8 KiB for Steins;Gate), and its cohort query takes 105 ms for a long runner (One Piece's cohort is 7,080 anime).
8. **Big tags are scanned**: a list filtered by Action (20% of the table) reads all of `anime_tag` (15.3 + 16.9 ms); an `anime_tag (tag_id)` index did not help on a temporary copy, so none was added.
9. **A one-letter search** takes about 30 ms (5,058 matches) even with the trigram index.
10. **The new indexes are not yet measured on the real RDS tables**, and the write cost of none of them was measured (the tables are written only by the weekly and manual refreshes).

**What to cache in Redis (#55).** Nothing here is per user and all of it changes only when the catalogue refreshes (weekly for airing anime, monthly for the rest), so a TTL of an hour is safe, except that `release_status=releasing` moves with the calendar. Redis runs with `noeviction`, so every cached key family needs a bounded key space and a size cap.

| Candidate | Why | Key space | Verdict |
|---|---|---|---|
| `/filters`, the default `/studios` page, by-type, by-season, by-year (no filters) | the same for everyone, 23 to 31 ms of aggregation or scanning | one key each | **cache** (`/filters` is already sent with `Cache-Control: public, max-age=3600`) |
| by-tag and by-studio, no year range | the most expensive (110.8 ms; 67 ms) | a few keys (type x sort x order) | **cache** |
| by-tag, by-studio, by-year with `year_from`/`year_to` | the same cost | thousands of combinations | **do not cache**, or only cache the ranges the frontend offers |
| list, default and the frontend's own presets (top by members, top by score, airing + releasing, a season) | the first page is 20 ms to 56 ms before the indexes | a handful of keys | **cache**, TTL at most an hour |
| list with any other filter, sort or offset | already cheap with the indexes, and the key space is unbounded | huge | **do not cache** |
| search | about 1 ms with the trigram index, and every keystroke is a new key | unbounded | **do not cache** |
| per-anime statistics | 35 ms, up to 105 ms for a long runner | one key per anime | **cache the popular ones only**: about 11 KiB on average, so 1,000 anime is about 11 MB, and all 23,086 about 250 MB (estimates from the sample), too much for a `noeviction` Redis |
| per-anime episodes | a primary-key read | one key per anime | **do not cache**: One Piece alone is 1.3 MB; if ever cached, compress and skip entries over a size limit |

Re-run `analysis/bench_endpoints.py` and `analysis/bench_aggregates.py` after the migrations are applied on RDS, and again after Redis is in, to compare.
