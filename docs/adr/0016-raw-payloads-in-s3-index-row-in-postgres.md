# Raw Tenrai responses live in S3; Postgres keeps an index row

The raw layer stores each Tenrai response as a gzipped JSON object in S3. The `raw_tenrai` table stays, but as an index: one row per `(mal_id, resource)` with `size_bytes` and `fetched_at`, and no payload. This replaces the `JSONB` payload column of ADR 0014; the rest of ADR 0014 (one raw table per source, the task ledger, the catalogue schema) stands.

The reason is separation, not saving money. The raw layer is a large, write-once, read-rarely archive of a source we do not control, and the database is the product's working store. At the projected 0.5-1.1 GB the saving is a few cents a month, so this is a deliberate design choice made while there is no raw data to migrate. If it ever gets in the way, ADR 0014's design (payload in Postgres) is the fallback, and the object layout makes moving back a simple copy.

## Decisions

- **Key:** `{prefix}/{resource}/{mal_id}.json.gz`, for example `tenrai/episodes/21.json.gz`. We choose it; S3 generates nothing. It is derived from `(resource, mal_id)`, so the index row does not store it and a refresh overwrites the same object. The prefix defaults to `tenrai`, so other sources get their own prefix (ADR 0008).
- **Object:** the response exactly as before, gzip-compressed JSON. A multi-page Resource is a list of pages in one object, written once after the last page (ADR 0014).
- **Index row:** `raw_tenrai (mal_id, resource, size_bytes, fetched_at)`. The clean job still pages through it in `(mal_id, resource)` order and filters on `fetched_at`, then reads each row's object by its derived key. Cursor and cut-off logic are unchanged.
- **Write order in the fetch job:** fetch from Tenrai, `PUT` the object, then in one transaction upsert the index row, mark the task `done` and commit. A crash before the commit leaves, at worst, an object with no index row, which the task's next attempt overwrites. An index row never points at a missing object.
- **S3 failures stop the run.** After the client's own retries (standard mode, 5 attempts), a failed `PUT` or `GET` raises `RawStoreError`. It says nothing about one anime (bad credentials, bucket missing, S3 down), so the fetch job releases the task without using an attempt and stops, like an open circuit. `one_pass` exits with code 6. In the clean job a missing object stops the run and is recorded with `resume_after`.
- **Clean job reads:** a batch's objects are fetched concurrently, at most 16 at a time, in the order of the index rows.
- **Metrics:** the fetch run summary gets `raw_bytes`, the compressed bytes stored by tasks that committed. `raw_tenrai.size_bytes` sums to the same figure afterwards, which is the "raw size" the backfill issue asks for.
- **Security:** a dedicated bucket with Block Public Access and default encryption on. The pipeline's identity gets `s3:PutObject` and `s3:GetObject` on the bucket's prefix and nothing else. Credentials come from the standard AWS chain (environment or an IAM role), never from the repository. Bucket and prefix are `RAW_S3_BUCKET` and `RAW_S3_PREFIX`; the jobs refuse to start without a bucket (exit code 2).
- **No versioning, no history:** a refresh overwrites the object, as it overwrote the row (ADR 0004).

## Considered and rejected

- **Keeping the payload in Postgres (ADR 0014).** Simpler, atomic with the task state, and queryable with SQL. Rejected for the separation above, not for cost or speed.
- **No index table, S3 only.** The clean job would have to list the bucket and track its own cursor and cut-off. The index table keeps that logic and lets SQL answer "what has been fetched".
- **Storing the key in the row.** It is derivable, so a stored copy could only drift from the real layout.
- **A generic object store for every source.** One class per layout is enough until a second source needs raw storage.

## Consequences

- **Atomicity is now across two systems.** The order above keeps it safe; the cost is the orphan-object case and a new failure mode (S3), covered by `RawStoreError`.
- **No SQL over payloads.** Checks like "how many episode pages did we fetch" now read the objects, or use `size_bytes` as a proxy.
- **Reparsing reads one object per row.** About 92,000 `GET`s for a full reparse, at 16 in flight, bounded by S3 latency and not by the database.
- **Cost:** about 92,000 `PUT`s for the first backfill, roughly $0.50.
- **Rows written before this change** have no object. The migration drops the payload column, so any database that already holds raw rows must be emptied (`raw_tenrai`, `ingestion_task`) and refetched.
- **Tests:** the raw store is faked in memory for job tests and with a fake boto client for the store's own tests; the index table and the jobs are still tested against real Postgres (ADR 0006).

## Revisit if

- Raw data grows far beyond the projection (more sources, or history), or the clean job spends most of its time waiting on S3.
- We need SQL over raw payloads (for example for analysis); then a copy in Postgres or a query engine over the bucket is the alternative.
- A second process has to write the same objects concurrently; the derived key would need a conflict rule.

## Measured in the first full Backfill

92,236 objects, 179,554,931 bytes compressed (7.8 KB per anime), against the 41.5 KB of uncompressed JSON per anime that ADR 0012 projected (-81%). The clean job reads them back concurrently, 16 at a time: 406 s of reads in a clean of 822 s. `docs/METRICS.md` section 5.
