# The invoking checkout is sacred

`giro implement` used to check out the Spec branch in the human's own working copy, demand it clean, and block the terminal — one running loop hijacked the workspace, and a second Spec could not run at all. Decided: every loop runs in a persistent per-Spec worktree on `giro/<slug>`, forked from an explicit base branch recorded in Spec frontmatter at first activation; the engine never switches, gates, or writes the invoking checkout.

Consequences: dirty-tree dispatch, concurrent Specs, and detached Runs become possible; both concurrency shapes now run in worktrees, so no worker ever sees git-ignored local files — gates must be hermetic.
