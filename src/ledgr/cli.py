"""The Ledgr command-line interface."""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import tomlkit
import yaml

from .config import BUMPS, Config, Error, load, read_toml
from .core import (
    MARKER,
    VersionFile,
    apply_release,
    insert_entry,
    next_version,
    read_changes,
    release_target,
    render_entry,
    required_bump,
    version,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="ledgr", description="Fragment-based changelogs and semantic versioning."
    )
    result.add_argument(
        "--config", help="Explicit configuration file (paths are relative to it)"
    )
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize Ledgr in the current directory")
    init.add_argument("--version-file", help="TOML, JSON, or plain-text version file")
    init.add_argument("--version-key", help="Dotted key for TOML/JSON version files")
    init.add_argument(
        "--initial-version",
        help="Create a missing plain-text version file with this version",
    )
    add = commands.add_parser("add", help="Record a pending change")
    add.add_argument("type", nargs="?")
    add.add_argument("body", nargs="?")
    add.add_argument("--bump", choices=BUMPS)
    add.add_argument(
        "--edit", action="store_true", help="Open the fragment in $VISUAL or $EDITOR"
    )
    commands.add_parser("status", help="Show pending changes and the proposed version")
    commands.add_parser("check", help="Validate configuration, fragments, and template")
    release = commands.add_parser(
        "release", help="Update the version and changelog; consume fragments"
    )
    overrides = release.add_mutually_exclusive_group()
    overrides.add_argument("--version", help="Explicit target version")
    overrides.add_argument("--bump", choices=BUMPS[1:])
    release.add_argument("--dry-run", action="store_true")
    return result


def initialize(args) -> None:
    root = Path.cwd()
    if args.config:
        raise Error(
            "init writes ledgr.toml; --config is only for existing configurations."
        )
    if (root / "ledgr.toml").exists() or (
        (root / "pyproject.toml").exists()
        and "ledgr" in read_toml(root / "pyproject.toml").get("tool", {})
    ):
        raise Error("Ledgr is already configured in this directory.")
    file, key = args.version_file, args.version_key
    if not file:
        if (root / "pyproject.toml").exists() and "version" in read_toml(
            root / "pyproject.toml"
        ).get("project", {}):
            file, key = "pyproject.toml", key or "project.version"
        elif (root / "package.json").exists():
            file, key = "package.json", key or "version"
        else:
            file = "VERSION"
    path = root / file
    config = Config(root, path, key)
    if path.exists():
        if args.initial_version:
            raise Error("--initial-version cannot replace an existing version file.")
        VersionFile(config)
    else:
        if not args.initial_version or path.suffix.lower() in (".toml", ".json") or key:
            raise Error(
                "Provide an existing version file, or --initial-version to create a plain-text version file."
            )
        version(args.initial_version)
    changelog = root / "CHANGELOG.md"
    if changelog.exists() and changelog.read_text(encoding="utf-8").count(MARKER) != 1:
        raise Error(
            f"Add exactly one {MARKER} marker to the existing CHANGELOG.md before initializing."
        )
    if path.resolve() in {(root / "ledgr.toml").resolve(), changelog.resolve()}:
        raise Error("Version file must differ from the configuration and changelog.")
    changes = root / ".ledgr/changes"
    directories = {changes, *changes.parents, *path.resolve().parents}
    if path.resolve() in directories:
        raise Error("Version file conflicts with a required directory.")
    for directory in directories:
        if directory.exists() and not directory.is_dir():
            raise Error(f"{directory}: expected a directory, found a file.")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.initial_version + "\n", encoding="utf-8")
    settings = {"version-file": file}
    if key:
        settings["version-key"] = key
    (root / "ledgr.toml").write_text(tomlkit.dumps(settings), encoding="utf-8")
    changes.mkdir(parents=True, exist_ok=True)
    if not changelog.exists():
        changelog.write_text(f"# Changelog\n\n{MARKER}\n", encoding="utf-8")
    print(f"Initialized Ledgr using {file}.")


def add_change(config, args) -> None:
    kind, body = args.type, args.body
    if not kind or not body:
        if not sys.stdin.isatty():
            raise Error("Pass a type and body when stdin is not interactive.")
        kind = kind or input(f"Type ({', '.join(config.types)}): ").strip()
        body = body or input("Change description: ").strip()
    if kind not in config.types:
        raise Error(f"Unknown type {kind!r}. Choose from: {', '.join(config.types)}")
    if not body.strip():
        raise Error("Change body must not be empty.")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if args.edit and not editor:
        raise Error("Set VISUAL or EDITOR to use --edit.")
    config.changes.mkdir(parents=True, exist_ok=True)
    path = config.changes / f"{uuid4().hex[:12]}.md"
    metadata = {"type": kind}
    if args.bump is not None:
        metadata["bump"] = args.bump
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            "---\n"
            + yaml.safe_dump(metadata, sort_keys=False)
            + "---\n\n"
            + body.strip()
            + "\n"
        )
    print(f"Created {path}")
    if args.edit and editor:
        completed = subprocess.run([*shlex.split(editor), str(path)], check=False)
        if completed.returncode:
            raise Error(
                f"Editor exited with {completed.returncode}; fragment kept at {path}."
            )


def run(args) -> None:
    if args.command == "init":
        initialize(args)
        return
    config = load(Path.cwd(), args.config)
    if args.command == "add":
        add_change(config, args)
        return
    source = VersionFile(config)
    changes = read_changes(config)
    proposed = next_version(source.current, required_bump(changes), config.pre_1_0)
    if args.command == "status":
        print(f"Current version: {source.current}")
        for change in changes:
            print(
                f"  {change.type} [{change.bump}] {change.body.splitlines()[0]} ({change.path.name})"
            )
        print(f"Pending changes: {len(changes)}")
        print(
            f"Proposed version: {proposed}"
            if proposed != source.current
            else "No version bump required."
        )
    elif args.command == "check":
        entry = render_entry(config, changes, proposed)
        insert_entry(config, entry)
        print(
            f"OK: configuration, version, {len(changes)} fragment(s), and template are valid."
        )
    else:
        if not changes:
            raise Error("No pending changes to release.")
        target = release_target(
            config, source.current, changes, args.version, args.bump
        )
        entry = render_entry(config, changes, target)
        changelog_text = insert_entry(config, entry)
        version_text = source.render(target)
        if args.dry_run:
            print(
                f"Dry run: {source.current} -> {target}; consume {len(changes)} fragment(s).\n"
            )
            print(entry)
        else:
            apply_release(config, source, changes, version_text, changelog_text)
            print(
                f"Released {target}; consumed {len(changes)} fragment(s). No commit or tag created."
            )


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        run(args)
    except (Error, OSError, ValueError, EOFError) as exc:
        print(f"ledgr: {exc}", file=sys.stderr)
        return 1
    return 0
