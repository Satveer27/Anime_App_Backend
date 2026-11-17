"""Pure maths behind the statistics responses: no database, no I/O.

Every function returns None instead of raising when an input is missing or a denominator is
zero. Shared with the platform-wide aggregates so a rate means the same thing everywhere.
"""

from collections.abc import Sequence
from math import sqrt

from app.statistics.schemas import ListStatusSchema, ScoreBucketSchema, ScoreDistributionSchema

# MAL's own status texts, as stored on `anime.status`.
STATUS_AIRING = "Currently Airing"
STATUS_NOT_YET_AIRED = "Not yet aired"

SCORES = range(1, 11)
LOW_SCORES = range(1, 5)
HIGH_SCORES = range(9, 11)


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, or None if either is missing or the denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def build_list_status(
    *,
    watching: int,
    completed: int,
    on_hold: int,
    dropped: int,
    plan_to_watch: int,
    total: int,
    anime_status: str | None,
) -> ListStatusSchema:
    """List counts, each status's share of the total, and the engagement rates.

    The rates divide by the members who actually started the anime (total minus plan to
    watch), because plan to watch is intent, not an outcome. `completion_rate` is None while the
    anime is airing, and every rate is None for an anime that has not aired.
    """
    started = total - plan_to_watch
    rates_known = anime_status != STATUS_NOT_YET_AIRED
    return ListStatusSchema(
        watching=watching,
        completed=completed,
        on_hold=on_hold,
        dropped=dropped,
        plan_to_watch=plan_to_watch,
        total=total,
        watching_share=safe_ratio(watching, total),
        completed_share=safe_ratio(completed, total),
        on_hold_share=safe_ratio(on_hold, total),
        dropped_share=safe_ratio(dropped, total),
        plan_to_watch_share=safe_ratio(plan_to_watch, total),
        completion_rate=(
            safe_ratio(completed, started)
            if rates_known and anime_status != STATUS_AIRING
            else None
        ),
        drop_rate=safe_ratio(dropped, started) if rates_known else None,
        watching_rate=safe_ratio(watching, started) if rates_known else None,
    )


def _usable_votes(votes: Sequence[int | None]) -> list[int] | None:
    """The ten vote counts if all are present and at least one vote exists, else None."""
    if len(votes) != len(SCORES) or any(v is None for v in votes):
        return None
    if sum(votes) == 0:
        return None
    return list(votes)


def build_score_distribution(votes: Sequence[int | None]) -> ScoreDistributionSchema:
    """The ten-bucket histogram (index 0 is score 1) with its total, mean and mode.

    If any bucket is missing or there are no votes at all, the buckets keep their raw counts
    and everything derived from them is None.
    """
    usable = _usable_votes(votes)
    if usable is None:
        return ScoreDistributionSchema(
            buckets=[ScoreBucketSchema(score=s, votes=votes[s - 1]) for s in SCORES]
            if len(votes) == len(SCORES)
            else [],
        )
    total = sum(usable)
    mean = sum(s * usable[s - 1] for s in SCORES) / total
    # The highest score wins a tie between equally common scores.
    mode = max(SCORES, key=lambda s: (usable[s - 1], s))
    return ScoreDistributionSchema(
        buckets=[
            ScoreBucketSchema(score=s, votes=usable[s - 1], percentage=usable[s - 1] / total * 100)
            for s in SCORES
        ],
        total_votes=total,
        mean=mean,
        mode=mode,
    )


def standard_deviation(votes: Sequence[int | None]) -> float | None:
    """Population standard deviation of the scores, weighted by their vote counts."""
    usable = _usable_votes(votes)
    if usable is None:
        return None
    total = sum(usable)
    mean = sum(s * usable[s - 1] for s in SCORES) / total
    return sqrt(sum(usable[s - 1] * (s - mean) ** 2 for s in SCORES) / total)


def vote_share(votes: Sequence[int | None], scores: range) -> float | None:
    """The share (0-1) of all votes that went to the given scores."""
    usable = _usable_votes(votes)
    if usable is None:
        return None
    return sum(usable[s - 1] for s in scores) / sum(usable)
