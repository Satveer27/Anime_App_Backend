"""Run the per-season verdict over every anime with episode scores, and over well-known series.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/check_trend_thresholds.py

Read-only. Prints the verdict distribution, how the turning points are spread, and the verdict
for a list of well-known series, so the threshold constants in app/statistics/trend.py can be
checked against real data (issue #52).
"""

import asyncio
import statistics
from collections import Counter, defaultdict

from sqlalchemy import text

from app.core.database.database import engine
from app.statistics.schemas import Verdict
from app.statistics.trend import compute_trend

SERIES = [
    "One Piece",
    "Shingeki no Kyojin",
    "Fullmetal Alchemist: Brotherhood",
    "Death Note",
    "Steins;Gate",
    "Naruto",
    "Bleach",
    "Hunter x Hunter (2011)",
    "Gintama",
    "Code Geass: Hangyaku no Lelouch",
    "Sword Art Online",
    "Kimetsu no Yaiba",
    "Tokyo Ghoul",
    "Mob Psycho 100",
    "Cowboy Bebop",
    "Shingeki no Kyojin: The Final Season",
    "Vinland Saga",
    "Boku no Hero Academia",
]


async def main():
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT anime_id, episode_number, mal_score FROM episode "
                    "WHERE mal_score IS NOT NULL ORDER BY anime_id, episode_number"
                )
            )
        ).all()
        titles = {
            title.lower(): (mal_id, members)
            for mal_id, title, members in (
                await conn.execute(
                    text(
                        "SELECT mal_id, title, members_mal FROM anime "
                        "WHERE type = 'TV' ORDER BY members_mal NULLS FIRST"
                    )
                )
            ).all()
        }

    by_anime = defaultdict(list)
    for anime_id, number, score in rows:
        by_anime[anime_id].append((number, float(score)))

    verdicts = Counter()
    positions = []  # where the turning point sits in the series, 0 = start, 1 = end
    changes = []
    by_length = defaultdict(Counter)
    for scored in by_anime.values():
        trend = compute_trend(scored)
        verdicts[trend.verdict] += 1
        bucket = (
            "1-13"
            if len(scored) <= 13
            else "14-26"
            if len(scored) <= 26
            else "27-100"
            if len(scored) <= 100
            else "100+"
        )
        by_length[bucket][trend.verdict] += 1
        if trend.verdict == Verdict.GETS_GOOD:
            index = [n for n, _ in scored].index(trend.turning_point_episode)
            positions.append(index / len(scored))
            changes.append(trend.change)

    total = sum(verdicts.values())
    print(
        f"anime with any scored episode: {len(by_anime)}; with a verdict: "
        f"{total - verdicts[Verdict.INSUFFICIENT_DATA]}"
    )
    print("\nverdict distribution (all anime with scored episodes)")
    for verdict, count in verdicts.most_common():
        print(f"  {verdict.value:20} {count:5}  {count / total * 100:5.1f}%")
    print("\nverdict by number of scored episodes (all anime with scored episodes)")
    for bucket in ("1-13", "14-26", "27-100", "100+"):
        counts = by_length[bucket]
        size = sum(counts.values())
        parts = ", ".join(f"{v.value} {counts[v] / size * 100:.0f}%" for v in Verdict if counts[v])
        print(f"  {bucket:7} n={size:5}  {parts}")
    if positions:
        quartiles = statistics.quantiles(positions, n=4)
        print(
            f"\ngets_good turning point as a share of the series: median {statistics.median(positions):.2f}, "
            f"quartiles {quartiles[0]:.2f} / {quartiles[2]:.2f}; median change +{statistics.median(changes):.2f}"
        )

    print("\nwell-known series")
    for name in SERIES:
        found = titles.get(name.lower())
        if not found or found[0] not in by_anime:
            print(f"  {name}: not found or no scored episodes")
            continue
        mal_id = found[0]
        scored = by_anime[mal_id]
        trend = compute_trend(scored)
        print(
            f"  {name} (id {mal_id}): {len(scored)} scored, {trend.verdict.value}, "
            f"baseline {trend.baseline and round(trend.baseline, 2)} ({trend.baseline_size} eps), "
            f"end {trend.end_mean and round(trend.end_mean, 2)}, turning point {trend.turning_point_episode}, "
            f"change {trend.change and round(trend.change, 2)}"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
