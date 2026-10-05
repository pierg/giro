# M1 first light — 2026-08-06

The first Spec shipped end-to-end by the engine with live LLM contexts, on the first attempt after auth. Target: a scratch repo with an **empty** Spec (no Issues), so the run exercised every context role.

## The run

```
$ giro implement slugify
slugify: all-done — validate passed — merge branch giro/slugify when ready
  slugify/01-implement-slugify-function-with-unit-tests: done
$ echo $?
0
```

Roster: `claude` driver, `haiku` model for all three roles. Budgets: 2 attempts, 1 validate cycle. Wall time ≈ 3.5 minutes.

## The engine's footprints (git log, oldest first)

```
0ee5e17 seed
2b22884 giro(slugify): activate
6eaf7c8 giro(slugify): plan 1 issue(s)
6e9e70e giro(slugify/01-implement-slugify-function-with-unit-tests): claim attempt 1
7d34e1c giro(slugify/01-implement-slugify-function-with-unit-tests): attempt 1
92eaf47 giro(slugify/01-implement-slugify-function-with-unit-tests): state -> done
9d37cfc giro(slugify): state -> done
```

Exactly the designed sequence — activation committed before any driver call, claim separated from worker output, state moves as their own commits, work left on `giro/slugify` for the human merge.

## What each live context did

- **Planner** (haiku): decomposed the Spec into exactly **one** Issue — respected the Spec's "do not over-decompose" — with concrete, testable acceptance criteria (`slugify('foo---bar') == 'foo-bar'`).
- **Worker** (haiku): wrote a clean `slugify(text)` (regex collapse, strip, empty-input guard) plus `test_slugify.py` with **11 unittest cases**. First attempt, no retries.
- **Command gate**: `python3 -m unittest discover` — green; re-run by hand afterwards to confirm.
- **Judge** (haiku): `spec-fit` prompt gate on the Spec + full branch diff — pass, well-formed verdict envelope.

## What the road here taught (all pinned as regression tests)

1. **Fail loud or debug blind.** The first launch died with `claude exited 1: ` — the actual error ("OAuth session expired") was inside claude's stdout JSON with `is_error: true`. Drivers now surface the CLI's own message, even at exit 0.
2. **A crash must never wedge the store.** That same failure left Spec activation uncommitted, so the retry refused at the dirty-tree guard. Activation now commits before any driver call.
3. **Engine bookkeeping ≠ worker output.** Claim commits are separated from attempt commits so "worker produced no changes" stays detectable.
4. Fenced JSON (```` ```json ````) from models is the common case — `extract_json` takes the last complete object, which handles it.
