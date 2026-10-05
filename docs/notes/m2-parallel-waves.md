# M2 parallel waves — 2026-08-06

The first **parallel** wave shipped live: two Issues, two real worker contexts running concurrently in isolated worktrees, merged back one at a time with integrated re-Verify. `concurrency = 2`, haiku roster, 2:48 wall time, exit 0.

## The topology is the proof

```
* 542ca6a giro(textkit): state -> done
* a378ccd giro(textkit/02-truncate): state -> done
*   bfaa363 giro(textkit): merge giro-wt/textkit/02-truncate
|\
| * 43b2a27 giro(textkit/02-truncate): attempt 1
* | ee69981 giro(textkit/01-slugify): state -> done
* |   b8a293c giro(textkit): merge giro-wt/textkit/01-slugify
|\ \
| |/
|/|
| * bf50ad2 giro(textkit/01-slugify): attempt 1
|/
* cd6dbcd giro(textkit): claim wave [01-slugify, 02-truncate]
* c895ef7 giro(textkit): activate
```

Both worker branches fork from the same claim-wave commit — they really ran from the same head, at the same time (two `claude -p` processes and three worktrees were observed live mid-run) — then land serially, each behind its own `--no-ff` merge and an integrated `[verify]` run on the merged result. Worktrees and worker branches were gone at exit; the checkout ends clean on `giro/textkit`.

## The invariant that makes it safe

**Only the engine's main thread touches the store or the integration branch.** Worker threads get a store-free attempt cycle (`_attempt_cycle`) in their own worktree on their own `giro-wt/<slug>/<issue>` branch. Everything serialized: claim-all (one commit), merges, integrated verify, state + log writes.

Collision policy, as designed: a merge conflict or an integrated-verify failure resets the integration branch to pre-merge and sends the Issue back to `ready` with findings — the retry runs from the *updated* HEAD, which is the serialization of colliding scopes. Bounded by the same attempt budget as everything else. All three paths (clean merge, add/add conflict retry, integrated failure reset) are covered by fake-driver tests; the conflict and reset paths were not exercised in this live run.

## Incidents → tests

- `git` refuses a branch named `giro/<slug>/<issue>` when `giro/<slug>` exists (refs can't nest under an existing ref) — worker branches are `giro-wt/…`. Caught by the first parallel test run, one minute after writing the code.
- Stale `in-progress` states from a crashed run are now re-claimed by the frontier (the engine is the single writer, so a stale claim is always safe) — previously they could leak into Validate.
