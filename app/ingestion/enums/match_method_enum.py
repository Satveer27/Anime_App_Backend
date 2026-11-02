from enum import StrEnum


class MatchMethod(StrEnum):
    # A manual decision. Checked before any scoring and never overwritten by the pipeline.
    OVERRIDE = "override"
    # Matched by title similarity.
    TITLE = "title"
    # Matched by walking an IMDb series' episodes in Main line order (season slices).
    CHAIN = "chain"
