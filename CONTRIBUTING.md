# Contributing

Bug reports and pull requests are welcome.

## Setup

```bash
uv sync
uv run pre-commit install
```

Python 3.12 is required. `uv sync` installs the project and the development tools.

## Checks

All of these must pass before a change is merged; CI runs the same commands.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

`pytest` enforces a coverage floor of 85%. Tests must not use the network: mock HTTP with
`respx` and use the HTML fixtures in `tests/fixtures`.

## Guidelines

- Keep the module boundaries: `config`, `fetch`, `parse`, `storage`, `changes`, `alerts`,
  `report`, `cli`. Parsing never fetches; fetching never parses.
- Public functions are fully typed (`mypy --strict`).
- Add a test with every behaviour change and a line to `CHANGELOG.md` under *Unreleased*.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
- Do not add site-specific code to the core. Express a site in `sites.yaml`, or write a plugin.

## Adding a site to the examples

Only add sites that explicitly permit scraping, and say so in the PR.
