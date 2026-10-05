"""Runs — one execution of the engine for one target.

A Run holds its Spec's lock for its whole life: ``giro/<slug>`` has one engine
working it, or none, never two. The lock lives in the engine's runtime
directory, names the process holding it, and is reclaimed when that process is
gone — a machine that lost power must not need a manual unlock. A live lock
refuses the second Run loudly and at once; it never queues.

The same runtime directory holds the checkout's worker slots: dispatching
freely must not melt the machine, so every worker context — whichever Run it
belongs to — takes one of a fixed number of slots first, and waits when they
are all taken.
"""

from __future__ import annotations

import sys

# giro is POSIX-only. `fcntl` (advisory locks), POSIX hardlinks, and
# `start_new_session` (for detached Runs) have no working equivalent on
# Windows, and no CI machine we ship against runs Windows. Fail at import
# time with one clean line — a Windows user should not chase an obscure
# error deep inside the engine, and WSL works fine.
if sys.platform == "win32":  # pragma: no cover — not exercised on Linux CI
    raise ImportError(
        "giro requires a POSIX platform — Windows is not supported "
        "(giro.runs uses fcntl, hardlinks, and start_new_session). "
        "Use WSL, a Linux VM, or a container."
    )

import fcntl
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SLOT_POLL = 0.1  # seconds between attempts to take a busy worker slot


class RunError(Exception):
    """A Run cannot start — another Run already holds this Spec."""


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def process_alive(pid: int) -> bool:
    """Whether the process that took a lock or a slot is still there.

    ``kill(pid, 0)`` asks without signalling. A PermissionError means the
    process exists but belongs to someone else — alive, and still holding.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(path: Path) -> dict[str, object] | None:
    """The holder a lock file names, or None when it is gone or unreadable —
    an unreadable lock is a corpse, not a claim."""
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return holder if isinstance(holder, dict) else None


def _holder_pid(holder: dict[str, object] | None) -> int:
    """The process a lock claims, or 0 when it claims nothing usable — an
    unreadable claim is treated as no claim, never as a crash."""
    try:
        return int(holder["pid"])  # type: ignore[arg-type, index]
    except (KeyError, TypeError, ValueError):
        return 0


def _describe(holder: dict[str, object] | None) -> str:
    if not holder:
        return "holder unknown"
    return (
        f"pid {holder.get('pid', '?')}, target {holder.get('target', '?')!r}, "
        f"started {holder.get('started', '?')}"
    )


def _notice(message: str) -> None:
    """A runtime notice goes to stderr: it is not the Run's result, and stdout
    belongs to the report a human or a machine reads."""
    print(f"giro: {message}", file=sys.stderr)


@contextmanager
def spec_lock(runtime: Path, slug: str, target: str) -> Iterator[Path]:
    """Hold the Spec's lock for the life of a Run.

    The lock is a hardlink from a file that *already* names its holder, so it
    never exists without saying whose it is — the discipline the worker slots
    keep (see ``_take_slot``). A create-then-write lock is empty for as long as
    it takes to name itself, and a second Run reclaiming stale locks in that
    instant would read no holder, call the fresh lock a corpse, and take it too:
    two Runs on one Spec, the one thing the lock exists to prevent. Because the
    lock is whole the moment it exists, a live holder is always visible as live
    and refused; only a dead holder's lock is reclaimed — and reclaiming
    happens under a per-runtime mutex, so two reclaimers cannot both judge and
    both unlink and both re-link. Unlike a slot, a live lock is a refusal, not
    a wait.
    """
    locks = runtime / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    path = locks / f"{slug}.json"
    mine = {"pid": os.getpid(), "slug": slug, "target": target, "started": now_iso()}
    # A file that already names us; the lock is a hardlink of it, never empty.
    staging = locks / f".claim-{os.getpid()}-{threading.get_ident()}"
    staging.write_text(json.dumps(mine), encoding="utf-8")
    try:
        take = staging.stat().st_ino  # our claim's identity, carried by the hardlink
        try:
            os.link(staging, path)
        except FileExistsError:
            # Reclaim is judged AND performed under one mutex: two reclaimers
            # cannot both read no live holder, both unlink, and both re-link.
            with _reclaim_mutex(locks):
                # Re-read the holder inside the mutex — the winner of a prior
                # race may have taken it in the interval since we saw
                # FileExistsError.
                if path.exists():
                    holder = _read(path)
                    if process_alive(_holder_pid(holder)):
                        raise RunError(_taken(slug, holder)) from None
                    _notice(
                        f"reclaimed a stale lock on spec {slug!r} — the Run that held it "
                        f"({_describe(holder)}) is gone"
                    )
                    path.unlink(missing_ok=True)  # a dead holder's lock, or damage
                # Fresh link inside the mutex: no second reclaimer can slip
                # between the judge and this link (both would be serialized
                # here), so this either succeeds or the lock has just been
                # taken by a live process (impossible under the mutex — the
                # only way in is through this same critical section).
                try:
                    os.link(staging, path)
                except FileExistsError:  # only if a non-mutex-holder linked
                    raise RunError(_taken(slug, _read(path))) from None
    finally:
        staging.unlink(missing_ok=True)
    try:
        yield path
    finally:
        _give_back(path, take)  # release only while it is still the lock we took


def _taken(slug: str, holder: dict[str, object] | None) -> str:
    return (
        f"spec {slug!r} already has a live Run ({_describe(holder)}) — one Run per "
        "Spec, so this dispatch is refused rather than queued: wait for that Run to "
        "finish, or stop that process"
    )


def refuse_if_locked(runtime: Path, slug: str) -> None:
    """Refuse a dispatch that the Spec's live Run would refuse anyway.

    The lock is still taken by the Run itself — this is not the decision, it is
    the early word, said at the terminal where the human is standing rather
    than into a background process's Ledger. Losing the race just means the
    refusal arrives the usual way instead.
    """
    path = runtime / "locks" / f"{slug}.json"
    holder = _read(path) if path.exists() else None
    if holder is not None and process_alive(_holder_pid(holder)):
        raise RunError(_taken(slug, holder))


@contextmanager
def worker_slot(runtime: Path, cap: int) -> Iterator[Path]:
    """Hold one of the checkout's worker slots for the life of a context.

    The cap spans every Run dispatched from one checkout (the slots live in its
    ``.giro/``), so dispatching freely cannot melt the machine or the budget.
    Runs from another clone of the repository have their own slots. Unlike
    the Spec lock, a full slot set is a *wait*, never a refusal — the Run
    takes its turn.

    Slots are files named by their holder, so the same stale-reclaim rule
    applies: a slot whose process is gone is free.
    """
    slots = runtime / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    mine = slots / f".take-{os.getpid()}-{threading.get_ident()}"
    mine.write_text(json.dumps({"pid": os.getpid(), "taken": now_iso()}), encoding="utf-8")
    try:
        take = mine.stat().st_ino  # the take's identity, carried by the hardlink
        path = _take_slot(slots, mine, max(cap, 1))
    finally:
        mine.unlink(missing_ok=True)
    try:
        yield path
    finally:
        _give_back(path, take)


def _give_back(path: Path, take: int) -> None:
    """Release a slot — but only while it is still the take we sat down in.

    A holder is never a corpse, so nothing may replace its slot underneath it;
    the check is the same guard the Spec lock keeps, said in inodes because a
    take is a hardlink and its inode is exactly which take it is.
    """
    try:
        if path.stat().st_ino == take:
            path.unlink(missing_ok=True)
    except OSError:
        pass  # already gone: nothing of ours is left to give back


def _take_slot(slots: Path, mine: Path, cap: int) -> Path:
    """Wait for one of ``cap`` slots and take it.

    The take is a hardlink from a file that *already* names its holder, so a
    slot file never exists without saying whose it is. An exclusive create
    would leave an empty file for as long as it takes to write the holder into
    it, and a context scanning for corpses in that instant would read no holder,
    call the slot free, and sit down in it — two live contexts in one slot, the
    cap exceeded by exactly the thing it exists to prevent.

    Linking comes first and reclaiming only answers its refusal, so the ordinary
    take — a free slot — costs one syscall and never touches the reclaim mutex.
    """
    while True:
        for index in range(1, cap + 1):
            path = slots / f"{index:02d}.json"
            try:
                os.link(mine, path)
            except FileExistsError:
                if not _reclaimed(slots, path):
                    continue  # a live context is sitting there
                try:
                    os.link(mine, path)
                except FileExistsError:
                    continue  # someone took the slot we freed — theirs, fairly
            return path
        time.sleep(SLOT_POLL)  # every slot is taken by a live context — wait for a turn


def _reclaimed(slots: Path, path: Path) -> bool:
    """Free a slot whose holder is gone, and say whether it was freed.

    The corpse is judged *inside* the mutex rather than before it. A slot that
    was a corpse a moment ago may be a live take by now — the context that
    reclaimed it has already sat down — and unlinking that would put two live
    contexts in one slot, which is the whole cap. Judging under the mutex closes
    that window: while a corpse is still there nobody can replace it, because
    taking is an exclusive link and every removal but the holder's own waits
    here. So the file removed is exactly the one judged dead.
    """
    with _reclaim_mutex(slots):
        if not _is_corpse(path):
            return False
        path.unlink(missing_ok=True)  # the context that held it is gone
    return True


@contextmanager
def _reclaim_mutex(where: Path) -> Iterator[None]:
    """Serialize corpse reclaim across every context on this machine.

    Shared by slot reclaim and spec-lock reclaim. An ``flock`` rather than a
    lock file, because this mutex is the one thing that must never need
    reclaiming itself: the kernel drops it when the holder dies, so a context
    killed mid-reclaim costs nothing and no second guard is needed to notice.
    It is held across a stat and an unlink+link, never across work. For a spec
    lock, judging the holder happens inside the mutex too, so no second
    reclaimer can arrive between the judge and the re-link — the whole
    reclaim decision is atomic.
    """
    where.mkdir(parents=True, exist_ok=True)
    fd = os.open(where / ".reclaim", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # which releases the lock, however this ended


def _is_corpse(path: Path) -> bool:
    """Whether a slot is free for the taking: it names a process, and that
    process is gone.

    Anything else is held. A slot we cannot read is not a take in flight —
    takes are whole (see ``_take_slot``) — so it is damage, and damage costs one
    slot until a person removes it. Reading it as free would cost the guarantee.
    """
    pid = _holder_pid(_read(path))
    return pid > 0 and not process_alive(pid)
