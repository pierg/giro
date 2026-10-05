# Setup owns config; init is the CI fallback

The human path into a project is `giro install`, then the `giro-setup` host skill in chat: it inspects the repo and agent CLIs on PATH, interviews for preferences, and writes a fitting `giro.toml`. `giro init` remains only as the non-interactive fallback for headless/CI bootstraps where no conversation exists. `giro.toml` is wholly conversation-owned (the engine only reads it) — unlike Spec/Issue files, it has no engine zone; do not treat config as one-file-two-zones.
