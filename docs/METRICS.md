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
