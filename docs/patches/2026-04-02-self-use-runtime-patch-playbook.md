# Self-Use Runtime Patch Playbook

## Goal

Record both:

1. why this fork carries self-use runtime patches; and
2. how to rebuild these patches onto a newer `HKUDS/nanobot` upstream base.

This document is for the self-use fork only. It is not an upstream PR plan.

---

## Why These Patches Exist

The live service uses a set of local runtime changes that are intentionally outside the current upstream baseline. The main patch themes are:

1. provider retry / timeout UX hardening
2. context compression / memory consolidation routing and fail-open behavior
3. workspace bridge integration for slash-command routing and postprocess hooks
4. local runtime compatibility fixes across selected providers / channels / tools
5. focused tests that lock down the above self-use behavior

The purpose of this fork is to keep those self-use patches on top of a clean upstream base, so future updates can be rebased or re-transplanted without relying on `/home/admin/nanobot-main-live` as a long-term git source of truth.

---

## What Is In Scope

In scope for this fork branch:

1. `nanobot/` runtime code
2. runtime-facing tests under `tests/`
3. runtime patch docs under `docs/patches/`

Out of scope:

1. `~/.nanobot/workspace/scripts/*`
2. workspace handoff / report / task / memory assets
3. other local user data under `~/.nanobot/workspace`

Important: some runtime code still calls `~/.nanobot/workspace/scripts/router.py`. That dependency is intentionally preserved as an external local companion asset and is not moved into this repo.

---

## Rebuild Procedure On A New Upstream Base

When upstream moves forward, rebuild the self-use patch set with this sequence.

### 1. Prepare Clean Base

```bash
git clone https://github.com/CangWolf17/nanobot.git /home/admin/nanobot-fork-live
cd /home/admin/nanobot-fork-live
git remote add upstream https://github.com/HKUDS/nanobot.git
git fetch origin
git fetch upstream
git checkout -b cangwolf/runtime-patches-YYYY-MM-DD upstream/main
```

### 2. Use The Live Tree As Patch Source, Not As Git Base

Current live source tree:

```text
/home/admin/nanobot-main-live
```

Treat it as a file source only.

Do not try to push it directly as the long-term fork baseline, because that tree may have an incomplete or non-standard git index.

### 3. Overlay Runtime Files Without Deleting Upstream-Only Files

Apply local runtime files onto the clean clone by copying only modified / added local files.

Rules:

1. copy local `M` and `A` files from `nanobot/`, `tests/`, and `docs/patches/`
2. ignore `__pycache__/`, `*.pyc`, backup files, and other local junk
3. do **not** blindly delete files that exist only in upstream

This keeps new upstream files unless a later review decides a deletion is intentional.

### 4. Review Patch Surface

Before cutover, inspect:

```bash
git status --short
git diff --stat
git diff --name-only
```

Focus review on these buckets:

1. `nanobot/agent/*`
2. `nanobot/providers/*`
3. `nanobot/cli/*`
4. `nanobot/command/*`
5. `nanobot/config/*`
6. corresponding `tests/*`
7. `docs/patches/*`

### 5. Validate In A Parallel Runtime

Create and use a separate virtualenv, for example:

```bash
python3 -m venv /home/admin/.nanobot-fork/venv
/home/admin/.nanobot-fork/venv/bin/pip install -e /home/admin/nanobot-fork-live
```

Then verify:

1. focused tests for transplanted runtime patches
2. local `nanobot agent` smoke
3. workspace-bridge command path still resolves `~/.nanobot/workspace/scripts/router.py`

Current 2026-04-02 cutover note:

1. the parallel runtime uses `/home/admin/.nanobot-fork/venv`
2. because the package mirror did not provide one required dependency version during cutover, the new venv currently bootstraps dependencies through a `.pth` bridge into `/home/admin/.nanobot/venv/lib/python3.12/site-packages`
3. code import priority still points to `/home/admin/nanobot-fork-live` first
4. this is acceptable for the current self-use cutover, but should be revisited if a fully isolated dependency install becomes necessary later

### 6. Cut Over Only After Validation

The service cutover should only change `ExecStart` to the new venv entrypoint.

Keep these old paths intact for rollback:

```text
/home/admin/nanobot-main-live
/home/admin/.nanobot/venv/bin/nanobot gateway
```

Current rollback commands:

```bash
cp /home/admin/nanobot.service.backup.20260402-cutover /home/admin/.config/systemd/user/nanobot.service
systemctl --user daemon-reload
systemctl --user restart nanobot
```

---

## Current Branch Intent

The branch carrying these patches is expected to remain self-use and fork-local. It exists to make future upstream syncs easier, not to prepare a PR against the official repository.
