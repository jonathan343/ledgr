"""Project configuration and configurable change types."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class Error(Exception):
    """An actionable error suitable for displaying without a traceback."""


BUMPS = ("none", "patch", "minor", "major")
DEFAULT_TYPES = {
    "breaking": {"heading": "Breaking changes", "bump": "major"},
    "feature": {"heading": "Features", "bump": "minor"},
    "bugfix": {"heading": "Bug fixes", "bump": "patch"},
    "docs": {"heading": "Documentation", "bump": "none"},
    "other": {"heading": "Other changes", "bump": "none"},
}


@dataclass
class Config:
    root: Path
    version_file: Path
    version_key: str | None = None
    changes: Path = Path(".ledgr/changes")
    changelog: Path = Path("CHANGELOG.md")
    template: Path | None = None
    pre_1_0: bool = True
    types: dict = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_TYPES.items()}
    )


def read_toml(path: Path) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Error(f"Cannot read {path}: {exc}") from exc
    if path.name == "pyproject.toml":
        for section in ("tool", "project"):
            if section in data and not isinstance(data[section], dict):
                raise Error(f"{path}: {section} must be a table.")
        tool = data.get("tool", {})
        if "ledgr" in tool and not isinstance(tool["ledgr"], dict):
            raise Error(f"{path}: tool.ledgr must be a table.")
    return data


def load(root: Path, explicit: str | None = None) -> Config:
    if explicit:
        path = Path(explicit).resolve()
        data = read_toml(path)
        if path.name == "pyproject.toml":
            data = data.get("tool", {}).get("ledgr")
    else:
        # Resolve from the nearest configured ancestor, like other project CLIs.
        for directory in (root, *root.parents):
            standalone = directory / "ledgr.toml"
            project = directory / "pyproject.toml"
            project_data = (
                read_toml(project).get("tool", {}).get("ledgr")
                if project.exists()
                else None
            )
            if standalone.exists() and project_data is not None:
                raise Error(
                    "Both ledgr.toml and [tool.ledgr] exist; select one with --config."
                )
            if standalone.exists():
                path, data = standalone, read_toml(standalone)
                break
            if project_data is not None:
                path, data = project, project_data
                break
        else:
            raise Error("No Ledgr configuration found. Run ledgr init.")
    if not isinstance(data, dict):
        raise Error("Expected a Ledgr configuration table.")
    allowed = {
        "version-file",
        "version-key",
        "changes",
        "changelog",
        "template",
        "pre-1-0",
        "types",
    }
    if unknown := data.keys() - allowed:
        raise Error(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
    for key in ("version-file", "version-key", "changes", "changelog", "template"):
        if key in data and (not isinstance(data[key], str) or not data[key]):
            raise Error(f"{key} must be a nonempty string.")
    if "version-file" not in data:
        raise Error(
            "Configure version-file to specify the source of truth for the version."
        )
    if not isinstance(data.get("pre-1-0", True), bool):
        raise Error("pre-1-0 must be a boolean.")
    base = path.parent.resolve()
    config = Config(
        root=base,
        version_file=(base / data["version-file"]).resolve(),
        version_key=data.get("version-key"),
        changes=(base / data.get("changes", ".ledgr/changes")).resolve(),
        changelog=(base / data.get("changelog", "CHANGELOG.md")).resolve(),
        template=(base / data["template"]).resolve() if "template" in data else None,
        pre_1_0=data.get("pre-1-0", True),
    )
    overrides = data.get("types", {})
    if not isinstance(overrides, dict):
        raise Error("types must be a table.")
    for name, settings in overrides.items():
        if (
            not name
            or not isinstance(settings, dict)
            or settings.keys() - {"heading", "bump"}
        ):
            raise Error(f"Invalid configuration for change type {name!r}.")
        merged = config.types.get(name, {"heading": name.capitalize()}) | settings
        if merged.get("bump") not in BUMPS:
            raise Error(f"Type {name!r} needs a bump: none, patch, minor, or major.")
        if not isinstance(merged["heading"], str) or not merged["heading"].strip():
            raise Error(f"Type {name!r} needs a nonempty heading.")
        config.types[name] = merged
    if config.version_file == config.changelog:
        raise Error("version-file and changelog must be different files.")
    return config
