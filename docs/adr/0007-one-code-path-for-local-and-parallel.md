# Local and parallel are one code path

`concurrency` decides the shape: 1 = sequential fresh workers on the integration branch; >1 = isolated worktrees on `giro-wt/` branches, serialized `--no-ff` merges, integrated re-verify, conflict retry from updated HEAD (a frontier of one needs no wave and runs sequentially regardless). Only the engine's main thread ever touches the store or the integration branch.
