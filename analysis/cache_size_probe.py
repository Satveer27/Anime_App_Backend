"""Load the top 1,000 anime's statistics into the cache once and measure what it costs."""

import asyncio
import statistics

import httpx
import redis

HOST = "http://127.0.0.1:8000/api/v1"


async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        ids = []
        for offset in range(0, 1000, 100):
            r = await client.get(
                f"{HOST}/anime", params={"sort": "members", "limit": 100, "offset": offset}
            )
            ids += [i["mal_id"] for i in r.json()["items"]]
        sizes_wire = {}
        sem = asyncio.Semaphore(8)

        async def one(mal_id):
            async with sem:
                r = await client.get(f"{HOST}/anime/{mal_id}/statistics")
                sizes_wire[mal_id] = len(r.content)

        await asyncio.gather(*(one(i) for i in ids))

    r = redis.Redis(decode_responses=True)
    keys = list(r.scan_iter("cache:v1:anime_statistics:*", count=500))
    sizes = sorted(r.memory_usage(k) for k in keys)
    wire = sorted(sizes_wire.values())

    def pct(values, p):
        return values[min(len(values) - 1, int(len(values) * p))]

    print(
        f"top-1000 requested: {len(ids)}, stored: {len(keys)}, skipped (over the cap): {len(ids) - len(keys)}"
    )
    print(
        f"response bytes: avg {statistics.mean(wire) / 1024:.1f} KiB, median {pct(wire, 0.5) / 1024:.1f}, p95 {pct(wire, 0.95) / 1024:.1f}, max {wire[-1] / 1024:.1f}"
    )
    print(
        f"redis bytes per key: avg {statistics.mean(sizes) / 1024:.1f} KiB, median {pct(sizes, 0.5) / 1024:.1f}, p95 {pct(sizes, 0.95) / 1024:.1f}, max {sizes[-1] / 1024:.1f}"
    )
    print(
        f"total for stored keys: {sum(sizes) / 1048576:.1f} MiB; used_memory {r.info('memory')['used_memory'] / 1048576:.1f} MiB"
    )
    ttl = r.ttl(keys[0])
    print(f"ttl of a key: {ttl}s")


asyncio.run(main())
