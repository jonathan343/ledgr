# Changelog

<!-- ledgr releases -->

## v0.2.0 (2026-09-25)

### Features

- Write entry descriptions directly in the editor with `ledgr add --edit`. Cancel empty entries and keep drafts when validation or the editor fails. ([#11](<https://github.com/jonathan343/ledgr/pull/11>))

- Attach PR links to entries with `ledgr add --pr`, preserve references in release archives, and render them in changelogs. ([#10](<https://github.com/jonathan343/ledgr/pull/10>))

### Other changes

- Format default release headings as `v1.2.3 (YYYY-MM-DD)`. ([#12](<https://github.com/jonathan343/ledgr/pull/12>))


## v0.1.0 (2026-09-24)

### Features

- Customize change types, their version bumps, and release notes with Jinja2 templates. ([#1](<https://github.com/jonathan343/ledgr/pull/1>))

- Manage pending changelog entries with `init`, `add`, `status`, `check`, and `release` commands. Calculate semantic version bumps and update TOML, JSON, or plain-text version files. ([#1](<https://github.com/jonathan343/ledgr/pull/1>))

- Archive released entries as JSON and regenerate release history with `ledgr render`. ([#5](<https://github.com/jonathan343/ledgr/pull/5>))

### Other changes

- Support Python 3.11–3.15, with CI coverage on Linux, macOS, and Windows. ([#2](<https://github.com/jonathan343/ledgr/pull/2>), [#3](<https://github.com/jonathan343/ledgr/pull/3>))

- Prepare PyPI publishing with package metadata and an Apache-2.0 license. ([#7](<https://github.com/jonathan343/ledgr/pull/7>))
