# Single sequential worker at 100 requests per minute

The pipeline calls Tenrai from one sequential worker, with at least 0.6 s between request starts: 100 requests a minute, 83% of the documented 120/min, and a peak of 2 requests a second against the 4/s cap. Spacing is an in-memory timer on `time.monotonic()`, equivalent to a token bucket of capacity 1. It is not in Redis, because only one process calls Tenrai. Airflow puts all Tenrai tasks in a pool with one slot, so runs never overlap.

Evidence is the probe in `docs/METRICS.md`: 2,852 requests, all 200, with zero 429s, 5xx or timeouts. Median latency is about 0.05 s on every anime endpoint, so throughput is bound by the rate limit, not latency. One worker spends about 92% of its time waiting on the limiter, so concurrency adds no throughput. Measured spacing: minimum gap 0.6 s, at most 100 requests in any 60 s, at most 2 in any 1 s.

## Decisions

- **Rate:** 100/min. Raise to 110/min only after a full overnight run with zero 429s.
- **No proactive slowdown.** Tenrai sends no rate-limit headers, so we rely on 429 handling.
- **Retries:**
  - 429 waits for `Retry-After` (30 s if missing).
  - 500, 502, 503, 504 and network errors (`httpx.TransportError`) retry with exponential backoff (1, 2, 4, 8 s), at most 5 attempts, then `dead`.
  - 400 is `dead` (it is a bug in our request).
  - 404 is `gone`.
  - Other 4xx are not retried.
- **Episodes** are fetched for every type, movies included. That is about one call per anime; only 1-7% need a second page, at most 12.
- **Cost** is 4.03 calls and about 41.5 KB of raw JSON per anime (random groups). For the 28,497 anime left after the rating filter that is about 115,000 calls, about 19 hours at 100/min, and up to about 1.1 GB raw (0.55-1.13 GB). These are projections. The exact catalogue size comes from the discovery pass.

## Considered and rejected

- **Several concurrent workers.** No throughput gain, and they need a shared limiter and bring race conditions.
- **A Redis Lua token bucket now.** Only one process calls Tenrai, so it adds a moving part for nothing.

## Revisit if

- The achieved rate falls well below 100/min without any 429s, meaning latency has become the bottleneck.
- 429s appear.
- A second process has to call Tenrai. A shared Redis limiter is then needed.

ADR 0008's per-adapter Redis bucket becomes this in-memory limiter for Tenrai. The MAL API adapter for user traffic keeps its own limiter.
