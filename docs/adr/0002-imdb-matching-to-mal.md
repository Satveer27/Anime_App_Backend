# IMDb is matched to MAL by weighted trigram scoring, with overrides first

MAL is the source of truth, and IMDb data is attached to it, never the reverse. Matches use normalised titles, pg_trgm similarity and a weighted score (title, year, episode count, type). Later seasons are matched through sequel relations. Stored Overrides are checked before any scoring, and uncertain Matches go to the Review queue. The target is at least 97% precision on auto-accepted Matches, measured on a hand-labelled, stratified set with a held-out part, with coverage reported separately.
