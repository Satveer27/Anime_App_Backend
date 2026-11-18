"""Cross-check the statistics endpoints against each other and against direct SQL on the real catalogue.

    PYTHONIOENCODING=utf-8 PYTHONPATH=. uv run --env-file .env.rds python analysis/audit_statistics.py

Read-only. Checks that totals add up across by-type, by-year and by-season, that the aggregate
means match SQL, that no group reports more scored anime than anime, that min_ratings behaves
monotonically, and that the per-anime statistics and episode responses hold their invariants
(shares add to 1, vote percentages to 100, percentiles within 0..1, smoothed scores within the
score range) over random samples. Results: docs/METRICS.md, section 12.
"""

import asyncio
import statistics

import httpx
from sqlalchemy import text

from app.core.database.database import engine
from app.main import app

fails, passes = [], []


def check(name, ok, detail=""):
    (passes if ok else fails).append(f"{name} {detail}".strip())


async def main():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120
    ) as h:
        get = lambda u: h.get(u)  # noqa: E731
        total = (await get("/api/v1/anime?limit=1")).json()["total"]
        bt = (await get("/api/v1/statistics/by-type")).json()
        by = (await get("/api/v1/statistics/by-year")).json()
        bs = (await get("/api/v1/statistics/by-season")).json()
        tg = (await get("/api/v1/statistics/by-tag")).json()
        st = (
            await get("/api/v1/statistics/by-studio?sort=mean_score&limit=100&min_anime=10")
        ).json()
        async with engine.connect() as c:
            async def one(q):
                return (await c.execute(text(q))).scalar()
            null_year, null_season, null_type = (
                await one("SELECT count(*) FROM anime WHERE year IS NULL"),
                await one("SELECT count(*) FROM anime WHERE season IS NULL"),
                await one("SELECT count(*) FROM anime WHERE type IS NULL"),
            )
            ok_filter = "mal_score IS NOT NULL AND scored_by_mal >= 50"
            sql_scored, sql_avg = (
                await one(f"SELECT count(*) FROM anime WHERE {ok_filter}"),
                float(await one(f"SELECT avg(mal_score) FROM anime WHERE {ok_filter}")),
            )
            sql_scored_year = await one(
                f"SELECT count(*) FROM anime WHERE {ok_filter} AND year IS NOT NULL"
            )
        sums = lambda b, k: sum(r[k] for r in b["items"])  # noqa: E731
        check(
            "list total = by-type anime total",
            sums(bt, "anime_count") + null_type == total,
            f"({sums(bt, 'anime_count')} + {null_type} null type vs {total})",
        )
        check(
            "by-year anime total + null years = list total",
            sums(by, "anime_count") + null_year == total,
            f"({sums(by, 'anime_count')} + {null_year})",
        )
        check(
            "by-season anime total + null seasons = list total",
            sums(bs, "anime_count") + null_season == total,
            f"({sums(bs, 'anime_count')} + {null_season})",
        )
        check(
            "by-type scored total = SQL count at min 50",
            sums(bt, "scored_count") == sql_scored,
            f"({sums(bt, 'scored_count')} vs {sql_scored})",
        )
        check(
            "by-year scored total = SQL count (years only)",
            sums(by, "scored_count") == sql_scored_year,
            f"({sums(by, 'scored_count')} vs {sql_scored_year})",
        )
        w = sum(r["mean_score"] * r["scored_count"] for r in bt["items"]) / sums(bt, "scored_count")
        check(
            "by-type weighted mean = SQL avg",
            abs(w - sql_avg) < 1e-3,
            f"({w:.4f} vs {sql_avg:.4f})",
        )
        allrows = bt["items"] + by["items"] + bs["items"] + tg["items"] + st["items"]
        check(
            "scored_count <= anime_count everywhere",
            all(r["scored_count"] <= r["anime_count"] for r in allrows),
            f"({len(allrows)} rows)",
        )
        check(
            "means and medians within 1..10 (or null)",
            all(
                (r["mean_score"] is None or 1 <= r["mean_score"] <= 10)
                and (r["median_score"] is None or 1 <= r["median_score"] <= 10)
                for r in allrows
            ),
        )
        check(
            "null mean exactly when scored_count = 0",
            all((r["mean_score"] is None) == (r["scored_count"] == 0) for r in allrows),
        )
        check(
            "by-studio sorted by mean, minimum respected",
            [r["mean_score"] for r in st["items"]]
            == sorted((r["mean_score"] for r in st["items"]), reverse=True)
            and all(r["scored_count"] >= 10 for r in st["items"]),
            f"({len(st['items'])} of {st['total']})",
        )
        low = (await get("/api/v1/statistics/by-type?min_ratings=0")).json()
        high = (await get("/api/v1/statistics/by-type?min_ratings=500")).json()
        check(
            "scored_count shrinks as min_ratings rises (0 >= 50 >= 500)",
            sums(low, "scored_count") >= sums(bt, "scored_count") >= sums(high, "scored_count"),
            f"({sums(low, 'scored_count')}, {sums(bt, 'scored_count')}, {sums(high, 'scored_count')})",
        )

        async with engine.connect() as c:
            ids = [
                r[0]
                for r in (
                    await c.execute(
                        text(
                            "SELECT anime_id FROM anime_statistics_mal WHERE total > 0 ORDER BY md5(anime_id::text) LIMIT 80"
                        )
                    )
                ).all()
            ]
            eids = [
                r[0]
                for r in (
                    await c.execute(
                        text(
                            "SELECT anime_id FROM (SELECT DISTINCT anime_id FROM episode) t ORDER BY md5(anime_id::text) LIMIT 60"
                        )
                    )
                ).all()
            ]
        bad, diffs, votes_vs_scoredby, gems = [], [], [], 0
        for i in ids:
            r = (await get(f"/api/v1/anime/{i}/statistics")).json()
            s, core = r["statistics"], r["core"]
            ls, sd, co, en = s["list_status"], s["score_distribution"], s["cohort"], s["engagement"]
            if (
                abs(
                    sum(
                        ls[k]
                        for k in (
                            "watching_share",
                            "completed_share",
                            "on_hold_share",
                            "dropped_share",
                            "plan_to_watch_share",
                        )
                    )
                    - 1
                )
                > 1e-9
                and ls["total"]
                == ls["watching"]
                + ls["completed"]
                + ls["on_hold"]
                + ls["dropped"]
                + ls["plan_to_watch"]
            ):
                bad.append((i, "status shares"))
            if sd["total_votes"] and abs(sum(b["percentage"] for b in sd["buckets"]) - 100) > 1e-6:
                bad.append((i, "vote percentages"))
            if sd["mean"] is not None and core["mal_score"] is not None:
                diffs.append(abs(sd["mean"] - core["mal_score"]))
            if sd["total_votes"] is not None and core["scored_by_mal"]:
                votes_vs_scoredby.append(sd["total_votes"] / core["scored_by_mal"])
            for k in ("score", "popularity", "favorites", "drop_rate", "completion_rate"):
                m = co.get(k)
                if m and not (0 <= m["percentile"] <= 1 and 1 <= m["rank"] <= co["cohort_size"]):
                    bad.append((i, f"cohort {k}"))
            if co["is_hidden_gem"] and en["low_sample"] is not False:
                bad.append((i, "gem on low sample"))
            gems += bool(co["is_hidden_gem"])
        check(
            f"per-anime statistics invariants on {len(ids)} random anime",
            not bad,
            f"(violations: {bad[:3]})",
        )
        print(f"histogram mean vs MAL score on {len(diffs)} sampled anime: mean gap {statistics.mean(diffs):.3f}, max {max(diffs):.3f} (see docs/METRICS.md section 12)")
        ebad = []
        for i in eids:
            e = (await get(f"/api/v1/anime/{i}/statistics/episodes")).json()
            eps = e["episodes"]
            sc = [x["mal_score"] for x in eps if x["mal_score"] is not None]
            if sc:
                lo, hi = min(sc) - 1e-9, max(sc) + 1e-9
                if any(
                    x["smoothed_score"] is not None and not lo <= x["smoothed_score"] <= hi
                    for x in eps
                ):
                    ebad.append((i, "smoothed outside the score range"))
                if e["best"]["mal_score"] < e["worst"]["mal_score"]:
                    ebad.append((i, "best < worst"))
            if e["trend"]["verdict"] == "gets_good" and e["trend"]["turning_point_episode"] is None:
                ebad.append((i, "gets_good without a turning point"))
            if (
                e["trend"]["verdict"] != "gets_good"
                and e["trend"]["turning_point_episode"] is not None
            ):
                ebad.append((i, "turning point without gets_good"))
            if e["breakdown"]["total_episodes"] != len(eps):
                ebad.append((i, "breakdown total"))
        check(
            f"episode endpoint invariants on {len(eids)} random anime",
            not ebad,
            f"(violations: {ebad[:3]})",
        )
        r = (await get("/openapi.json")).json()
        check(
            "OpenAPI document builds",
            "/api/v1/statistics/by-year" in r["paths"],
            f"({len(r['paths'])} paths)",
        )
        print(
            f"votes / scored_by_mal over {len(votes_vs_scoredby)} anime: median {statistics.median(votes_vs_scoredby):.3f}, min {min(votes_vs_scoredby):.3f}, max {max(votes_vs_scoredby):.3f}"
        )
    await engine.dispose()
    print(f"\nPASSED {len(passes)}:")
    [print("  ok  ", p) for p in passes]
    print(f"FAILED {len(fails)}:")
    [print("  FAIL", f) for f in fails]


asyncio.run(main())
