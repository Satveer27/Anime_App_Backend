"""Walk the cached anime-list pages twice and compare a database read with a cache hit."""

import statistics
import time

import httpx
import redis

HOST = "http://127.0.0.1:8000/api/v1/anime"
r = redis.Redis(decode_responses=True)


def timed(client, params):
    start = time.perf_counter()
    response = client.get(HOST, params=params)
    elapsed = (time.perf_counter() - start) * 1000
    response.raise_for_status()
    return elapsed, response.json()


def keys():
    return len(list(r.scan_iter("cache:v1:anime_list:*", count=500)))


def walk(client, name, base, limit, last_offset_exclusive):
    miss, hit, same = [], [], True
    pages = 0
    for offset in range(0, last_offset_exclusive, limit):
        params = {**base, "limit": limit, "offset": offset}
        t1, first = timed(client, params)
        t2, second = timed(client, params)
        miss.append(t1)
        hit.append(t2)
        same &= first == second
        pages += 1
        if not first["items"]:
            break
    print(
        f"{name:34} limit {limit:>3}: {pages:>2} pages, first load median {statistics.median(miss):6.1f} ms, "
        f"cached median {statistics.median(hit):5.1f} ms, identical={same}, total={first['total']}"
    )


with httpx.Client(timeout=60) as client:
    before = keys()
    walk(client, "airing, by members", {"status": "airing", "sort": "members"}, 20, 400)
    walk(client, "airing, by members", {"status": "airing", "sort": "members"}, 50, 400)
    walk(client, "upcoming, by members", {"status": "upcoming", "sort": "members"}, 20, 300)
    walk(
        client,
        "airing + year 2026, by members",
        {"status": "airing", "year": 2026, "sort": "members"},
        20,
        400,
    )
    walk(
        client,
        "airing + year_from 2025, by members",
        {"status": "airing", "year_from": 2025, "sort": "members"},
        50,
        400,
    )
    # the show that premiered in winter and still airs must be in the airing-this-year list
    _, page = timed(client, {"status": "airing", "year": 2026, "sort": "members", "limit": 100})
    sbr = [i for i in page["items"] if "Steel Ball Run" in i["title"]]
    print(
        "Steel Ball Run in airing + year 2026:", bool(sbr), [(i["season"], i["year"]) for i in sbr]
    )
    _, page = timed(client, {"season": "fall", "year": 2026, "sort": "members", "limit": 100})
    print(
        "Steel Ball Run in season fall 2026:",
        any("Steel Ball Run" in i["title"] for i in page["items"]),
    )
    walk(
        client,
        "fall 2026 (current), by score",
        {"season": "fall", "year": 2026, "sort": "score"},
        20,
        200,
    )
    walk(client, "plain list, by members (10 pages)", {"sort": "members"}, 20, 200)
    after = keys()
    print(f"cache keys for the list: {before} -> {after}")

    # these must NOT be cached
    t_before = keys()
    for params in (
        {"sort": "members", "limit": 20, "offset": 200},  # page 11 of the plain list
        {"status": "finished", "sort": "members"},
        {"season": "summer", "year": 2026, "sort": "score"},  # last season
        {"status": "airing", "min_score": 7},
        {"status": "airing", "year": 2025},  # not this year
    ):
        timed(client, params)
    print(f"keys after four requests that must not be cached: {keys() - t_before} new")
    sizes = [r.memory_usage(k) for k in r.scan_iter("cache:v1:anime_list:*", count=500)]
    print(
        f"list keys: {len(sizes)}, avg {statistics.mean(sizes) / 1024:.1f} KiB, max {max(sizes) / 1024:.1f} KiB, total {sum(sizes) / 1048576:.2f} MiB"
    )
