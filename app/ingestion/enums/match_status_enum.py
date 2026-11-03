from enum import StrEnum


class MatchStatus(StrEnum):
    # Confident match; its IMDb score is written to the catalogue.
    ACCEPTED = "accepted"
    # Borderline or tied; waits for a human decision.
    REVIEW = "review"
    # No usable candidate.
    UNMATCHED = "unmatched"
