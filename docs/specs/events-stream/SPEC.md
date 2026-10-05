---
state: draft
---
# Events Stream

## Problem Statement

Every Run already produces a structured, append-only stream — the Ledger's `events.jsonl` under `.giro/runs/<run-id>/` — with one JSON object per state-changing moment (activation, plan, wave, claim, attempt with verdicts and findings, merge, gap-cycle, escalation, projection, exit). The GitHub projection (M6) works because the Ledger is fanned into by every subsystem; there is nothing left to invent about *producing* the stream. What is missing is *consumption*: it is not a documented contract (a consumer parses a shape the code happens to emit), and there is no machine-friendly tail (a script tails a raw file it had to locate by convention, or shells out to `giro logs` which prints human prose). A chat agent driving giro today ends up polling `.giro/runs/*/events.jsonl` with `tail -f` and hoping the fields do not drift; a CI job wiring alerts either does the same or waits for the exit code.

Separately, `giro implement` in the foreground is silent while it runs. A human at a terminal has no sense of which Issue, attempt, or gate is in flight until proof or escalation arrives at the end. That silence is a different problem — humans read a terminal, machines read a stream — and today neither is served.

## Solution

Two orthogonal moves, each fitting a pattern the engine already uses elsewhere:

- **Promote the Ledger's event stream to a public contract**, the same way state in markdown is public. Every event carries a `schema_version`. The full schema — every `type`, every required and optional field, the ordering invariants — lives in `docs/design.md` under Runs. Adding a new event type or field is a versioned change; the schema doc and the emitter are held to each other by a test so drift becomes a failure.
- **Give machines the tail, give humans the terminal.** `giro logs --json` prints the Ledger's raw JSONL instead of the human rendering — same events, one JSON object per line, from any Run, live or finished, works with `--follow`. `giro implement` gains short **phase lines on stderr** while it runs — engine-owned milestones only (claim, worker, each gate, state transitions, wave merge) — with `--quiet` to suppress them. The final stdout report and exit codes are unchanged.

No `--events` flag on `implement`. Foreground consumers that want live events compose two commands the engine already offers: `giro implement --detach` returns the Run id, and `giro logs <run-id> --json --follow` tails it — the same shape a chat agent or CI job would use for a dispatched Run.

## User Stories

1. As a chat agent driving the `giro` doorway skill, I want to read live events from any Run as newline-delimited JSON, so that I can relay progress in the conversation without polling a file I had to locate by convention or scraping human prose.
2. As a CI job, I want to consume the same events from a running or finished Run, so that I can decide when to alert (post to Slack on the first escalation, fail the pipeline on validate exhaustion) without waiting for the final exit code.
3. As a skill author or an integration writer, I want the event schema documented in `docs/design.md` with a `schema_version` on every event, so that I can code against a stable contract that will not drift silently.
4. As a developer watching `giro implement` in a terminal, I want short phase lines on stderr as the loop advances, so that a long wait shows which Issue, attempt, or gate is running instead of a blank prompt.
5. As a CI job that only cares about the exit code and the final report, I want `--quiet` on `giro implement` to suppress phase lines, so that pipeline logs stay signal-heavy.
6. As an existing consumer of `giro implement` or `giro logs` without new flags, I want stdout, stderr, exit codes, commit graph, and Ledger contents byte-identical to today, so that no script needs to change to keep working.
7. As the human, I want `giro logs --json` to work on any Run — foreground, detached, live, or long finished — so that the tail is one command, not two variants.

## Implementation Decisions

- **The Ledger's emitter is the one source of events.** Every event that reaches `events.jsonl` reaches every consumer of the stream, automatically. No code path emits an event elsewhere first. Adding a new event type in the future needs one change in the emitter and one in the schema doc.
- **`schema_version` is a top-level field on every event**, alongside `seq`, `at`, and `type`. Set to `1` with this Spec. A consumer can refuse an unknown version; a bump is a semver-shaped signal that the contract changed.
- **The schema lives in `docs/design.md`** in a new "Events" subsection under Runs. It names every `type`, its required and optional fields, and the invariants (append-only, one per state-changing moment, monotonically increasing `seq`, main-thread-only writer). A doc-drift test walks a scripted end-to-end fixture Run, collects every event emitted, and asserts each event's `type` and every field it carries is named in the doc — the doc cannot silently drift from the code.
- **`giro logs --json`** prints one JSON object per line, ordered by `seq`, honouring `-n`/`--lines` and `--follow` the same way the human mode does. The `-n` semantics stay identical: skip earlier events, print the tail, follow the rest. The final line on a finished Run is the `exit` event.
- **Phase lines are emitted from the loop, not derived from events**, and go to stderr as short human strings — "claim wave …", "worker started …", "verify gate `check` failed (2 findings)", "state → done" — never JSON. They read the same triggers the emitter does, so silence in one path never means silence in the other; a wave-merge that emits an event also prints a phase line.
- **`--quiet` on `giro implement`** suppresses phase lines only. It does not touch stdout (the final report), does not touch the Ledger, does not touch exit codes.
- **`--events` is not a flag on `giro implement`.** It would be a convenience shortcut for "run in foreground and print JSONL to stdout at once," which composes from `--detach` + `giro logs --json --follow`. Adding it later stays possible; leaving it out keeps `implement`'s stdout contract simple (human report only) and stops two ways of getting the same thing.
- **The Ledger's on-disk format does not change.** `.giro/runs/<id>/events.jsonl` is where events land and where they are read from; `giro logs --json` is a rendering choice. Deleting a finished Run's Ledger still costs only observability.
- **The `giro` doorway skill learns the streaming surface** — a short note that `giro logs --json` is the machine tail — but its primary contract stays `giro status` and the final report; live streaming is a capability skills can reach for, not a requirement.
- **Vocabulary is already recorded upstream** (ADR-0010): Ledger, Run, event stream are in `CONTEXT.md` and the design doc. This Spec adds the schema documentation, not the vocabulary.

## Testing Decisions

External behaviour through the existing seams — the engine with a `FakeDriver` against real temporary git repositories, capturing stdout and stderr from CLI invocations — as `test_loops.py`, `test_ledger.py`, and `test_console.py` already do.

What matters:
- **The schema doc matches the code.** A scripted end-to-end fixture Run drives the engine through every event `type` the loops emit (activation, plan, wave, claim, attempt with verdicts and findings, merge, gap-cycle, escalation, projection, exit); the test collects every event, then asserts each `type` and every field name that appeared is documented in the `docs/design.md` Events subsection. Adding an undocumented event or field fails the build.
- **`schema_version` is on every event**, at its declared value, unconditionally — foreground, detached, projected or not, all Runs, all event types.
- **`giro logs --json`** on a finished Run prints valid JSONL with every event in `seq` order; `--follow` on a live Run prints events as they land and exits when the Run does; `-n 0` prints every event; a `run-id` argument, an omitted argument (newest Run), and a Run started elsewhere all work identically.
- **Phase lines on stderr**: a Run in the foreground produces at least one line for claim, one for the worker, one per gate, one for each state transition observed, and one per wave merge; a Run in `--quiet` produces none of them and its stdout final report is byte-identical to the noisy variant.
- **Backward compatibility**: `giro implement` and `giro logs` without new flags produce stdout, stderr, and exit codes byte-identical to today (existing tests remain green with no changes).
- **Parallel waves** (`concurrency > 1`) still produce events in a single monotonically-increasing `seq` order — only the main thread writes.
- **`giro logs --json`** on a Run that fell over mid-Ledger (or whose Ledger is truncated) prints whatever is parseable and stops, with a clear stderr note; it never invents an event or renders one twice.

Prior art: `test_ledger.py` for the emitter and reader; `test_console.py` for `giro logs` stdout; `test_loops.py` for `giro implement` end-to-end; `test_parallel.py` for main-thread-only writes.

## Out of Scope

- **`--events` on `giro implement`.** Not needed: `--detach` + `giro logs --json --follow` covers the machine consumer that wants a foreground-shaped experience, and every other consumer is already served by tailing the Ledger. Add it later if a real use case appears; do not add it up front.
- **Streaming full worker or judge tool transcripts.** Envelopes and gate findings live inside the event payloads; raw session dumps stay out of the Ledger and out of the stream.
- **A TUI, tmux layout, or interactive attach** to a running Run. Phase lines and `giro logs --json --follow` cover both audiences.
- **Cost per event.** Token and dollar accounting is a separate Spec that will extend the schema by adding fields under a bumped `schema_version`; the emitter and the tail will carry them when they exist.
- **Backpressure or consumer flow control.** `giro logs --json` is fire-and-forget on stdout; a consumer that reads too slowly gets pipe buffering, not a protocol.
- **Changing the Ledger's disk format** or its `.giro/runs/` layout. Events land at the same shape they always have.
- **Structured phase lines on stderr.** Phase lines are for humans; the machine surface is the JSONL stream. Do not add a JSON variant on stderr.
- **A "current Run" pointer or symlink** — a shortcut so a tool without a run id can find the newest Run — is a small ergonomic addition that this Spec does not need; `giro logs` (with no id) already resolves to the newest Run and covers the same case.
- **Retroactive `schema_version = 0` on Ledgers written before this Spec.** Consumers that meet an event with no `schema_version` field can treat it as pre-1; the engine emits `1` from this Spec onward.
