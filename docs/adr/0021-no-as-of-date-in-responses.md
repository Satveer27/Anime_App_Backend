# Responses carry no "as of" date; fresh and stale rows are mixed

**Status: draft.** Not accepted until the owner has reviewed it. Amends one sentence of ADR 0004 ("rankings show an 'as of' date"); the rest of ADR 0004 stands.

## Decision

- Statistics and ranking responses do not expose an `as_of` field or an oldest/newest `updated_at`.
- Aggregates and leaderboards mix fresh and stale rows. Anime outside the refresh tiers (Active tier, this year's Anime, the top ~1,000) keep the numbers from their last fetch.
- The refresh tiers, the full-refresh run and "no score history" from ADR 0004 are unchanged.

## Why

The owner judged that users of the statistics page do not need the age of each number, and an `as_of` block on every aggregate adds a query and a response field for little value.

## Considered and rejected

- **An `as_of` block on every aggregate and a per-anime `updated_at`.** More fields to compute, document and test, for information the page does not use.

## Consequences

- Rankings can compare a number fetched last week with one fetched months ago, as ADR 0004 already accepted. The response no longer says so.
- `anime_statistics_mal.updated_at` is still stored, so the field can be added later without a migration.

## Out of scope / known limitations

- Telling stale Anime apart in the UI. The data to do it stays in the database.

## Revisit if

- Users misread old numbers as current, or a ranking visibly mixes very stale and fresh rows: expose `updated_at` per anime, or restrict leaderboards to the refreshed tiers.
