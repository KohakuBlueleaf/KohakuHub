# Changelog

All notable changes to KohakuHub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Fallback cache: strict-freshness contract** ([#79](https://github.com/deepghs/KohakuHub/issues/79)).
  Cache key now includes `user_id` and a `tokens_hash` derived from the
  user's effective external tokens. Two users (or one user with
  different per-request `Authorization: Bearer ...|url,token|...` external
  tokens) can no longer share a binding. Three monotonic generation
  counters (`global_gen`, `user_gens[uid]`, `repo_gens[(rt, ns, name)]`)
  drive a `safe_set` race-protection check that rejects cache writes
  whose pre-probe snapshot disagrees with the post-probe state — closing
  the admin-mutation / token-rotation / repo-CRUD races where a probe
  in flight could otherwise pin a stale binding after an invalidation.
- **Fallback cache: invalidation hooks** ([#79](https://github.com/deepghs/KohakuHub/issues/79)).
  `cache.invalidate_repo(repo_type, ns, name)` and
  `cache.clear_user(user_id)` provide the two new eviction primitives;
  hooked into local repo create/delete/move/visibility-toggle, user
  external-token POST/DELETE/PUT bulk, and admin source create (the
  last one parallels the existing UPDATE/DELETE that already evicted).
- **Admin endpoints: per-repo and per-user fallback cache eviction**
  ([#79](https://github.com/deepghs/KohakuHub/issues/79)). New
  `DELETE /admin/api/fallback-sources/cache/repo/{repo_type}/{namespace}/{name}`
  and `DELETE /admin/api/fallback-sources/cache/user/{user_id}` for
  operational hygiene.
- **Fallback: repo-grain binding.** Within a single bind window every read
  against one `repo_id` goes to exactly one source. Eliminates cross-source
  mixing where the SPA showed source A's metadata while `resolve` served
  source B's bytes for the same repo. Implements the contract from #75.
  ([#77](https://github.com/deepghs/KohakuHub/pull/77))
- **Fallback: per-loop, per-repo binding lock.** Concurrent cache-miss
  callers serialize on the lock so all observers agree on the same bound
  source instead of independently scanning the chain. ([#77](https://github.com/deepghs/KohakuHub/pull/77))
- **Fallback: three-state classifier `FallbackDecision`.**
  `BIND_AND_RESPOND` / `BIND_AND_PROPAGATE` / `TRY_NEXT_SOURCE` in
  `kohakuhub.api.fallback.utils.classify_upstream`, mirroring
  `huggingface_hub.utils.hf_raise_for_status` priority order
  (`X-Error-Code` wins over numeric status). ([#77](https://github.com/deepghs/KohakuHub/pull/77))

### Changed

- **Fallback: `disabled` upstream marker now classifies as `TRY_NEXT_SOURCE`
  instead of `BIND_AND_PROPAGATE`.** When HuggingFace returns
  `X-Error-Message: "Access to this resource is disabled."` (moderation
  takedown), the chain now advances to the next source rather than
  immediately propagating `DisabledRepoError`. The aggregate response
  surfaces the `disabled` marker only when **every** source in the chain
  returns disabled. `huggingface_hub` clients are unaffected — they don't
  inspect chain internals — but tooling that depended on first-source
  `disabled` propagating verbatim must update. ([#77](https://github.com/deepghs/KohakuHub/pull/77))
- **Fallback: `with_repo_fallback` decorator gates the 404 fall-through on
  the local response's `X-Error-Code`.** A local 404 carrying
  `EntryNotFound` or `RevisionNotFound` is now authoritative ("the local
  repo exists; this entry/revision is missing") and is **not** dispatched
  to the fallback chain. The chain is entered only when the local layer
  signals `RepoNotFound` or returns a 404 without `X-Error-Code`.
  Restores the user-stated guarantee that a local repo wins absolutely on
  its namespace, regardless of upstream state. ([#77](https://github.com/deepghs/KohakuHub/pull/77))

### Tracked

Planned follow-up work surfaced during the [#77](https://github.com/deepghs/KohakuHub/pull/77) risk review:

- [#78](https://github.com/deepghs/KohakuHub/issues/78) — lower default
  fallback cache TTL (`KOHAKU_HUB_FALLBACK_CACHE_TTL`) from `300` to `60`,
  decouple chain-probe logic into a pure `core.probe_chain` function, and
  add admin endpoints + frontend panel for real / simulated chain testing.
