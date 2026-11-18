"""Load test for the public statistics and anime endpoints (issue #55).

Start the API (see docs/METRICS.md section 14 for the exact commands), then, for one
concurrency level and one mix:

    uv run locust -f analysis/locustfile.py FullMix --headless -u 20 -r 20 -t 60s \
        --host http://127.0.0.1:8000 --csv OUT_PREFIX --only-summary

`FullMix` calls every public endpoint, cacheable and not. `StatsOnly` calls only the per-anime
statistics, to see that one endpoint on its own. Both are read-only.

The anime ids are read from the running API when the test starts: the top 1,000 by members
(the popular set) and a sample of the tail. A request for a popular anime is drawn with a
Zipf weight, `1 / (rank + 1) ** ZIPF_S` (default 1.0), so a few anime are asked for far more
than the rest. **That skew is an assumption**: nothing here knows the real traffic, so it is
set with `ZIPF_S` (0 would be uniform over the top 1,000) and recorded with the results.
"""

import os
import random
from itertools import accumulate

import requests
from locust import HttpUser, between, events, task

API = "/api/v1"
ZIPF_S = float(os.environ.get("ZIPF_S", "1.0"))
TOP_N = 1000
TAIL_SAMPLE = 1000
SHARE_POPULAR = 0.75  # of per-anime requests, the rest go to a random anime from the tail

popular_ids: list[int] = []
popular_cum_weights: list[float] = []
tail_ids: list[int] = []

TYPES = ["tv", "movie", "ova", "special", "ona", "tv_special"]
SEARCHES = ["titan", "one piece", "naruto", "love", "hero", "slime", "dragon", "school", "a"]
TAGS = [1, 2, 4, 8, 10, 22, 36]  # genres/themes, the ids are only used as filter values


def _page(host: str, offset: int) -> list[int]:
    response = requests.get(
        f"{host}{API}/anime",
        params={"sort": "members", "limit": 100, "offset": offset},
        timeout=30,
    )
    response.raise_for_status()
    return [item["mal_id"] for item in response.json()["items"]]


@events.test_start.add_listener
def load_ids(environment, **_):
    host = environment.host
    popular_ids.clear()
    for offset in range(0, TOP_N, 100):
        popular_ids.extend(_page(host, offset))
    popular_cum_weights.clear()
    popular_cum_weights.extend(
        accumulate(1 / (rank + 1) ** ZIPF_S for rank in range(len(popular_ids)))
    )
    tail_ids.clear()
    for offset in random.sample(range(TOP_N, 20_000, 100), TAIL_SAMPLE // 100):
        tail_ids.extend(_page(host, offset))
    print(f"ids: {len(popular_ids)} popular, {len(tail_ids)} tail, zipf s={ZIPF_S}")


def pick_anime() -> int:
    if random.random() < SHARE_POPULAR:
        return random.choices(popular_ids, cum_weights=popular_cum_weights)[0]
    return random.choice(tail_ids)


class StatsOnly(HttpUser):
    """Only the per-anime statistics page."""

    wait_time = between(0.05, 0.15)

    @task
    def anime_statistics(self):
        self.client.get(f"{API}/anime/{pick_anime()}/statistics", name="/anime/{id}/statistics")


class FullMix(HttpUser):
    """Everything the statistics page and the anime pages call."""

    wait_time = between(0.05, 0.15)

    # --- per-anime (tier 2) ---

    @task(14)
    def anime_statistics(self):
        self.client.get(f"{API}/anime/{pick_anime()}/statistics", name="/anime/{id}/statistics")

    @task(2)
    def anime_episodes(self):
        self.client.get(
            f"{API}/anime/{pick_anime()}/statistics/episodes",
            name="/anime/{id}/statistics/episodes",
        )

    # --- aggregates and lists (tier 1) ---

    @task(3)
    def by_year(self):
        self.client.get(f"{API}/statistics/by-year", name="/statistics/by-year")

    @task(2)
    def by_type(self):
        self.client.get(f"{API}/statistics/by-type", name="/statistics/by-type")

    @task(2)
    def by_season(self):
        self.client.get(f"{API}/statistics/by-season", name="/statistics/by-season")

    @task(3)
    def by_tag(self):
        params = {"type": random.choice([None, "tv", "movie"])}
        self.client.get(f"{API}/statistics/by-tag", params=params, name="/statistics/by-tag")

    @task(2)
    def by_studio(self):
        self.client.get(f"{API}/statistics/by-studio", name="/statistics/by-studio")

    @task(3)
    def hidden_gems(self):
        params = {"type": random.choice([None, "tv", "ona"])}
        self.client.get(
            f"{API}/statistics/hidden-gems", params=params, name="/statistics/hidden-gems"
        )

    @task(3)
    def most_dropped(self):
        params = {"type": random.choice([None, "tv", "movie"])}
        self.client.get(
            f"{API}/statistics/most-dropped", params=params, name="/statistics/most-dropped"
        )

    @task(3)
    def filters(self):
        self.client.get(f"{API}/filters", name="/filters")

    @task(2)
    def studios(self):
        self.client.get(f"{API}/studios", name="/studios")

    @task(6)
    def list_default(self):
        params = {"sort": random.choice(["members", "score", "favorites"])}
        self.client.get(f"{API}/anime", params=params, name="/anime (unfiltered first page)")

    # --- requests the cache does not cover (unbounded key space) ---

    @task(4)
    def list_filtered(self):
        params = {
            "tag_id": random.choice(TAGS),
            "year": random.randint(2000, 2025),
            "sort": "score",
            "min_ratings": 500,
        }
        self.client.get(f"{API}/anime", params=params, name="/anime (tag + year)")

    @task(3)
    def search(self):
        self.client.get(
            f"{API}/anime", params={"q": random.choice(SEARCHES)}, name="/anime (search)"
        )

    @task(2)
    def by_year_range(self):
        params = {"year_from": random.randint(1990, 2015), "year_to": random.randint(2016, 2025)}
        self.client.get(
            f"{API}/statistics/by-year", params=params, name="/statistics/by-year (year range)"
        )
