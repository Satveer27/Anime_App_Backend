# One versioned embedding model, Gemini by default

All vectors (synopsis, vibe profiles, review chunks) come from a single embedding model, and its version is stored with every vector. Gemini is the default; the local model replaces it only if it scores within about 2 points of Gemini's recall@10 in the evals. Indexing failures mark the row `embedding_pending` and are retried on the next run. Query-time failures fall back to BM25-only retrieval and flag the response as degraded. Changing the model means re-embedding everything, so the benchmark runs before the index is built.
