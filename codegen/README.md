# rboto-codegen

rboto is an independent project maintained by [Ferrumio](https://github.com/ferrumio).
It is not affiliated with, sponsored by, or endorsed by Amazon Web Services (AWS).

**Preview (Alpha).** rboto is under active development. APIs may change between releases.

rboto-codegen generates typed Python clients, Rust bindings, package metadata, and
operation READMEs for rboto. This is a contributor tool; applications install
`rboto` and the service packages they use.

## Installation

Run from an rboto source checkout with Python 3.12 or newer and the repository's
Rust toolchain:

```bash
python -m venv .venv
.venv/bin/pip install -e codegen
cargo fetch --locked
.venv/bin/rboto-codegen fetch-model --all
```

## Usage

Inspect a service before generating it:

```bash
.venv/bin/rboto-codegen describe s3
.venv/bin/rboto-codegen report s3
.venv/bin/rboto-codegen generate s3
```

After a shared generator or template change, regenerate every service:

```bash
.venv/bin/rboto-codegen generate --all
```

## Commands

| Command | Purpose |
|---|---|
| `list` | List service IDs; `--json` produces a JSON array |
| `describe SERVICE` | Print the service descriptor as JSON |
| `fetch-model SERVICE` | Download the pinned model and verify its checksum |
| `report SERVICE` | Compare modeled operations with the pinned Rust crate |
| `generate SERVICE` | Generate bindings, manifests, README, and license files |
| `generate-manifests SERVICE` | Write manifests and licenses before downloading updated crates |

`fetch-model`, `report`, `generate`, and `generate-manifests` also accept `--all`
instead of a service ID.

Service names and guide paths live in `src/rboto_codegen/services/*.toml`.
Usage examples live in the repository's `examples/clients/` directory.
The package READMEs use the examples and the same in-memory Python signatures
emitted by the generator. Edit these sources and regenerate rather than editing
generated package files.

See the repository's [.ai/commands.md](https://github.com/ferrumio/rboto/blob/main/.ai/commands.md)
for the required verification commands.

[Repository](https://github.com/ferrumio/rboto) ·
[Issues](https://github.com/ferrumio/rboto/issues) ·
[Releases](https://github.com/ferrumio/rboto/releases)

Licensed under Apache-2.0.
