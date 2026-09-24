# Changelog

<!-- ledgr releases -->

## 0.1.0 — 2026-09-24

### Features

- Customize change types, their version bumps, and release notes with Jinja2 templates.

- Manage pending changelog entries with `init`, `add`, `status`, `check`, and `release` commands. Calculate semantic version bumps and update TOML, JSON, or plain-text version files.

- Archive released entries as JSON and regenerate release history with `ledgr render`.

### Other changes

- Support Python 3.11–3.15, with CI coverage on Linux, macOS, and Windows.

- Prepare PyPI publishing with package metadata and an Apache-2.0 license.
