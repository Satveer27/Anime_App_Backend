# IMDb is dropped: all scores and episode analysis come from MAL

**Status: draft.** Not accepted until the owner has reviewed it. Supersedes ADR 0002, ADR 0018 and ADR 0019 in full, and the IMDb-dependent part of ADR 0003.

## Decision

- IMDb is no longer a data source. Matching IMDb series to MAL Anime (ADR 0002), the weekly Spark filter-and-match job (ADR 0018) and the in-memory matching design (ADR 0019) are not built.
- MAL stays the only source of scores. `anime.mal_score` and `episode.mal_score` are the only scores the product reads.
- **"Does it get good?" (ADR 0003) runs on one MAL entry at a time.** It looks at the MAL episode scores of a single season or cour: does the score rise and then stay up. It does not walk a Franchise or a Main line, because the cross-season slicing existed only to line MAL entries up with IMDb's grouped seasons.
- `anime.imdb_score`, `anime.anilist_score` and the matching `episode` columns stay in the schema as nullable and unused. Nothing reads them and no response exposes them.
- No code or table is removed by this ADR. The IMDb download job, `imdb_snapshot`, `anime_imdb_match` and the `imdb_weekly` DAG are left as they are and removing them is a separate cleanup.

## Why

Matching IMDb to MAL did not reach a quality the owner could rely on, and a wrong Match shows a wrong score next to an anime, which is worse than showing none. MAL already provides the scores, the score-vote distribution and per-episode scores that the statistics features need. Dropping the second source removes the Match step, the Review queue, the Overrides, a weekly Spark job and its memory cost on a 6 GB box (ADR 0017), and the labelled-set work that measuring Match precision needed.

## Considered and rejected

- **Keep the IMDb pipeline and ship statistics without it.** It keeps work and operational cost alive for a source no feature uses.
- **Delete the IMDb code, tables and DAG now.** Safe to do later, but it is a separate change with its own migration and risk, and it should not block the statistics work.
- **Keep "does it get good" across the Main line, on MAL scores only.** Possible, but it needs the recursive Sequel/Prequel walk and a rule for crossing entries. A single entry covers the question for now.

## Consequences

- ADR 0003's Main line and the Match / Review queue terms in `CONTEXT.md` have no consumer until something needs them again. `CONTEXT.md` should be updated to say so.
- `imdb_score` stays NULL for every anime. Any code or test that assumes it is populated is wrong.
- A score on `anime` can only mean MAL's, so a response needs no per-source label.
- MAL's 0–5 episode scale, doubled to 0–10, is coarse, and episode scores can be missing (stored as NULL, about 3% in the sample). The per-season "gets good" signal is rougher than IMDb's would have been.

## Out of scope / known limitations

- Dropping the IMDb tables, job and DAG, and removing their code and tests.
- A cross-season "does it get good" (Main line, Watch order, Overrides).
- AniList as a replacement second source.

## Revisit if

- A second score source is wanted again. Then write a new ADR instead of reviving ADR 0018 as it is, because the matching approach it describes did not work.
- A feature needs the Franchise or Main line (for example a watch order).
