"""Workspace — the engine's hands on git.

Workers edit files; the engine commits. The Spec's work accumulates on a
``giro/<slug>`` branch; merging that branch into your main line stays a
human act, always.

The engine never works in the invoking checkout (ADR-0013): every loop runs
in a persistent per-Spec worktree under the runtime directory, so the human's
branch, index, and uncommitted edits are untouched while the machine runs.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

RUNTIME_DIR = ".giro"

TIMED_OUT = 124  # the conventional exit code for a bounded call that ran out of time
PUSH_TIMEOUT = 30  # seconds — a checkpoint's push is a Projection call, and those are bounded


class WorkspaceError(Exception):
    """A git operation failed or the tree is not in a usable state."""


class Workspace:
    def __init__(self, root: Path):
        self.root = root

    def _git(
        self,
        *args: str,
        check: bool = True,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run git in this tree.

        ``timeout`` bounds the call and closes its stdin: a bounded call is one
        that must answer rather than ask, and one that ran out of time answers
        like any other failure — a non-zero result, never a raised timeout.

        Spawns in its own session (F6) so a timeout can kill the whole process
        group — a git subprocess (e.g. an SSH-tunnelled push) cannot leave
        children behind. Reads output with ``errors='replace'`` so a garbled
        byte in git's stderr never raises inside the engine.

        Signs no commits (``commit.gpgsign=false``) — an interactive gpg
        pinentry would hang a bounded engine, and hooks are skipped
        (``--no-verify`` on ``commit``) so project pre-commit hooks do not
        judge engine bookkeeping. See F9.
        """
        base_env = env if env is not None else os.environ.copy()
        # Prevent an interactive gpg pinentry from hanging engine commits.
        base_env.setdefault("GIT_COMMIT_GPG_SIGN", "0")
        base_env.setdefault("GIT_TAG_GPG_SIGN", "0")
        argv = ["git", "-c", "commit.gpgsign=false", *args]
        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=base_env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise WorkspaceError(f"`git` not found on PATH: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            completed = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            completed = subprocess.CompletedProcess(
                argv,
                TIMED_OUT,
                "",
                f"git {' '.join(args)} timed out after {timeout}s",
            )
        if check and completed.returncode != 0:
            raise WorkspaceError(
                f"git {' '.join(args)} failed: {completed.stderr.strip()[:2000]}"
            )
        return completed

    def is_repo(self) -> bool:
        return self._git("rev-parse", "--git-dir", check=False).returncode == 0

    def ensure_clean(self) -> None:
        status = self._git("status", "--porcelain").stdout.strip()
        if status:
            raise WorkspaceError(
                f"working tree at {self.root} is not clean — commit or stash before "
                "running giro:\n" + status[:2000]
            )

    def has_commits(self) -> bool:
        return self._git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def current_branch(self) -> str:
        return self._git("branch", "--show-current").stdout.strip()

    def head_sha(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def branch_exists(self, branch: str) -> bool:
        return (
            self._git("rev-parse", "--verify", f"refs/heads/{branch}", check=False).returncode
            == 0
        )

    def remote_url(self, name: str = "origin") -> str:
        """A remote's URL, or "" when there is no such remote — how the
        Projection learns which repository it is rendering onto."""
        proc = self._git("remote", "get-url", name, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    # -- reading a branch without going there ---------------------------------

    def spec_branch_slugs(self) -> list[str]:
        """Every Spec that has a branch, whatever the checkout is standing on."""
        proc = self._git(
            "for-each-ref", "--format=%(refname:short)", "refs/heads/giro/", check=False
        )
        return sorted(
            name[len("giro/") :] for name in proc.stdout.split() if name.startswith("giro/")
        )

    def read_blob(self, ref: str, relpath: str) -> str | None:
        """A file's content at ``ref``, or None when the ref has no such file."""
        proc = self._git("show", f"{ref}:{relpath}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    def list_tree(self, ref: str, relpath: str) -> list[str]:
        """The names directly under a directory at ``ref``; empty when absent."""
        proc = self._git("ls-tree", "--name-only", f"{ref}:{relpath}", check=False)
        if proc.returncode != 0:
            return []
        return [name for line in proc.stdout.splitlines() if (name := line.strip())]

    def ahead_behind(self, branch: str, base: str) -> tuple[int, int] | None:
        """Commits ``branch`` has that ``base`` lacks, and the other way round —
        the drift between a Spec's work and the branch it will merge into. None
        when either ref is missing."""
        proc = self._git("rev-list", "--left-right", "--count", f"{base}...{branch}", check=False)
        parts = proc.stdout.split()
        if proc.returncode != 0 or len(parts) != 2:
            return None
        behind, ahead = parts  # left = base-only, right = branch-only
        return int(ahead), int(behind)

    # -- the engine's own workshop --------------------------------------------

    def runtime_path(self) -> Path:
        """Where the runtime directory is — without making it. Asking after a
        Run (status, `giro runs`) must never create the engine's workshop."""
        return self.root / RUNTIME_DIR

    def runtime_dir(self) -> Path:
        """The engine-owned runtime directory, ignored by its own ``.gitignore``.

        A single ``*`` rule inside it means git cannot see anything under it on
        any branch, so neither the engine's ``add -A`` nor a human's can ever
        commit a worktree, a lock, or a Ledger.
        """
        path = self.runtime_path()
        path.mkdir(parents=True, exist_ok=True)
        ignore = path / ".gitignore"
        if not ignore.is_file():
            ignore.write_text("*\n", encoding="utf-8")
        return path

    def spec_worktree_path(self, slug: str) -> Path:
        return self.root / RUNTIME_DIR / "worktrees" / slug

    def ensure_spec_worktree(self, slug: str, base_branch: str = "") -> tuple[Workspace, bool]:
        """The persistent worktree holding ``giro/<slug>``, made on first activation.

        Returns ``(workspace, first_activation)``. ``first_activation`` is true
        only when the branch itself was just forked from ``base_branch`` — a
        worktree re-created for an existing branch resumes, it never re-seeds.
        ``base_branch`` is only needed for that fork; a resume passes nothing.
        """
        branch = f"giro/{slug}"
        path = self.spec_worktree_path(slug)
        self.runtime_dir()  # re-assert the ignore rule on every Run, not just the first
        if (path / ".git").exists():
            return Workspace(path), False

        self.prune_worktrees()  # drop admin entries a deleted directory left behind
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.branch_exists(branch):
            if self.current_branch() == branch:
                raise WorkspaceError(
                    f"branch {branch} is checked out at {self.root} — the engine needs it "
                    f"for its own worktree; switch that checkout to another branch "
                    f"(the Spec's work is on {branch} either way) and run giro again"
                )
            self._git("worktree", "add", str(path), branch)
            return Workspace(path), False
        self._git("worktree", "add", "-b", branch, str(path), base_branch)
        return Workspace(path), True

    def commit_all(self, message: str) -> bool:
        """Stage and commit everything. Returns False when there was nothing to commit.

        ``--no-verify`` (F9): the engine's commits are bookkeeping — pre-commit
        hooks are project judgment, not engine judgment, and running them on
        every state-change would make the loop's determinism a hook's problem.
        A worker's own changes are still judged by the ``[verify]`` gate set,
        which is the engine's contract for what "green" means.
        """
        self._git("add", "-A")
        staged = self._git("diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            return False
        self._git("commit", "--no-verify", "-m", message)
        return True

    def commit_within(self, relpath: str, message: str) -> bool:
        """Stage and commit only what changed under one path. False when nothing did.

        The pathspec is carried through every step so nothing outside it can be
        swept in: whatever else the tree holds is still there, uncommitted, for
        the caller that cares to refuse it.

        Status decides whether there is anything to do at all — ``git add`` on a
        pathspec matching nothing is fatal, and a path that does not exist yet
        (a Spec's directory before it is seeded) is the ordinary case, not a
        failure.

        ``--no-verify`` (F9): as for ``commit_all`` — engine bookkeeping is
        not the project's pre-commit hooks' business.
        """
        if not self._git("status", "--porcelain", "--", relpath).stdout.strip():
            return False
        self._git("add", "-A", "--", relpath)
        if self._git("diff", "--cached", "--quiet", "--", relpath, check=False).returncode == 0:
            return False
        self._git("commit", "--no-verify", "-m", message, "--", relpath)
        return True

    def push(self, branch: str, remote: str = "origin", timeout: int = PUSH_TIMEOUT) -> bool:
        """Publish a branch as it stands. False when it could not be published.

        Bounded and mute, because a checkpoint's push is a Projection call and
        those fail soft (ADR-0014): a dead network costs visibility, not work.
        Git is told never to ask — a missing credential or an unknown host key
        exits non-zero instead of blocking on a stdin nobody is typing into —
        and the timeout catches the remote that hangs without asking anything.

        Never forced, by construction: a push happens only at a post-decision
        checkpoint, after every reset this Run was going to make, so published
        history only grows and a rejected push is a fail-soft answer rather
        than something to overwrite (ADR-0014).
        """
        proc = self._git(
            "push",
            "--set-upstream",
            remote,
            branch,
            check=False,
            timeout=timeout,
            env=self._mute_env(),
        )
        return proc.returncode == 0

    def remote_branch_exists(
        self, branch: str, remote: str = "origin", timeout: int = PUSH_TIMEOUT
    ) -> bool:
        """Whether the remote already carries a branch — asked, never made.

        Reconcile publishes nothing: a push is a write, and the Projection runs
        one way (ADR-0014). So a pull request it would open needs its head to be
        there already, and this is how it finds out. Bounded and mute like a
        push, and false whenever git will not say.
        """
        proc = self._git(
            "ls-remote",
            "--heads",
            remote,
            f"refs/heads/{branch}",
            check=False,
            timeout=timeout,
            env=self._mute_env(),
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def _mute_env(self) -> dict[str, str]:
        """The environment a remote call runs in: git never asks a question a
        missing credential or an unknown host key would block on."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        ssh = env.get("GIT_SSH_COMMAND", "").strip() or "ssh"
        env["GIT_SSH_COMMAND"] = f"{ssh} -o BatchMode=yes"
        return env

    def diff_since(self, base: str) -> str:
        if not base:
            return self._git("show", "--stat", "HEAD").stdout
        return self._git("diff", f"{base}..HEAD").stdout

    def working_tree_diff(self) -> str:
        """Uncommitted changes vs HEAD, plus untracked files — what `giro verify`
        judges. Empty when the tree is clean."""
        tracked = self._git("diff", "HEAD", check=False).stdout
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard", check=False
        ).stdout.strip()
        parts = []
        if tracked.strip():
            parts.append(tracked.rstrip())
        if untracked:
            listing = "\n".join(f"  {p}" for p in untracked.splitlines())
            parts.append(f"Untracked files:\n{listing}")
        return "\n\n".join(parts) if parts else "(working tree clean — no changes to judge)"

    # -- worktrees & integration (parallel waves) ----------------------------

    def add_worktree(self, path: Path, branch: str, start_ref: str) -> Workspace:
        """Create an isolated worktree on a fresh branch; return its Workspace."""
        self._git("worktree", "add", "-b", branch, str(path), start_ref)
        return Workspace(path)

    def remove_worktree(self, path: Path, branch: str) -> None:
        """Discard a worktree and its branch. Best-effort — never raises."""
        self._git("worktree", "remove", "--force", str(path), check=False)
        self._git("branch", "-D", branch, check=False)

    def prune_worktrees(self) -> None:
        """Drop administrative entries for worktrees whose directories are gone
        — e.g. after a hard-killed wave — so a fresh wave can reuse the branch."""
        self._git("worktree", "prune", check=False)

    def merge_no_ff(self, branch: str, message: str) -> bool:
        """Merge a worker branch into the current branch. False on conflict (aborted)."""
        proc = self._git("merge", "--no-ff", "-m", message, branch, check=False)
        if proc.returncode != 0:
            self._git("merge", "--abort", check=False)
            return False
        return True

    def reset_hard(self, ref: str) -> None:
        self._git("reset", "--hard", ref)

    def uncommit_to(self, ref: str) -> None:
        """Move HEAD (and the index) back to ``ref`` and keep the working tree:
        commits made since ``ref`` become uncommitted changes. A no-op when
        HEAD is still ``ref``. Used to undo commits a worker made on its own."""
        if self.head_sha() != ref:
            self._git("reset", "--mixed", "-q", ref)

    def changed_paths(self) -> list[str]:
        """Every path that differs from HEAD or is a new untracked file — the
        raw material the guardrail against protected-path edits reads.

        Includes tracked modifications and untracked files; excludes ignored
        files (workspace ``.giro``/ debris a worker cannot see anyway). Read
        NUL-separated (``-z``), so a non-ASCII or odd name arrives verbatim
        rather than C-quoted past the guardrail's prefix match."""
        tracked = self._git("diff", "--name-only", "-z", "HEAD", check=False).stdout
        untracked = self._git(
            "ls-files", "-z", "--others", "--exclude-standard", check=False
        ).stdout
        return _nul_split(tracked) + _nul_split(untracked)

    def ignored_paths(self, pathspecs: list[str]) -> list[str]:
        """Untracked files under ``pathspecs`` that git ignores — invisible to
        :meth:`changed_paths`, so a worker could hide an edit behind a
        ``.gitignore`` rule. The protected-path guardrail reads these too."""
        if not pathspecs:
            return []
        out = self._git(
            "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", *pathspecs,
            check=False,
        ).stdout
        return _nul_split(out)

    def ignored_snapshot(self, pathspecs: list[str]) -> dict[str, tuple[int, int]]:
        """``ignored_paths`` with each file's (size, mtime) — taken before a
        worker runs, so the guardrail blames only ignored files the attempt
        created or changed, never a gate's own earlier output."""
        snap: dict[str, tuple[int, int]] = {}
        for rel in self.ignored_paths(pathspecs):
            try:
                st = (self.root / rel).lstat()
            except OSError:
                continue
            snap[rel] = (st.st_size, st.st_mtime_ns)
        return snap

    def ignored_probes(self, probes: list[str]) -> dict[str, str]:
        """Which of ``probes`` git would ignore, each mapped to the ignore file
        whose rule does it. Negated (``!``) matches are not ignores."""
        if not probes:
            return {}
        # -z needs --stdin; the probes are fixed ASCII names, and quotePath off
        # keeps a non-ASCII ignore-file path verbatim.
        out = self._git(
            "-c", "core.quotePath=false", "check-ignore", "-v", "--no-index", "--", *probes,
            check=False,
        ).stdout
        hits: dict[str, str] = {}
        for line in out.splitlines():
            where, _, path = line.partition("\t")
            source, _, rest = where.partition(":")
            pattern = rest.partition(":")[2]
            if source and path and not pattern.startswith("!"):
                hits[path] = source
        return hits

    def discard_changes(self) -> list[str]:
        """Drop every uncommitted change and untracked (not ignored) file, and
        say which paths went. Used when an attempt cycle escalates, so the
        engine's worktree is clean for the Run that answers it."""
        paths = self.changed_paths()
        if paths:
            self._git("reset", "-q", "--hard", "HEAD")
            self._git("clean", "-fdq")
        return paths

    def revert_paths(self, paths: list[str]) -> None:
        """Discard worker changes under ``paths``: unstage, restore from HEAD,
        and remove any untracked debris. Best-effort — used to enforce the
        protected-path guardrail before the next attempt starts."""
        if not paths:
            return
        # Unstage first, so a staged new file is untracked and cleanable.
        self._git("reset", "-q", "HEAD", "--", *paths, check=False)
        # Restore tracked files that were modified/deleted. Only paths HEAD
        # knows: one unknown pathspec makes git refuse the whole checkout.
        known = _nul_split(
            self._git(
                "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths, check=False
            ).stdout
        )
        if known:
            self._git("checkout", "HEAD", "--", *known, check=False)
        # Remove untracked additions under those same paths, ignored ones too
        # (a worker may have hidden them behind a .gitignore rule).
        self._git("clean", "-fdx", "--", *paths, check=False)


def _nul_split(out: str) -> list[str]:
    """Paths from git's ``-z`` output: NUL-separated, never quoted."""
    return [p for p in out.split("\0") if p]
