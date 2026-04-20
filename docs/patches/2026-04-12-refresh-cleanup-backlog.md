# 2026-04-12 Refresh Mainline Cleanup Backlog

## Purpose

Turn the post-refresh state into a simple backlog of what still needs to be cleaned up on top of `cangwolf/runtime-patches-2026-04-12-refresh`.

This is **not** a donor-branch replay plan.
This is the shortlist for stabilizing and documenting the refreshed mainline.

## Current State In One Sentence

The fork mainline is already on the latest fetched `upstream/main`, the validated self-use patch line has been reapplied, and the remaining work is mostly **stabilization / cleanup / operational hardening**, not big new feature work.

## Priority 0 — Do before promoting this branch

### 1. Run one broader verification pass

Why:
- current confidence comes from focused buckets, not a full suite run
- before treating this branch as the new default mainline, we should catch any cross-area regressions once

Minimum target:
- full repo pytest, or the largest practical subset that covers runtime + CLI + harness + channels

Done when:
- we have a single recorded verification result for the refreshed branch
- any failures are either fixed or explicitly written down as accepted gaps

### 2. Do one local runtime smoke test

Why:
- the branch passed focused unit/integration buckets, but we still want one real command-path sanity check
- the historical patch playbook explicitly expected local runtime validation before cutover/promotion

Suggested smoke checks:
- local `nanobot agent` or equivalent runtime entrypoint starts cleanly
- workspace bridge path still resolves
- no obvious startup import/config crash in the refreshed branch

Done when:
- one short smoke-test note is written into the truth docs or commit log

## Priority 1 — Environment / packaging cleanup

### 3. Clean up `.venv` provisioning

Why:
- current verification depended on ad hoc local repair (`filelock`, `jinja2`)
- `uv sync --all-extras` was not clean because of external build-tool friction
- this is survivable for now, but fragile for future verification and cutover

Goal:
- document or fix a repeatable environment bootstrap path for this refreshed branch

Done when:
- a future verifier can recreate the env without needing hidden one-off repair steps

### 4. Revisit the parallel validation env note

Why:
- older docs still talk about a parallel runtime venv / `.pth` bridge cutover path
- after the 2026-04-12 refresh, we should make sure that advice still matches reality

Goal:
- either confirm the old parallel-env guidance is still correct
- or replace it with the current recommended verification environment flow

## Priority 2 — Documentation truth cleanup

### 5. Mark historical docs more aggressively

Why:
- we already marked the 2026-04-08 upstream-sync doc as historical
- but readers can still confuse older V2/subagent docs with current truth

Goal:
- make the doc stack obvious at a glance:
  - current truth docs
  - historical reference docs
  - explicitly deferred donor work

Good candidates:
- `docs/patches/2026-04-11-runtime-subagent-v2-draft.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-implementation-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-probe-boundary.md` (verify whether it is still normative or only historical support)

### 6. Add one plain-language branch map

Why:
- there are now multiple refresh / donor / rollback branches
- future-you should not have to rediscover which branch is current, which is superseded, and which is archive-only

Goal:
- one tiny document or ledger section saying:
  - current working mainline
  - rollback branch/tag
  - superseded branches safe to archive later
  - donor branches kept only as reference

## Priority 3 — Code cleanup worth doing later, not now

### 7. Queue operational polish

This is optional follow-up, not a blocker for the refresh branch.

Candidates already implied by the V3 docs:
- queue length / status visibility
- richer queue logs
- dedupe repeated queue requests in the same session
- later fairness improvements if starvation shows up in practice

### 8. Selective donor salvage only if architecture direction changes

Only revisit this if the fork later intentionally revives the V2 model-registry direction.

If that ever happens:
- start with the narrow fallback ideas from `72d7a3b`
- do not revive the whole donor branch
- do not let `model_state.json` become startup truth by accident

## Things We Should Explicitly Avoid Right Now

1. do **not** merge the `runtime-model-subagent-*` branches wholesale
2. do **not** replay repo-wide lint/baseline cleanup together with runtime patch work
3. do **not** expand the refreshed branch into a new architecture migration unless that is a deliberate new task

## Recommended Next Execution Order

1. broader verification pass
2. local runtime smoke test
3. `.venv` / bootstrap cleanup note or fix
4. doc-truth cleanup for historical vs current patch docs
5. only then consider promotion / archive actions
