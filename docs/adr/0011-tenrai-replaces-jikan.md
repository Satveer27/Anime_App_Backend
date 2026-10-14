# Tenrai replaces Jikan as the catalogue source

Jikan's public API was discontinued on 1 Oct 2026 (jikan-rest issue #613) and no longer answers. The pipeline now reads MAL data from Tenrai (`https://api.tenrai.org/v1`), a Jikan v4-compatible service that serves MAL data from its own pre-gathered store. Its documented limits are 120 requests a minute and 4 a second. The source separation of ADR 0008 is unchanged: users still only hit the MAL API, and the pipeline only hits Tenrai.

The adapter keeps the Jikan v4 response shapes, and the base URL is a setting (`MAL_DATA_BASE_URL`). Another compatible service, or a self-hosted Jikan, can replace Tenrai without code changes.

## Consequences

- Tenrai is a third-party service with no agreement behind it. Its terms of use, uptime and key requirements are unchecked (see the open questions in `docs/METRICS.md`).
- Our data is only as fresh as Tenrai's own gathering, which we do not control.
- Self-hosting Jikan is the fallback. It has the same effective limits and scrapes MAL from our own IP, which is why it was rejected as the first choice.
- ADR 0008 and ADR 0004 name Tenrai and its request budget instead of Jikan. The request rate and worker design are in ADR 0012.
