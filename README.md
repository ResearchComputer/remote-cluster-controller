# rcc

Remote cluster controller. Generalizes the classic `ssh host "cd dir && cmd"` +
`rsync project/ host:dir/` workflow into a small CLI with per-project config.

## Install

```bash
uv tool install rcc
# or
pipx install rcc
```

## Quickstart

```bash
cd my-project
rcc init
# edit .rcc/config.toml to set host and remote_dir
rcc push --dry-run
rcc push
rcc run -- nvidia-smi
rcc run -t -- htop
rcc shell
rcc pull
rcc status
rcc close
```

## Configuration

`.rcc/config.toml` (per project, gitignored):

```toml
default = "myhost"

[profiles.myhost]
host = "myhost"
remote_dir = "/abs/path/on/remote"
```

Per-command overrides: `--profile`, `--host`, `--remote-dir`.
Excludes: edit `.rcc/rccignore` (gitignore syntax).

See [`docs/superpowers/specs/2026-04-23-rcc-design.md`](docs/superpowers/specs/2026-04-23-rcc-design.md) for the full spec.
