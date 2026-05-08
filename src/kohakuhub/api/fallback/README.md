# Fallback API Module

The `kohakuhub.api.fallback` module lets a KohakuHub instance proxy reads
to external sources (HuggingFace or another KohakuHub) when a repository
or file is not present locally. Writes never fall back — local is the
only writable source.

This document reflects the post-[#77](https://github.com/deepghs/KohakuHub/pull/77)
design: **repo-grain binding with strict consistency**. The module's prior
"cache as reorder hint" model has been replaced by a per-`repo_id`
binding contract that this document specifies.

## Contract

For each `repo_id = (repo_type, namespace, name)`:

1. **Same auth + same external state ⇒ same source.** Within the cache TTL
   for one `repo_id`, every read goes to exactly one source. The SPA's
   info card, the tree listing, and the file resolve cannot describe
   different repos that happen to share a name across sources.
2. **Local namespace wins absolutely.** If a local repo with the same
   `repo_id` exists, every read against it serves from local — even when
   an upstream source has the same name with different content. The
   fallback chain is entered only when the local layer signals
   `RepoNotFound` (or returns a 404 without `X-Error-Code`).
3. **Cross-source mixing is forbidden.** Once a source claims a `repo_id`
   (whether to serve content or to surface a definitive error), no other
   source is consulted for that bind window.
4. **Writes are local-only.** No write endpoint carries a fallback
   decorator. Repo creation, commit, preupload, branch ops, settings
   mutations all bypass the fallback layer.

The trade-off: **consistency over availability**. A bound source's
transient failure (5xx, network blip) surfaces to the client as that
failure, not as a magic-rebind to a different source. The cache TTL
bounds the recovery window. See [Cache](#cache) for tuning.

## Module structure

| File | Responsibility |
|---|---|
| `utils.py` | `FallbackDecision` enum, `classify_upstream`, aggregate-failure builder, header hygiene helpers. |
| `client.py` | Async HTTP client (`FallbackClient`) for outbound source requests; URL mapping between HF-flavor and kohakuhub-flavor sources. |
| `config.py` | `get_enabled_sources(namespace, user)` — DB + global config resolution, returns priority-ordered list with per-source token already filled in. |
| `cache.py` | `RepoSourceCache` — TTL+LRU cache mapping `(repo_type, namespace, name) → bound source`. |
| `operations.py` | `_run_cached_then_chain` orchestrator + per-op wrappers `try_fallback_resolve` / `try_fallback_info` / `try_fallback_tree` / `try_fallback_paths_info`. Owns the binding lock registry. |
| `decorators.py` | `@with_repo_fallback`, `@with_user_fallback`, `@with_list_aggregation` — wrap FastAPI endpoints. |

## Request lifecycle

For a repo-scoped request decorated with `@with_repo_fallback`:

1. **Local first.** The decorator invokes the wrapped endpoint. If it
   succeeds (2xx/3xx), the local response is returned unchanged.
2. **Local 404 — gated fall-through.** On a local 404 the decorator
   inspects `X-Error-Code`:
   - `EntryNotFound` or `RevisionNotFound` → return local. The local repo
     exists; the entry/revision missing is the authoritative answer.
     **The chain is not entered.**
   - `RepoNotFound`, or no `X-Error-Code` → enter the chain.
3. **Chain entry: lock acquisition.** `_run_cached_then_chain` looks up
   the per-loop, per-repo binding lock and `await`s on it.
4. **Cache check (under lock).** If `cache.get(repo_type, namespace, name)`
   hits, the bound source is used directly: a single upstream call to
   that source, classified per the rules below. The rest of the chain is
   not consulted.
5. **Cache miss: chain probe.** `get_enabled_sources(namespace, user)`
   returns priority-ordered sources. Each is queried in turn; each
   response is fed to `classify_upstream`:
   - `BIND_AND_RESPOND` — write cache, serve upstream's body, terminate.
   - `BIND_AND_PROPAGATE` — write cache, forward upstream's 4xx + headers
     verbatim (so a `huggingface_hub` client raises the right exception),
     terminate.
   - `TRY_NEXT_SOURCE` — append to attempts list, advance.
6. **Chain exhaustion.** If every source returned `TRY_NEXT_SOURCE`,
   `build_aggregate_failure_response` synthesizes a single response from
   the attempt categories. The bound-source cache is **not** written
   (there is no winner to pin).
7. **Lock release.**

The lock guarantees that 100 concurrent first-time callers for one cold
repo all observe the same bound source. The first to enter probes the
chain; the rest wait, then re-check the cache (post-lock cache-recheck
fast-path) and serve via the now-bound source.

## Classifier (`utils.classify_upstream`)

Maps an upstream response (or transport exception) to a `FallbackDecision`,
mirroring `huggingface_hub.utils.hf_raise_for_status` priority — the
header `X-Error-Code` is the source of truth, not the numeric status.

| Upstream signal | Decision |
|---|---|
| 2xx / 3xx | `BIND_AND_RESPOND` |
| `X-Error-Code: EntryNotFound` | `BIND_AND_PROPAGATE` |
| `X-Error-Code: RevisionNotFound` | `BIND_AND_PROPAGATE` |
| `X-Error-Code: GatedRepo` (401 or 403) | `TRY_NEXT_SOURCE` (aggregate keeps the GatedRepo signal if every source ends up gated) |
| `X-Error-Code: RepoNotFound` (404, authed caller) | `TRY_NEXT_SOURCE` |
| `X-Error-Message: "Access to this resource is disabled."` | `TRY_NEXT_SOURCE` (changed in [#77](https://github.com/deepghs/KohakuHub/pull/77); previously `BIND_AND_PROPAGATE`) |
| Bare 401 (HF anti-enum: `"Invalid username or password."`) | `TRY_NEXT_SOURCE` |
| Bare 401 (`"Invalid credentials in Authorization header"`) | `TRY_NEXT_SOURCE` |
| Bare 403 / 404 / 5xx | `TRY_NEXT_SOURCE` |
| Timeout / network error | `TRY_NEXT_SOURCE` |

`BIND_AND_PROPAGATE` is the critical anti-mixing case: the upstream is
saying *the repo lives at this source, the file/revision does not* — a
sibling source's same-named repo would be a different repo. Forwarding
the upstream's 4xx verbatim lets a `huggingface_hub` client raise
`EntryNotFoundError` / `RevisionNotFoundError` cleanly.

## Aggregate-failure priority

When the chain exhausts (every source `TRY_NEXT_SOURCE`),
`build_aggregate_failure_response` picks one error to surface, with this
priority:

```
AUTH > DISABLED > FORBIDDEN > NOT_FOUND > 502
```

The intent: if any source claimed authentication was required, that
signal is more actionable for the user than a generic 404. If any source
returned the disabled marker, surface that. If every source said 404, the
final response is a 404 with `X-Error-Code: RepoNotFound`.

## Cache

`RepoSourceCache` (in `cache.py`) is a `cachetools.TTLCache(maxsize=10000)`.

| Property | Value |
|---|---|
| Key | `f"fallback:repo:u={user_id\|anon}:t={tokens_hash\|}:{repo_type}:{namespace}/{name}"` |
| Value | `{source_url, source_name, source_type, exists, checked_at}` |
| TTL | `cfg.fallback.cache_ttl_seconds` (env: `KOHAKU_HUB_FALLBACK_CACHE_TTL`, default `300`) |
| Eviction | TTL + LRU at `maxsize` |

`tokens_hash` is `sha256_hex(canonical_json(sorted(merged_tokens.items())))[:16]`,
or empty when no per-user tokens are present. This isolates two requests
that authenticate as the same user but pass different external tokens
via `Authorization: Bearer ...|url,token|...`.

Lifecycle:

- **Write**: only on `BIND_AND_RESPOND` / `BIND_AND_PROPAGATE` from a
  fresh chain probe, and only via `safe_set` (see "Generation
  counters" below).
- **No write on chain exhaustion**: an aggregate-failure response leaves
  the next caller free to re-probe.
- **No write from cache hits**: a cache hit serves directly without
  re-binding.
- **Strict consistency under TTL**: a bound source returning a transient
  error (5xx, timeout) within the TTL **does not** invalidate the cache
  or rebind. The error surfaces to the client. Self-heal is bounded by
  the TTL.

### Generation counters (race protection, #79)

Three monotonic counters guard against the "an invalidation event lands
mid-probe and the probe writes a now-stale binding after the
invalidation" race:

| Counter | Bumped by | Reset on |
|---|---|---|
| `global_gen` | `cache.clear()` (admin source mutations) | never |
| `user_gens[user_id]` | `cache.clear_user(user_id)` (user external-token mutations) | never |
| `repo_gens[(rt, ns, name)]` | `cache.invalidate_repo(rt, ns, name)` (local repo CRUD, admin per-repo eviction) | never |

`_run_cached_then_chain` snapshots all three before doing upstream I/O
and passes the snapshot to `safe_set`; `safe_set` rejects the cache
write if any of the three has been bumped during the probe window.
Rejection means the response still flows to the caller (it was already
constructed) but the cache stays empty for that bucket, so the next
call re-probes with the post-mutation configuration.

### Invalidation matrix

Every event that can change the binding outcome triggers an
invalidation. Inputs that are boot-time-only (env / TOML) are handled
by the natural process-restart cycle; runtime-mutable inputs hook into
the cache as follows:

| Event | Hook location | Cache op | Generation bumped |
|---|---|---|---|
| Admin POST/PATCH/DELETE on `FallbackSource` | `api/admin/routers/fallback.py` | `cache.clear()` | `global_gen` |
| User POST `/api/users/{u}/external-tokens` | `api/auth/external_tokens.py` | `cache.clear_user(user.id)` | `user_gens[uid]` |
| User DELETE `/api/users/{u}/external-tokens/{url}` | same | `cache.clear_user(user.id)` | `user_gens[uid]` |
| User PUT `/api/users/{u}/external-tokens/bulk` | same | `cache.clear_user(user.id)` | `user_gens[uid]` |
| Local POST `/api/repos/create` | `api/repo/routers/crud.py` | `cache.invalidate_repo(rt, ns, name)` | `repo_gens[(rt,ns,n)]` |
| Local DELETE `/api/repos/delete` | same | `cache.invalidate_repo(rt, ns, name)` | `repo_gens[(rt,ns,n)]` |
| Local POST `/api/repos/move` (rename / transfer) | same | `cache.invalidate_repo(old)` + `cache.invalidate_repo(new)` | both `repo_gens` entries |
| Local PUT settings, `private` field changed | `api/settings.py` | `cache.invalidate_repo(rt, ns, name)` | `repo_gens[(rt,ns,n)]` |
| Local POST `/api/repos/squash` | n/a — `full_id` unchanged | n/a | n/a |
| Header-token change | per-request | n/a — encoded in cache key via `tokens_hash` | n/a |

### Operator invalidation endpoints

| Endpoint | Effect |
|---|---|
| `DELETE /admin/api/fallback-sources/cache/clear` | Wipe entire cache; bumps `global_gen`. |
| `DELETE /admin/api/fallback-sources/cache/repo/{repo_type}/{namespace}/{name}` | Evict every (user, tokens_hash, repo) bucket for one repo; bumps that repo's gen. |
| `DELETE /admin/api/fallback-sources/cache/user/{user_id}` | Evict every (tokens_hash, repo) bucket for one user; bumps that user's gen. |

### Planned changes

- [#78](https://github.com/deepghs/KohakuHub/issues/78) lowers the
  default TTL to `60` (self-heal window 1 minute instead of 5),
  decouples chain probing into a pure `core.probe_chain` function, and
  adds admin tooling for chain inspection and what-if simulation.
- Multi-worker shared cache (so admin clear in one fork is visible to
  siblings) is a separate follow-up — the current implementation is
  per-process in-memory.

## Configuration

Environment variables (overrides `config-example.toml` defaults):

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `KOHAKU_HUB_FALLBACK_ENABLED` | bool | `true` | Master switch. |
| `KOHAKU_HUB_FALLBACK_CACHE_TTL` | int (s) | `300` | Bind-cache TTL. See [#78](https://github.com/deepghs/KohakuHub/issues/78). |
| `KOHAKU_HUB_FALLBACK_TIMEOUT` | int (s) | `30` | Per-request timeout to upstream sources. |
| `KOHAKU_HUB_FALLBACK_MAX_CONCURRENT` | int | `10` | Concurrency cap for in-flight upstream requests. |
| `KOHAKU_HUB_FALLBACK_REQUIRE_AUTH` | bool | `false` | If true, require an authenticated caller before consulting any fallback source. |
| `KOHAKU_HUB_FALLBACK_SOURCES` | JSON list | `[]` | Global source list (the admin DB-backed `fallbacksource` table is preferred for non-toy deployments). |

Source list is the union of (a) the JSON env var / TOML config and (b)
the `fallbacksource` DB table managed via the admin API. The DB-backed
sources are dynamic; admin mutations clear the cache on commit.

## Trust assumptions

The fallback layer treats configured sources as **trusted**. The
operator's responsibility:

- A compromised or hostile source can return arbitrary content under any
  `repo_id` it claims to serve.
- httpx's redirect handler follows 307 `Location` headers across origins
  by default (used to honor HF's canonical-name redirects). A hostile
  source that 307s us to an attacker-controlled URL will be followed.
  See [#77 risk review § 14](https://github.com/deepghs/KohakuHub/pull/77#issuecomment-4360658543).
- Operator-managed `source_url` and the FastAPI-validated path
  parameters are the only inputs to outgoing requests; no
  user-controlled URL fragment reaches an upstream.

Mitigation: only configure sources you operate or fully trust.
A future PR may add a configurable allowlist for post-redirect host
patterns.

## How `huggingface_hub` clients see the fallback

Because the classifier mirrors `hf_raise_for_status`'s priority, a
`huggingface_hub` client downstream of KohakuHub experiences:

- **Bind-and-serve**: 2xx / 3xx body — `hf_raise_for_status` returns
  cleanly, the call succeeds.
- **Bind-and-propagate**: KohakuHub forwards the upstream's 4xx +
  `X-Error-Code` verbatim — the client raises `EntryNotFoundError` /
  `RevisionNotFoundError` / `DisabledRepoError`.
- **Chain exhaustion**: aggregate response with the priority code
  (`GatedRepo` if any source gated, else `RepoNotFound` / `EntryNotFound`
  per scope, else generic) — the client raises the matching exception.

Clients don't observe the chain itself. The `X-Source` /
`X-Source-URL` / `X-Source-Status` response headers expose chosen-source
information for debugging and operator telemetry.

## Testing

End-to-end coverage lives in `test/kohakuhub/api/fallback/`:

- `test_e2e_matrix.py` — ~50 scenarios via the real `huggingface_hub`
  library against a mock upstream (`scenario_hf_server.py`), one per
  status × `X-Error-Code` × op-type combination.
- `test_chain_enumeration.py` — exhaustive enumeration of chain length
  1..3 over 12 per-source scenarios for each of 4 ops (~4368 cases per
  op, runs in ~2 s using a single shared event loop).
- `test_strict_consistency.py` — proves the four strict-consistency
  rules: no rebind on transient bound failure; no source mixing within
  TTL; concurrent first-binders agree; admin invalidation forces re-probe.
- `test_decorators.py` — `with_repo_fallback` X-Error-Code gating;
  `with_user_fallback` namespace-existence DB check.
- `test_operations.py`, `test_utils.py`, `test_client.py`, `test_cache.py`
  — unit-level coverage of each module.
