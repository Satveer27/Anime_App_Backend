# Source separation: users hit the MAL API only, the pipeline hits Jikan only

User-triggered work (profile imports) calls the MAL API, and the pipeline calls Jikan. No user action ever triggers a Jikan call: anime missing from a user's list are queued for the pipeline. Each source adapter has its own Redis token bucket, so user traffic and pipeline traffic never share a rate limit. The cost is that a very new anime can be absent from an analysis until the next pipeline run.
