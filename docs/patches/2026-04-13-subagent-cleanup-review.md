# 2026-04-13 Subagent Cleanup Review Memo

## Purpose

Summarize what the recent cleanup sequence on the refreshed branch actually changed, what remains high-value to keep, and where the cleanup lane should slow down.

Scope of the reviewed sequence:

1. `09e6489` — trim first-layer spawn/prompt cleanup
2. `f6f5a14` — normalize standard-tier compatibility toward worker
3. `f2e6103` — shrink route selection helpers
4. `cb28b58` — centralize provider status mutation
5. `f559222` — clarify provider probe orchestration boundaries
6. `157e585` — type runtime probe request/result plumbing
7. `f4a642a` — remove repeated backend probe-builder glue

## Executive Summary

The cleanup sequence succeeded.

The live subagent/runtime-control patch is still intact, but the surrounding control-plane code is now noticeably easier to read:

- the **public spawn surface** is narrower
- the **standard-tier compatibility path** now points earlier toward the worker path
- **route selection** is less bucket-and-helper heavy
- **provider status mutation** no longer repeats the same write/update shape in multiple places
- **probe orchestration** now reads as a clear flow instead of scattered inline bookkeeping
- **runtime probe execution** uses a typed request object rather than raw tuples
- **backend probe builders** keep their backend-specific differences while sharing identical glue

In plain terms:

> the fork still behaves like the same fork, but more of its complexity is now explicit structure instead of accidental repetition.

## What Improved Materially

### 1. Better boundary between public API and compatibility API

The code now communicates more clearly that:
- preferred selectors are `type` / `model`
- `label` / `tier` remain compatibility inputs, not the preferred interface

This reduces future confusion about whether the fork is still primarily tier-driven. It is not.

### 2. Better boundary between intent and resource selection

`tier=standard` is no longer treated like a separate first-class design path. It now points toward the canonical `worker` path earlier, while still carrying compatibility fallback when worker candidates are absent.

This is a meaningful clarification, because it makes the live design more obviously:
- `worker`
- `explorer`
- explicit `model`

with tier compatibility at the edges.

### 3. Better boundary between queueing and selection policy

Route/candidate selection is still opinionated, but it is less structurally noisy.

The queue semantics were intentionally left alone, which was the right call. The cleanup reduced helper sprawl around selection without weakening the queue itself.

### 4. Better boundary between provider-health mutation and probe orchestration

Before this pass sequence, provider-health behavior was correct but spread across:
- manager-only mutations
- workspace persistence writes
- snapshot loading
- probe application

Now the repeated mutation shapes are centralized, and probe orchestration is easier to read as:
- decide strategy
- choose runner
- run probe
- annotate result
- apply status

### 5. Better boundary between backend-specific probe logic and shared plumbing

The builders for openai-compatible, Azure, Anthropic, and GitHub Copilot probes still expose the important differences:
- URL shape
- auth headers
- backend-specific payload details

But repeated glue such as:
- merging extra headers
- emitting the shared probe user message
- attaching reasoning effort when applicable

is now shared instead of copied.

## What Did Not Change

The cleanup deliberately did **not** change these behavioral cores:

- real queued subagent semantics
- guarded nested delegation model
- provider fallback before tool side effects
- route-health persistence semantics
- runtime-vs-workspace probe strategy behavior
- backend-specific probe request meaning

That is important.

A cleanup lane like this is only useful if it removes maintenance weight **without** making the fork's real operational behavior less reliable.

## Validation Snapshot

This memo is grounded in the currently passing protection buckets that were run after the cleanup sequence:

- focused probe/resource bucket: `16 passed, 25 deselected`
- broad subagent/runtime bucket: `110 passed`
- `ruff check nanobot/agent/subagent_resources.py --select F,W`
- `python -m py_compile nanobot/agent/subagent_resources.py`
- `git diff --check`

So the claim here is not just "the code looks cleaner".
It is:

> the code looks cleaner **and** the existing protection buckets still pass after the cleanup sequence.

## What Still Looks Like The Main Hotspots

### 1. `subagent_resources.py` is still the heaviest single file

It is cleaner now, but it still owns a lot:
- candidate resolution
- queue admission policy
- route status policy
- provider status persistence
- probe orchestration
- backend-specific probe request construction
- workspace fallback probing
- snapshot rebuilding

The file is more structured, but not yet small.

### 2. `tier=lite` remains a legacy-shaped compatibility area

The standard-tier path has been clarified much more than the lite path.

That is reasonable: lite is more entangled with archive/default profile behavior and historical resource assumptions. It should not be removed casually.

### 3. Workspace fallback probing is still a special compatibility boundary

`run_legacy_workspace_provider_probe()` remains necessary for old compatibility cases, but it is still one of the clearest signs that the fork carries legacy gravity.

It should stay obvious and isolated.

## Recommendation: Where To Stop For Now

My recommendation is to **pause major cleanup here** and switch to review mode unless a concrete pain point appears.

Why:

1. the recent passes already removed a meaningful amount of repetition
2. the remaining opportunities are less obviously low-risk
3. continuing to reshape the same area without a concrete operational pain could start trading clarity gains for churn

So the best current posture is:

> keep the queue, keep the policy, keep the fallback safety, and treat the recent cleanup sequence as a successful boundary-hardening pass rather than an excuse for endless refactor motion.

## Recommended Next Work Categories

If more work is needed later, prefer one of these categories:

### A. Operational verification
- run broader runtime-smoke checks around subagent execution in real harness scenarios
- verify workspace fallback paths only if they still matter in practice

### B. Compatibility retirement
- identify actual remaining call sites that still rely on `label` / `tier`
- retire them with evidence, not by guesswork

### C. File boundary work only when justified
- only split `subagent_resources.py` if there is a concrete maintenance reason
- do not split it just because it is large

## Bottom Line

The cleanup sequence did what it was supposed to do:

- it made the fork's **live design more legible**
- it removed **repetition and control-plane blur**
- it did **not** damage the fork's core runtime behavior

The right interpretation is not “keep refactoring forever.”
The right interpretation is:

> the subagent/resource lane is now in better shape, and future work should be driven by concrete operational value rather than cleanup momentum alone.
