# AnimePlanet

An anime analytics platform built on MAL data, with an evaluated agentic "mood search" on top. It exists as a portfolio project for backend, data and AI engineering roles.

## Language

### Catalogue

**Anime**:
One MAL entry: a single season, movie, OVA or special. MAL is the source of truth for what an Anime is.
_Avoid_: Show, series, title

**Catalogue**:
The set of all Anime the platform holds data for.
_Avoid_: Database, library

**Franchise**:
A connected group of Anime linked by Sequel, Prequel, Parent story or Side story relations.
_Avoid_: Series, universe

**Main line**:
The chain of TV Anime linked by Sequel and Prequel relations within a Franchise. A Franchise with several unrelated roots has several Main lines.
_Avoid_: Canon, core story

**Side line**:
A Sequel branch that is not followed by the Main line, or a side story attached one hop from it.
_Avoid_: Spin-off branch

**Watch order**:
The recommended viewing sequence for a Franchise: release order, corrected by Overrides.
_Avoid_: Timeline, chronological order

**Override**:
A manually curated correction (to a Match or a Watch order) that is applied before any automatic rule.
_Avoid_: Patch, manual fix

### Data sources

**Source adapter**:
The component that talks to exactly one external source and nothing else.
_Avoid_: Client, scraper, connector

**Backfill**:
The one-off first pass that loads the whole Catalogue, most popular Anime first.
_Avoid_: Initial load, seed

**Resource**:
One kind of data fetched for an Anime from a source: details, statistics, reviews, characters or episodes.
_Avoid_: Endpoint, data type

**Ingestion task**:
The unit of work that fetches one Resource for one Anime. Every Backfill and refresh is a set of Ingestion tasks.
_Avoid_: Job, request

**Gone**:
The state of an Ingestion task whose Anime no longer exists at the source (HTTP 404). Different from a Dead letter, which failed repeatedly.
_Avoid_: Deleted, missing

**Raw layer**:
Source responses stored untouched before any parsing, so parsing can be redone without fetching again.
_Avoid_: Staging, cache, bronze

**Selective refresh**:
The monthly re-fetch of scores and statistics for this year's Anime and the most popular ~1,000 Anime. A full refresh of the Catalogue is a separate, manually triggered run.
_Avoid_: Sync, update

**Match**:
A link between an IMDb series and a MAL Anime, with a confidence score.
_Avoid_: Mapping, join

**Review queue**:
The list of Matches too uncertain to accept automatically, waiting for an admin decision.
_Avoid_: Moderation queue

**Dead letter**:
A record that failed ingestion repeatedly and was set aside for inspection.
_Avoid_: Failed job

### Users

**Guest**:
A visitor with no account. Guests can run account analysis and get recommendations, and nothing about them is stored.
_Avoid_: Anonymous user, visitor

**Linked username**:
A MAL or AniList username a logged-in User has attached to their account. It is the only user list identity the platform persists.
_Avoid_: Connected account, profile

**Taste vector**:
A summary of what a person likes, built from their list: affinity weights over genres, tags and studios, plus a dense embedding average of anime they rated above their own mean.
_Avoid_: Preference profile, user embedding

**Group**:
Two to four public usernames considered together for a "watch together" search.
_Avoid_: Party, team

**Least misery**:
Scoring a candidate for a Group by its lowest score among the members.
_Avoid_: Consensus, average

### Search

**Mood search**:
Finding anime from a free-text description of a feeling or situation, answered by an agent over the Catalogue.
_Avoid_: Semantic search, chat

**Vibe profile**:
A short model-written summary of an Anime's tone, pacing, themes and feel, produced from its reviews.
_Avoid_: Review summary, tags
