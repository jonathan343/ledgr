"""Fragments, release planning, rendering, and version-file updates."""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
from yaml.resolver import BaseResolver

from .config import BUMPS, Config, Error

MARKER = "<!-- ledgr releases -->"
DEFAULT_TEMPLATE = """## v{{ version }} ({{ date }})
{% for section in sections %}
### {{ section.heading }}
{% for change in section.changes %}
- {{ change.body | indent(2) }}{% if change.prs %}{{ '\\n\\n  ' if '\\n' in change.body else ' ' }}({% for pr in change.prs %}[#{{ pr.rsplit('/', 1)[-1] }}](<{{ pr }}>){% if not loop.last %}, {% endif %}{% endfor %}){% endif %}
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
        self.text = self.path.read_bytes().decode("utf-8")
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
    prs: list[str] = field(default_factory=list)


def validate_prs(value) -> list[str]:
    if not isinstance(value, list):
        raise Error("prs must be a list of pull request URLs.")
    for url in value:
        if not isinstance(url, str) or re.search(r"[\s<>\\]", url):
            raise Error(
                "Each PR must be an HTTP(S) URL ending in a positive PR number."
            )
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
            or not re.search(r"/[1-9][0-9]*$", parsed.path)
        ):
            raise Error(
                "Each PR must be an HTTP(S) URL ending in a positive PR number."
            )
    return value


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
    return [read_change(config, path) for path in sorted(config.changes.glob("*.md"))]


def read_change(config: Config, path: Path, *, allow_empty: bool = False) -> Change:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise Error(f"{path}: expected YAML front matter starting with ---.")
    try:
        end = lines.index("---", 1)
        metadata = yaml.load("\n".join(lines[1:end]), Loader=MetadataLoader)
    except (ValueError, yaml.YAMLError, Error) as exc:
        raise Error(f"{path}: invalid YAML front matter: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.keys() - {"type", "bump", "prs"}:
        raise Error(f"{path}: metadata must contain type and optional bump/prs only.")
    kind = metadata.get("type")
    if not isinstance(kind, str) or kind not in config.types:
        raise Error(f"{path}: unknown change type {kind!r}.")
    bump = metadata.get("bump", config.types[kind]["bump"])
    if bump not in BUMPS:
        raise Error(f"{path}: invalid bump {bump!r}.")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body and not allow_empty:
        raise Error(f"{path}: change body must not be empty.")
    try:
        prs = validate_prs(metadata.get("prs", []))
    except (ValueError, Error) as exc:
        raise Error(f"{path}: {exc}") from exc
    return Change(path, kind, bump, body, prs)


def release_data(config: Config, changes: list[Change], target: str) -> dict:
    sections = [
        {
            "type": kind,
            "heading": settings["heading"],
            "changes": [
                {
                    "id": change.path.stem,
                    "type": change.type,
                    "bump": change.bump,
                    "body": change.body,
                    **({"prs": change.prs} if change.prs else {}),
                }
                for change in selected
            ],
        }
        for kind, settings in config.types.items()
        if (selected := [change for change in changes if change.type == kind])
    ]
    return {
        "schema-version": 1,
        "version": target,
        "date": datetime.now().astimezone().date().isoformat(),
        "sections": sections,
    }


def read_releases(config: Config) -> list[dict]:
    if config.releases.exists() and not config.releases.is_dir():
        raise Error(f"{config.releases}: expected an archive directory.")
    releases = []
    for path in config.releases.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or type(data.get("schema-version")) is not int
                or data["schema-version"] != 1
            ):
                raise Error("expected archive schema-version 1")
            version(data.get("version"))
            if data["version"] != path.stem:
                raise Error("archive version must match its filename")
            if (
                not isinstance(data.get("date"), str)
                or date.fromisoformat(data["date"]).isoformat() != data["date"]
            ):
                raise Error("expected release date in YYYY-MM-DD format")
            sections = data.get("sections")
            if not isinstance(sections, list) or not sections:
                raise Error("expected nonempty sections")
            for section in sections:
                if not isinstance(section, dict) or any(
                    not isinstance(section.get(key), str) or not section[key].strip()
                    for key in ("type", "heading")
                ):
                    raise Error("each section needs a type and heading")
                changes = section.get("changes")
                if not isinstance(changes, list) or not changes:
                    raise Error("each section needs nonempty changes")
                for change in changes:
                    if not isinstance(change, dict) or any(
                        not isinstance(change.get(key), str) or not change[key].strip()
                        for key in ("id", "type", "body")
                    ):
                        raise Error("each change needs an id, type, and body")
                    if (
                        change["type"] != section["type"]
                        or change.get("bump") not in BUMPS
                    ):
                        raise Error("invalid change type or bump")
                    validate_prs(change.get("prs", []))
            releases.append(data)
        except (ValueError, Error) as exc:
            raise Error(f"{path}: invalid release archive: {exc}") from exc
    return sorted(
        releases, key=lambda release: version(release["version"]), reverse=True
    )


def archive_path(config: Config, target: str) -> Path:
    version(target)
    path = config.releases / f"{target}.json"
    if path.exists() or path.is_symlink():
        raise Error(f"Release archive already exists: {path}")
    for directory in path.parents:
        if directory.exists() and not directory.is_dir():
            raise Error(f"{directory}: expected an archive directory.")
    if path.resolve() in {config.version_file, config.changelog, config.template}:
        raise Error(
            "Release archive must not overlap version, changelog, or template files."
        )
    return path


def render_entry(config: Config, release: dict) -> str:
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
            version=release["version"],
            date=release["date"],
            sections=[
                {
                    **section,
                    "changes": [{"prs": [], **change} for change in section["changes"]],
                }
                for section in release["sections"]
            ],
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
        config.changelog.read_bytes().decode("utf-8")
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
    release: dict,
) -> None:
    # Keep originals until all writes and removals succeed. Each replacement is
    # atomic; rollback covers ordinary I/O failures, not machine/power failures.
    archive = archive_path(config, release["version"])
    paths = [source.path, config.changelog, *(change.path for change in changes)]
    if len(set(paths)) != len(paths):
        raise Error("Version, changelog, and fragment paths must not overlap.")
    originals = {
        path: path.read_bytes().decode("utf-8") if path.exists() else None
        for path in paths
    }
    archive_text = json.dumps(release, indent=2, ensure_ascii=False) + "\n"
    missing_dirs = [
        directory
        for directory in (archive.parent, *archive.parent.parents)
        if not directory.exists()
    ]
    archive_created = False
    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects existing archives, including a collision
        # after preflight. Pending fragments remain until this write succeeds.
        with archive.open("x", encoding="utf-8", newline="") as stream:
            archive_created = True
            stream.write(archive_text)
        atomic_write(source.path, version_text)
        atomic_write(config.changelog, changelog_text)
        for change in changes:
            change.path.unlink()
    except OSError:
        if archive_created:
            # Retain the archive for recovery if rollback itself fails.
            for path, content in originals.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content)
            archive.unlink(missing_ok=True)
        for directory in missing_dirs:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise
