# ledgr

[![PyPI version](https://img.shields.io/pypi/v/ledgr.svg)](https://pypi.org/project/ledgr/)
[![Python versions](https://img.shields.io/pypi/pyversions/ledgr.svg)](https://pypi.org/project/ledgr/)
[![CI status](https://github.com/jonathan343/ledgr/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jonathan343/ledgr/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Record changes alongside your code, then combine them into a changelog and
semantic version bump. Ledgr supports single-package projects with stable `X.Y.Z`
versions; prereleases, build metadata, and monorepo coordination aren't supported yet.

## Install

Install from [PyPI](https://pypi.org/project/ledgr/) with
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install ledgr
```

Requires Python 3.11 or newer. Upgrade with `uv tool upgrade ledgr`.

## Quick start

In your project's root directory, initialize Ledgr using the version in
`pyproject.toml` or `package.json`:

```sh
ledgr init
```

Without an existing version file, use `ledgr init --initial-version 0.1.0` instead.
If you already have a `CHANGELOG.md`, first add `<!-- ledgr releases -->` once
above its latest release. Ledgr preserves the introduction and previous releases.

Record changes, preview the next release, then apply it:

```sh
ledgr add feature "Add CSV export"
ledgr add bugfix "Handle empty configuration files"
ledgr status                # Show pending entries and the proposed version
ledgr check                 # Validate configuration, entries, archives, and template
ledgr release --dry-run     # Preview without changing files
ledgr release
```

Commit pending entries with their code changes. After a release, review and commit
the updated version, changelog, archive, and deleted entries. Ledgr never commits,
tags, pushes, publishes, or updates package lockfiles.

## Entries and change types

Each entry is a Markdown file with YAML front matter in `.ledgr/changes/`, named
with a generated ID:

```markdown
---
type: breaking
---

Replace API-key authentication with OAuth.

Create an OAuth client in Settings and pass its access token instead.
```

`type` is required; `bump` and `prs` are optional. An omitted bump inherits the current
configuration; an explicit bump stays fixed. Everything after the closing `---`
is Markdown.

Run `ledgr add` for interactive prompts. To create and edit an entry, set `$VISUAL`
or `$EDITOR` and use `--edit` (`$VISUAL` takes precedence):

```sh
ledgr add feature --edit
```

Write the description directly in the editor, or pass one to prefill it. An empty
body cancels the entry; invalid metadata or editor failures keep the draft for recovery.

Add PR links with `--pr https://github.com/owner/repo/pull/42` (repeat for multiple
PRs), or edit the `prs` list in the entry's YAML front matter.

| Type | Changelog heading | Default bump |
| --- | --- | --- |
| `breaking` | Breaking changes | major |
| `feature` | Features | minor |
| `bugfix` | Bug fixes | patch |
| `docs` | Documentation | none |
| `other` | Other changes | none |

Choose `breaking` for incompatible changes. Customize types in
[configuration](#configuration), or override one entry's bump:

```sh
ledgr add other "Update certificates" --bump patch
```

## Releases

The largest pending bump determines the next version. Before `1.0.0`, a major bump
becomes a minor bump by default. Use `ledgr release --version 1.0.0` to move to stable,
or set `pre-1-0 = false` for literal major/minor/patch bumps.

Entries with bump `none` still appear in the changelog. If all pending entries use
`none`, choose `ledgr release --bump patch` or an explicit `--version X.Y.Z`.
`--bump` accepts `patch`, `minor`, or `major`. A release must have pending entries,
increase the version, and meet the minimum bump required by those entries.

`release` validates and renders, saves `.ledgr/releases/<version>.json`, updates
the version and changelog, then removes consumed entries. Archives preserve entry
text, IDs, types, effective bumps, headings, and the release date; existing archives
are never overwritten.

Run one release process at a time. Ordinary I/O failures trigger rollback, but
releases aren't crash-proof transactions. If rollback fails, the archive remains
for recovery.

### Render archived releases

Render archived releases to stdout, newest version first, using the current
template and original release dates:

```sh
ledgr render > CHANGELOG.preview.md
```

Unlike normal releases, rendering excludes custom introductions, manual edits,
and unarchived history, as well as pending entries. Review the output before
replacing your changelog.

## Configuration

`ledgr init` creates `ledgr.toml`. Alternatively, place the same settings under
`[tool.ledgr]` in `pyproject.toml`. Configuration is discovered from the current
directory upward; file paths are relative to the configuration file.

If both configurations exist in a directory, Ledgr reports an error. Select one
explicitly with `ledgr --config path/to/ledgr.toml status` (before the command).

```toml
# ledgr.toml
version-file = "pyproject.toml"
version-key = "project.version"
# Optional settings (defaults shown):
changes = ".ledgr/changes"
releases = ".ledgr/releases"
changelog = "CHANGELOG.md"
pre-1-0 = true
# template = ".ledgr/release.md.j2"

[types.docs]
bump = "patch"                # Override a built-in type

[types.security]
heading = "Security"
bump = "patch"                # Custom types require a bump
```

Overrides merge with the five built-in types. Built-in sections retain their
order; custom types follow in configuration order. Empty sections are omitted.

### Version files

The configured file determines the current version, not the changelog.

| File | `version-file` example | `version-key` example |
| --- | --- | --- |
| TOML | `pyproject.toml` | `project.version` |
| JSON | `package.json` | `version` |
| Plain text | `VERSION` | Omit |

For a custom location, pass `--version-file` and, for TOML/JSON, `--version-key`
to `ledgr init`. TOML/JSON version fields must already exist. `--initial-version`
creates a missing plain-text file without overwriting an existing one.

TOML comments and formatting are preserved. JSON is rewritten with two-space
indentation, preserving other field values. Plain-text files contain only the
version. Dotted keys address nested tables/objects, not literal dots in names or arrays.

## Custom templates

Set `template = ".ledgr/release.md.j2"` in your configuration to use a custom
Jinja2 template. Templates render one release and receive:

- `version`: the target version string.
- `date`: the local release date as `YYYY-MM-DD`.
- `sections`: ordered, nonempty groups with `type`, `heading`, and `changes`.
- Each change has `id` (fragment identifier), `type`, `bump` (effective), and
  `body` (Markdown), plus `prs` (a list of URLs, empty when absent).

Example `.ledgr/release.md.j2`:

```jinja2
## {{ version }} — {{ date }}
{% for section in sections %}
### {{ section.heading }}
{% for change in section.changes %}
- {{ change.body | indent(2) }}
{% endfor %}{% endfor %}
```

Template changes affect future releases unless you regenerate the changelog with
`ledgr render`; they don't affect version bumps. Unknown variables and invalid
syntax cause errors. Templates are trusted project code, not sandboxed—review
them before use.
