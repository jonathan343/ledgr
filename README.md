# ledgr

Fragment-based changelogs and semantic versioning. Record changes alongside your
code, then combine them into a release. Requires Python 3.11 or newer.

## Quick start

From this checkout:

```sh
uv sync
uv run ledgr --help
uv run pytest
```

Install the checkout as a CLI with `uv tool install .`, then run these commands in
the project whose changelog you want to manage:

```sh
ledgr init                  # Detect pyproject.toml or package.json version
# Or, without an existing manifest/version file:
# ledgr init --initial-version 0.1.0

ledgr add feature "Add CSV export"
ledgr add bugfix "Handle empty configuration files"
ledgr add breaking "Replace API-key authentication with OAuth" --edit
ledgr status
ledgr check
ledgr release --dry-run
ledgr release
```

`add` prompts for the type and description when run interactively without them.
`--edit` uses `$VISUAL`, falling back to `$EDITOR`. Fragments have generated IDs
and live in `.ledgr/changes/`. Commit them with the corresponding code changes:

```markdown
---
type: breaking
---

Replace API-key authentication with OAuth.

Create an OAuth client in Settings and pass its access token instead.
```

The YAML metadata accepts a required `type` and optional `bump`. Everything after
the closing `---` is Markdown. An omitted bump inherits the current configuration;
an explicit bump remains fixed if the configuration changes.

## Change types and releases

| Type | Changelog heading | Default bump |
| --- | --- | --- |
| `breaking` | Breaking changes | major |
| `feature` | Features | minor |
| `bugfix` | Bug fixes | patch |
| `docs` | Documentation | none |
| `other` | Other changes | none |

Each entry has one type. Choose `breaking` for incompatible changes; there is no
separate breaking flag. The largest effective bump wins. While the current version
is `0.x`, a major bump becomes a minor bump by default. Use
`ledgr release --version 1.0.0` to explicitly promote the project to stable.

Override a single entry with `ledgr add other "Update certificates" --bump patch`.
Entries with bump `none` are still included in the changelog. A release containing
only such entries needs an explicit `--bump patch|minor|major` or `--version X.Y.Z`.
Explicit release targets cannot go below the version required by pending changes.
An empty set of pending changes cannot be released.

`release` validates and renders before writing, archives the release data, updates
the version and changelog, then removes consumed fragments. `--dry-run` does none
of those writes. Ordinary I/O failures trigger rollback; this is not a crash-proof
multi-file transaction.
Run one release process at a time and review the resulting diff before committing.
Ledgr never commits, tags, pushes, publishes, or updates package lockfiles.

This initial version supports single-package projects and stable `X.Y.Z` versions.
Prerelease/build metadata and monorepo coordination are not supported yet.

## Release archives

Each release saves pending entries to `.ledgr/releases/<version>.json` before
removing their fragments. Archives preserve entry text, IDs, types, effective
bumps, section headings, and the release date. Commit them with the release;
existing archives are never overwritten. If rollback fails, the archive remains
for recovery.

Render archived releases to stdout, newest version first, using the current
template and original release dates:

```sh
ledgr render > CHANGELOG.preview.md
```

Rendering excludes pending entries, custom introductions, manual edits, and
unarchived history. Review the output before replacing your changelog. Normal
releases preserve existing changelog content. `ledgr check` validates archives.

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
Set `pre-1-0 = false` for literal major/minor/patch mapping at every version.

### Version files

The configured file is the version source of truth; Ledgr never extracts versions
from changelog headings.

| File | `version-key` | Example initialization |
| --- | --- | --- |
| TOML | Dotted table/key path | `ledgr init --version-file pyproject.toml --version-key project.version` |
| JSON | Dotted object/key path | `ledgr init --version-file package.json --version-key version` |
| Plain text | Omit | `ledgr init --version-file VERSION --initial-version 0.1.0` |

TOML comments and formatting are preserved. JSON is rewritten with two-space
indentation, preserving other field values. Plain-text files contain only the
version. Initial versions never overwrite existing files; TOML/JSON version fields
must already exist. Dotted keys do not support literal dots in field names or arrays.

## Custom templates

Templates use Jinja2 and render a single release entry. They receive:

- `version`: the target version string.
- `date`: the local release date as `YYYY-MM-DD`.
- `sections`: ordered, nonempty groups with `type`, `heading`, and `changes`.
- Each change has `id` (fragment identifier), `type`, `bump` (effective), and
  `body` (Markdown).

Example `.ledgr/release.md.j2`:

```jinja2
## {{ version }} — {{ date }}
{% for section in sections %}
### {{ section.heading }}
{% for change in section.changes %}
- {{ change.body | indent(2) }}
{% endfor %}{% endfor %}
```

Unknown variables and invalid template syntax cause an error. Templates are
trusted project code, not a sandbox; review custom templates before using them.
Rendering never determines the version bump.

Ledgr inserts new entries after `<!-- ledgr releases -->`, preserving the
introduction and earlier releases. For an existing changelog, add that marker once
above the latest release before initializing. Template changes affect future
entries only, unless you explicitly regenerate archived releases with `ledgr render`.
