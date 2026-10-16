# Refresh rotation is atomic, with a short grace window

A refresh token is single-use. Rotating it revokes the old token and issues a new pair, and presenting an already-revoked token is treated as theft, so every session of that user is wiped. Two requests carrying the same cookie, such as two browser tabs refreshing together, used to both read `revoke = false` and both mint a new pair, which defeats single use. Making rotation strict fixes that, but then the second tab looks exactly like a replay and logs the user out everywhere.

Rotation now decides the winner in the database: `UPDATE refresh_token SET revoke = true, revoked_at = now WHERE jti = :jti AND revoke = false`. Postgres lets one concurrent request match the row (`rowcount == 1`) and that request mints the new tokens. A request that gets `rowcount == 0` re-reads the row and chooses between a racing refresh and a replay.

## Decisions

- **Winner:** `rowcount == 1` from the conditional update above. Only the winner mints tokens. The check and the write are one statement, so there is no read-then-write gap.
- **Grace window:** `REFRESH_GRACE_SECONDS`, default 10. A loser whose token was revoked within the window gets a 401 and nothing else: no tokens, no session wipe, and the cookie is left alone.
- **Replay:** a loser outside the window, or whose row has no `revoked_at`, is treated as reuse. All of the user's refresh tokens are deleted, their access tokens are revoked in Redis, and the response is a 401.
- **`revoked_at`** (nullable `timestamptz`) is set by rotation and by logout-all. Rows revoked before the column existed and rows revoked by a single logout have `NULL`, which counts as "revoked long ago".
- **Re-read:** the loser re-reads with `populate_existing`, because the session otherwise returns the stale cached row and the window check would never see `revoked_at`.
- **Client contract:** on a 401 from `/auth/refresh`, retry once. The winning tab has already set the new cookie, and all tabs share one cookie jar. Keep one refresh in flight per tab. Send the user to login only if the retry also fails.

## Considered and rejected

- **Strict wipe on any reuse.** Simplest and safest, but two tabs refreshing together log the user out everywhere.
- **Return the winner's tokens to the loser.** Friendlier, but it needs the new token material stored server-side for a moment, which is a bigger attack surface than a 401 and a retry.
- **Token families, with lineage and revoking descendants.** It would also close the logout race below, but it needs a parent column and descendant revocation for a gap this small.
- **Rely on client-side single-flight alone.** The server cannot assume every client does it.

## Known gaps

- A stolen token replayed inside the window gets a 401 and no tokens, but it does not trigger the wipe. Detection is delayed, not lost, because a replay after the window does.
- If a refresh wins a race with a logout, the new token it minted stays live after the logout. In the other ordering, the losing refresh sees a `NULL` `revoked_at` and wipes the user's other sessions.
- Tests are mock-only. The atomicity is a Postgres guarantee that the unit tests cannot exercise (see ADR 0006).

## Revisit if

- Users still report being logged out by concurrent refreshes, so the window needs tuning or the client contract is not being followed.
- A security review wants replay detection inside the window, or the logout race matters. Token families are then the fix.
- A real-Postgres concurrency test is added to CI, which ADR 0006 permits for exactly this kind of check.
