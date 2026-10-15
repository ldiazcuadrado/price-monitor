# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-15

### Added

- Configuration-driven scraping from a validated `sites.yaml`: start URLs, pagination
  (next link or URL template), CSS field selectors with transforms (`price`, `int`, `regex`,
  `map`, `contains`, `strip`), detail-page fields and per-site request settings.
- Python plugin hook for sites that need custom parsing.
- SQLite storage with versioned schema: products, timestamped price observations, runs,
  change events and an alert log.
- Change detection (new, removed, price change, back in stock, out of stock) with percent and
  absolute thresholds.
- Alert rules (price drop, price increase, below target, stock, new, removed) with
  deduplication, delivered through Telegram, SMTP email or a JSON webhook.
- Excel report with Products, Summary, Changes, Price history and Top movers sheets, native
  line charts for the largest price movers, and CSV export.
- `watch` command with jittered interval and graceful SIGINT/SIGTERM shutdown.
- Optional Playwright renderer (`browser` extra), selected per site.
- Responsible-scraping defaults: robots.txt enforcement, per-domain rate limiting, retries with
  exponential backoff and jitter, timeouts and a configurable User-Agent.
- Typer CLI: `scrape`, `watch`, `report`, `history`, `sites validate`, `alerts test`.
- Dockerfile, Compose file, GitHub Actions CI and Dependabot configuration.

[Unreleased]: https://github.com/ldiazcuadrado/price-monitor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ldiazcuadrado/price-monitor/releases/tag/v0.1.0
