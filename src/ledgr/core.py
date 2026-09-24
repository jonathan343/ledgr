"""Fragments, release planning, rendering, and version-file updates."""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tomlkit
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
from yaml.resolver import BaseResolver

from .config import BUMPS, Config, Error

MARKER = "<!-- ledgr releases -->"
DEFAULT_TEMPLATE = """## {{ version }} — {{ date }}
{% for section in sections %}
### {{ section.heading }}
{% for change in section.changes %}
- {{ change.body | indent(2) }}
{% endfor %}{% endfor %}"""


def version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise Error(
            f"Invalid version {value!r}; expected stable SemVer X.Y.Z (prereleases are not supported yet)."
        )
    major, minor, patch = map(int, value.split("."))
    return major, minor, patch


def next_version(current: str, bump: str, pre_1_0: bool) -> str:
    major, minor, patch = version(current)
    if bump == "major" and major == 0 and pre_1_0:
        bump = "minor"
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    elif bump == "patch":
        patch += 1
    return f"{major}.{minor}.{patch}"


class VersionFile:
    def __init__(self, config: Config):
        self.path = config.version_file
        self.text = self.path.read_text(encoding="utf-8", newline="")
        self.format = self.path.suffix.lower()
        self.document = None
        self.parent = None
        self.key = None
        if self.format in (".toml", ".json"):
            if not config.version_key:
                raise Error("version-key is required for TOML and JSON version files.")
            try:
                self.document = (
                    tomlkit.parse(self.text)
                    if self.format == ".toml"
                    else json.loads(self.text)
                )
                keys = config.version_key.split(".")
                self.parent = self.document
                for key in keys[:-1]:
                    self.parent = self.parent[key]
                self.key = keys[-1]
                self.current = self.parent[self.key]
            except (KeyError, TypeError, ValueError) as exc:
                raise Error(
                    f"Cannot read version-key {config.version_key!r} in {self.path}: {exc}"
                ) from exc
        else:
            if config.version_key:
                raise Error("version-key is only supported for TOML and JSON files.")
            self.current = self.text.strip()
        version(self.current)

    def render(self, target: str) -> str:
        if self.document is None:
            return target + "\n"
        # A parsed document always has a resolved version field.
        assert self.parent is not None and self.key is not None
        self.parent[self.key] = target
        if self.format == ".toml":
            return tomlkit.dumps(self.document)
        return json.dumps(self.document, indent=2, ensure_ascii=False) + "\n"


@dataclass
class Change:
    path: Path
    type: str
    bump: str
    body: str


class MetadataLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently replacing release metadata."""


def metadata_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise Error("Fragment metadata keys must be unique strings.")
        result[key] = loader.construct_object(value_node)
    return result


MetadataLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, metadata_mapping)


def read_changes(config: Config) -> list[Change]:
    changes = []
    for path in sorted(config.changes.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---":
            raise Error(f"{path}: expected YAML front matter starting with ---.")
        try:
            end = lines.index("---", 1)
            metadata = yaml.load("\n".join(lines[1:end]), Loader=MetadataLoader)
        except (ValueError, yaml.YAMLError, Error) as exc:
            raise Error(f"{path}: invalid YAML front matter: {exc}") from exc
        if not isinstance(metadata, dict) or metadata.keys() - {"type", "bump"}:
            raise Error(f"{path}: metadata must contain type and optional bump only.")
        kind = metadata.get("type")
        if not isinstance(kind, str) or kind not in config.types:
            raise Error(f"{path}: unknown change type {kind!r}.")
        bump = metadata.get("bump", config.types[kind]["bump"])
        if bump not in BUMPS:
            raise Error(f"{path}: invalid bump {bump!r}.")
        body = "\n".join(lines[end + 1 :]).strip()
        if not body:
            raise Error(f"{path}: change body must not be empty.")
        changes.append(Change(path, kind, bump, body))
    return changes


def render_entry(config: Config, changes: list[Change], target: str) -> str:
    sections = [
        {"type": kind, "heading": settings["heading"], "changes": selected}
        for kind, settings in config.types.items()
        if (selected := [change for change in changes if change.type == kind])
    ]
    environment = Environment(
        loader=FileSystemLoader(config.template.parent) if config.template else None,
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    try:
        template = (
            environment.get_template(config.template.name)
            if config.template
            else environment.from_string(DEFAULT_TEMPLATE)
        )
        entry = template.render(
            version=target,
            date=datetime.now().astimezone().date().isoformat(),
            sections=sections,
        ).strip()
    except TemplateError as exc:
        raise Error(f"Cannot render release template: {exc}") from exc
    if not entry:
        raise Error("Release template produced an empty entry.")
    if MARKER in entry:
        raise Error("Release entries must not contain the changelog insertion marker.")
    return entry


def insert_entry(config: Config, entry: str) -> str:
    previous = (
        config.changelog.read_text(encoding="utf-8", newline="")
        if config.changelog.exists()
        else f"# Changelog\n\n{MARKER}\n"
    )
    if previous.count(MARKER) != 1:
        raise Error(f"{config.changelog} must contain exactly one {MARKER} marker.")
    before, after = previous.split(MARKER)
    return before + MARKER + "\n\n" + entry + "\n" + after


def required_bump(changes: list[Change]) -> str:
    return max((change.bump for change in changes), key=BUMPS.index, default="none")


def release_target(
    config: Config,
    current: str,
    changes: list[Change],
    explicit: str | None,
    bump: str | None,
) -> str:
    minimum = next_version(current, required_bump(changes), config.pre_1_0)
    target = explicit or next_version(
        current, bump or required_bump(changes), config.pre_1_0
    )
    if version(target) <= version(current):
        raise Error(
            "Release must increase the version. For docs/other-only releases, select --bump or --version."
        )
    if version(target) < version(minimum):
        raise Error(f"Pending changes require at least {minimum}; requested {target}.")
    return target


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_release(
    config: Config,
    source: VersionFile,
    changes: list[Change],
    version_text: str,
    changelog_text: str,
) -> None:
    # Keep originals until all writes and removals succeed. Each replacement is
    # atomic; rollback covers ordinary I/O failures, not machine/power failures.
    paths = [source.path, config.changelog, *(change.path for change in changes)]
    if len(set(paths)) != len(paths):
        raise Error("Version, changelog, and fragment paths must not overlap.")
    originals = {
        path: path.read_text(encoding="utf-8", newline="") if path.exists() else None
        for path in paths
    }
    try:
        atomic_write(source.path, version_text)
        atomic_write(config.changelog, changelog_text)
        for change in changes:
            change.path.unlink()
    except OSError:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content)
        raise
