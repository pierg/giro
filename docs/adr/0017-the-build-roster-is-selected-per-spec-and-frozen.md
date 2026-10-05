# The build roster is selected per-Spec and frozen at Run start

The "who and how" of a build — the roster, runner, and budget — lived in one project-wide `giro.toml` read once, so every Spec was built by the same agents at the same settings. We make it layered and per-Spec: named **Profiles** (`[profiles.<name>]`) and driver definitions in the project file, a per-Spec sidecar `docs/specs/<slug>/giro.toml` (added to the protected-path set, so a worker cannot rewrite the config that picks the judge grading it), and dispatch-time flags, resolved last-wins into one effective config. That config is **frozen at Run start and recorded in the Ledger**: preflight resolves and validates it against the current environment before the loop begins, and there is deliberately **no mid-run fallback** — a driver or quota that dies mid-Run fails closed to `needs-human`, never a silent swap to another model. Reproducibility and the never-silent contract require that a Run be explainable by exactly what it recorded, which a silent substitution would destroy.

## Consequences

A per-Spec selection is a preference the environment must satisfy, not a portable guarantee: a Spec whose sidecar names a driver absent here fails preflight loudly, and the answer is to pick a runnable profile or commit that driver to the project registry — never to substitute one behind the operator's back.
