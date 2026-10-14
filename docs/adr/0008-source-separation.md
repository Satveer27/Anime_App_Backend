# Source separation: users hit the MAL API only, the pipeline hits Tenrai only

User-triggered work (profile imports) calls the MAL API, and the pipeline calls Tenrai (which replaced Jikan, ADR 0011). No user action ever triggers a Tenrai call: anime missing from a user's list are queued for the pipeline. Each source adapter has its own rate limiter (in memory for Tenrai, ADR 0012), so user traffic and pipeline traffic never share a rate limit. The cost is that a very new anime can be absent from an analysis until the next pipeline run.
