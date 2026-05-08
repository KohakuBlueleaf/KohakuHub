# Cache layer

KohakuHub uses a Valkey-based L2 cache (Redis-compatible) to absorb traffic
that would otherwise hit Postgres or LakeFS REST repeatedly. The design has
one non-negotiable rule:

> The cache must never be on the correctness critical path.

If the cache is unavailable, the API stays correct — every read falls through
to the source of truth. The cache is allowed to be late, but never wrong, and
its absence is allowed to be slow, but never an outage.

This document covers what is in the cache, what is not, and the invariants
that callers must respect when integrating with it. Implementation lives in
[`src/kohakuhub/cache.py`](../../src/kohakuhub/cache.py); tests in
[`test/kohakuhub/test_cache.py`](../../test/kohakuhub/test_cache.py).

## Architecture

```
L1: per-worker cachetools.TTLCache (~µs)
    Immutable / content-addressed data ONLY.
    Mutable data is forbidden in L1 — no cross-worker invalidation channel.

L2: Valkey (~ms)
    All cacheable data (mutable + immutable).
    Cross-worker shared, cross-worker invalidation automatic.

L3: Postgres / LakeFS REST / S3 — source of truth.
```

L1 is intentionally narrow. The default 4-worker uvicorn deployment cannot
synchronously invalidate per-process state across workers; rather than build
a Pub/Sub fan-out (high complexity, low marginal benefit on top of a 1ms L2
hit), we restrict L1 to data that is provably safe to keep stale across all
workers — namely content-addressed entries whose key contains its own version
identifier.

## Consistency model

Two patterns, applied per data class:

### Mode A — Immutable / content-addressed

Key contains the version. Examples:

- `lakefs:commit:{repo}:{commit_id}` — LakeFS commits are SHA-addressed.
- `lakefs:stat:{repo}:{commit_id}:{path}` — same, scoped by commit.
- `lakefs:list:{repo}:{commit_id}:{prefix}` — same, scoped by commit.

These are correct by construction: the key cannot resolve to a different
value over time. TTL is a memory-pressure tool, not a correctness tool. No
active invalidation is needed; LFU eviction reclaims them when memory tightens.

### Mode B — Mutable, real-time consistency required

Two sub-patterns, picked by fan-out:

**B1. Generation counter** — preferred for entities with many derived caches
(Repository, User, Organization).

- The entity table has a `cache_gen BIGINT NOT NULL DEFAULT 1` column.
- Every mutating `db_operations.*` path bumps `cache_gen` in the same
  transaction as the business write.
- Reads embed the current gen in their cache key:
  `repo:info:{id}:g{gen}`. Old gens are unreachable; LFU reclaims them.
- Avoids the bug class of "forgot to enumerate one of the derived keys
  during invalidation."

**B2. Write-through DEL** — used for narrow-fanout entities (Session, Token,
Branch HEAD).

- The mutator path explicitly `DEL`s the affected key(s) **after** DB commit.
- Order matters: DB commit → cache DEL → response. Inverting that ordering
  races concurrent reads back into stale state before the new value is durable.

## Persistence and the boot-time flush

The cache container is started with RDB snapshotting enabled (`--save 300 100`,
`--appendonly no`) and a persistent volume. Two reasons:

1. The bulk of cache value lives in Mode-A immutable keys. Discarding them on
   every Valkey restart turns routine maintenance into a synchronized 1k-fanout
   LakeFS REST burst — exactly the spike the cache exists to prevent.
2. Mode-B keys CAN go stale across a restart (a write whose `DEL` lands during
   Valkey downtime is silently lost), so RDB-restored Mode-B values are
   suspect.

Resolution: persist RDB, but **flush all Mode-B namespaces on every Valkey
restart**. The list of Mode-B prefixes is the `MODE_B_PREFIXES` constant in
`cache.py`:

| Survives across restart (Mode A) | Flushed on every Valkey restart (Mode B) |
| --- | --- |
| `lakefs:commit:` | `lakefs:branch:` |
| `lakefs:stat:` | `repo:gen:`, `repo:info:`, `org:gen:`, `org:info:`, `user:gen:`, `user:info:` |
| `lakefs:list:` | `uo:`, `sess:`, `tok:`, `list_gen:`, `list:`, `negative:` |

Restart detection uses Valkey's `INFO server.run_id` (changes on every Valkey
process start). The API stores the last-seen `run_id` *in Valkey itself*
(`cache:bootstrap_run_id`); on connect, the worker that wins
`SET cache:bootstrap_lock 1 NX EX 30` performs the flush and updates the
marker. Other workers wait until the marker matches the current `run_id`
before serving Mode-B reads.

This decouples cache flushes from API restarts: redeploying the API against
a long-running Valkey is a no-op for the flush coordinator; a Valkey upgrade
or pod-eviction triggers exactly one flush across all API workers.

## Stampede / eviction defenses

All five must remain in place; each closes a different failure mode.

1. **TTL jitter ±15%** — applied inside `cache_set_json`, no opt-out. Without
   this, a synchronized burst of writes all expire at the same instant.
2. **Two-level singleflight** — `asyncio.Lock` per cache key inside the
   worker, plus Valkey `SET sf:lock:{key} 1 NX EX 5` across workers. Both
   required: per-worker alone leaves N workers racing; cross-worker alone
   leaves intra-worker contention.
3. **Negative cache** — `cache_set_negative()` writes a sentinel under a short
   TTL (15s default). Prevents repeated lookups for nonexistent entities from
   hammering L3. Critical: when the entity is later created, the create path
   MUST `cache_invalidate(...)` the negative key — otherwise the negative
   result hides the new entity until TTL expiry.
4. **Refresh-ahead** — allowed *only* on Mode-A immutable entries with
   measured high QPS. Forbidden on Mode-B; serving a stale value while
   refreshing in the background defeats write-through consistency.
5. **`maxmemory` + `allkeys-lfu`**, no key allowed without TTL (even
   immutable entries: cap at 24h). Memory is bounded; eviction is automatic.

## What is NOT cached

These are deliberately uncached, even though they would benefit from the
performance:

| Data | Why not |
| --- | --- |
| `File` row by `(repo, path)` | Preupload SHA256 dedup is strong-consistency-sensitive; a wrong-side cache miss causes silent skip-of-upload. |
| `Quota` / `used_bytes` | Mutated on every write; caching adds bug surface for no win. |
| Presigned S3 URLs | Already short-TTL by design, per-request, per-user — no shared key. |
| Likes / downloads counters | Already updated asynchronously; an extra cache layer adds inconsistency without latency win. |

## Adding a new cached read

1. Decide the mode (A vs B1 vs B2). If you can't decide, the data is
   probably not safe to cache without more thought.
2. Pick a key shape that reflects the mode:
   - Mode A: include the version in the key.
   - Mode B1: include `:g{gen}` and add a generation bump to every mutator.
   - Mode B2: pick a deterministic key and add `cache_invalidate(key)` to
     every mutator post-DB-commit.
3. Use `cache_get_or_fetch(key, fetch_fn, ttl=...)` from `cache.py`. Do not
   call `redis.asyncio` directly — singleflight, jitter, silent degradation,
   and metrics all live in the helper.
4. Add a test that writes the underlying entity and immediately reads through
   the cached path; assert the new value is returned. This is the regression
   guard for the real-time consistency contract.

## Operating

- Admin endpoint `GET /admin/api/cache/stats` returns hit/miss/error
  counters per namespace plus Valkey memory state and the last-flush
  metadata.
- `POST /admin/api/cache/metrics/reset` zeros the counters without
  touching cache contents.
- `KOHAKU_HUB_CACHE_ENABLED=false` disables the cache layer at startup.
  All cache calls then degrade to "miss" silently. CI runs a dedicated job
  with this flag set, so the silent-degradation contract is regression-tested
  on every push.

## Related issues

- [#73](https://github.com/deepghs/KohakuHub/issues/73) — design issue and
  TODO list for follow-up cache integrations beyond infrastructure.
