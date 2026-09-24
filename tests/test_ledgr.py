import json
import subprocess
import sys
from pathlib import Path

import pytest

from ledgr.cli import main
from ledgr.config import Error
from ledgr.core import MARKER, next_version, version


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--initial-version", "0.3.2"]) == 0
    return tmp_path


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("current", "bump", "policy", "expected"),
    [
        ("0.3.2", "major", True, "0.4.0"),
        ("0.3.2", "major", False, "1.0.0"),
        ("0.3.2", "minor", True, "0.4.0"),
        ("0.3.2", "patch", True, "0.3.3"),
        ("1.3.2", "major", True, "2.0.0"),
        ("1.3.2", "minor", True, "1.4.0"),
        ("1.3.2", "none", True, "1.3.2"),
    ],
)
def test_version_bumps(current, bump, policy, expected):
    assert next_version(current, bump, policy) == expected


@pytest.mark.parametrize(
    "value",
    [
        "01.2.3",
        "1.2",
        "v1.2.3",
        "1.2.3rc1",
        "1.2.3-beta.1",
        "1.2.3+build",
        "١.2.3",
        123,
    ],
)
def test_invalid_or_unsupported_versions(value):
    with pytest.raises(Error):
        version(value)


def test_complete_release_and_read_only_commands(project, capsys):
    previous = (
        f"# History\n\nKeep this introduction.\n\n{MARKER}\n\n## 0.3.2\n\nOld text.\n"
    )
    (project / "CHANGELOG.md").write_text(previous)
    assert main(["add", "feature", "Export CSV"]) == 0
    assert (
        main(
            [
                "add",
                "breaking",
                "Replace authentication\n\nUse OAuth instead.\n\n```sh\nlogin --oauth\n``` ",
            ]
        )
        == 0
    )
    assert main(["add", "docs", "Explain configuration"]) == 0
    before = snapshot(project)
    assert main(["status"]) == 0
    assert "Proposed version: 0.4.0" in capsys.readouterr().out
    assert main(["check"]) == 0
    assert main(["release", "--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "0.3.2 -> 0.4.0" in preview
    assert "  Use OAuth instead." in preview
    assert snapshot(project) == before
    assert main(["release"]) == 0
    assert (project / "VERSION").read_text() == "0.4.0\n"
    result = (project / "CHANGELOG.md").read_text()
    assert result.startswith(previous.split(MARKER)[0] + MARKER)
    assert result.endswith(previous.split(MARKER)[1])
    assert (
        result.index("### Breaking changes")
        < result.index("### Features")
        < result.index("### Documentation")
    )
    assert "### Bug fixes" not in result
    assert "- Explain configuration" in result
    assert not list((project / ".ledgr/changes").glob("*.md"))
    assert main(["release"]) == 1


def test_none_only_requires_explicit_bump(project, capsys):
    assert main(["add", "docs", "Explain installation"]) == 0
    before = snapshot(project)
    assert main(["status"]) == 0
    assert "No version bump required" in capsys.readouterr().out
    assert main(["release"]) == 1
    assert snapshot(project) == before
    assert main(["release", "--bump", "patch"]) == 0
    assert (project / "VERSION").read_text().strip() == "0.3.3"


@pytest.mark.parametrize(
    "arguments", [["--version", "0.3.3"], ["--bump", "patch"], ["--version", "0.2.9"]]
)
def test_cannot_understate_required_bump(project, arguments):
    assert main(["add", "breaking", "Drop old protocol"]) == 0
    before = snapshot(project)
    assert main(["release", *arguments]) == 1
    assert snapshot(project) == before


def test_explicit_stable_release(project):
    assert main(["add", "feature", "Stable API"]) == 0
    assert main(["release", "--version", "1.0.0"]) == 0
    assert (project / "VERSION").read_text().strip() == "1.0.0"
    assert main(["add", "breaking", "Replace stable API"]) == 0
    assert main(["release"]) == 0
    assert (project / "VERSION").read_text().strip() == "2.0.0"


def test_type_defaults_overrides_and_custom_template(project):
    assert main(["add", "docs", "Inherited bump"]) == 0
    assert main(["add", "docs", "Explicit bump", "--bump", "none"]) == 0
    (project / "ledgr.toml").write_text("""version-file = "VERSION"
template = "release.j2"
[types.docs]
bump = "minor"
[types.security]
heading = "Security updates"
bump = "patch"
""")
    (project / "release.j2").write_text(
        "{{ version }}\n{% for section in sections %}{{ section.heading }}\n{% for change in section.changes %}{{ change.body }} [{{ change.bump }}]\n{% endfor %}{% endfor %}"
    )
    assert main(["add", "security", "Fix credential exposure"]) == 0
    assert main(["release"]) == 0
    text = (project / "CHANGELOG.md").read_text()
    assert "0.4.0" in text
    assert "Inherited bump [minor]" in text
    assert "Explicit bump [none]" in text
    assert "Security updates\nFix credential exposure [patch]" in text


@pytest.mark.parametrize(
    "metadata",
    [
        "type: unknown",
        "type: [bugfix]",
        "type: bugfix\nbump: banana",
        "type: bugfix\nbump: null",
        "type: bugfix\nextra: value",
        "type: breaking\ntype: bugfix",
        "!!python/object/apply:os.system ['echo unsafe']",
        "type: [",
        "{}",
    ],
)
def test_invalid_metadata_prevents_writes(project, metadata):
    (project / ".ledgr/changes/bad.md").write_text(
        f"---\n{metadata}\n---\n\nDescription\n"
    )
    before = snapshot(project)
    assert main(["check"]) == 1
    assert main(["release"]) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("template", ["{{ nonexistent }}", "{% broken %}", ""])
def test_template_failure_prevents_writes(project, template):
    with (project / "ledgr.toml").open("a") as stream:
        stream.write('template = "release.j2"\n')
    (project / "release.j2").write_text(template)
    assert main(["add", "bugfix", "Fix parsing"]) == 0
    before = snapshot(project)
    assert main(["release"]) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize(
    ("filename", "contents", "key"),
    [
        (
            "pyproject.toml",
            '# Keep me\n[project]\nname = "demo"\nversion = "1.4.7" # release\n',
            "project.version",
        ),
        (
            "package.json",
            '{"name":"demo","release":{"version":"1.4.7"},"version":"9.9.9"}',
            "release.version",
        ),
    ],
)
def test_version_file_updates(tmp_path, monkeypatch, filename, contents, key):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / filename
    path.write_text(contents)
    assert main(["init", "--version-file", filename, "--version-key", key]) == 0
    assert main(["add", "bugfix", "Fix parsing"]) == 0
    assert main(["release"]) == 0
    if filename.endswith("toml"):
        assert path.read_text() == contents.replace("1.4.7", "1.4.8")
    else:
        assert json.loads(path.read_text()) == {
            "name": "demo",
            "release": {"version": "1.4.8"},
            "version": "9.9.9",
        }


def test_config_ambiguity_and_explicit_selection(project):
    (project / "pyproject.toml").write_text(
        '[tool.ledgr]\nversion-file = "VERSION"\npre-1-0 = false\n'
    )
    assert main(["status"]) == 1
    assert main(["--config", "pyproject.toml", "add", "breaking", "Replace API"]) == 0
    assert main(["--config", "pyproject.toml", "release"]) == 0
    assert (project / "VERSION").read_text().strip() == "1.0.0"


def test_ancestor_config_paths(project, monkeypatch):
    child = project / "src"
    child.mkdir()
    monkeypatch.chdir(child)
    assert main(["add", "feature", "Nested command"]) == 0
    assert len(list((project / ".ledgr/changes").glob("*.md"))) == 1
    assert not (child / ".ledgr").exists()


def test_init_never_overwrites_existing_work(project):
    before = snapshot(project)
    assert main(["init", "--initial-version", "2.0.0"]) == 1
    assert snapshot(project) == before


def test_io_failure_restores_release(project, monkeypatch):
    from ledgr import core

    assert main(["add", "bugfix", "Fix parsing"]) == 0
    before = snapshot(project)
    original = core.atomic_write
    calls = 0

    def fail_once(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated changelog write failure")
        original(path, content)

    monkeypatch.setattr(core, "atomic_write", fail_once)
    assert main(["release"]) == 1
    assert snapshot(project) == before


def test_installed_entry_point(project):
    executable = "ledgr.exe" if sys.platform == "win32" else "ledgr"
    result = subprocess.run(
        [str(Path(sys.executable).with_name(executable)), "check"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout


def test_preserves_existing_changelog_bytes(project):
    old = f"# History\r\n\r\n{MARKER}\r\n\r\n## 0.3.2\r\n\r\nPrevious release.\r\n".encode()
    (project / "CHANGELOG.md").write_bytes(old)
    assert main(["add", "bugfix", "Fix parsing"]) == 0
    assert main(["release"]) == 0
    updated = (project / "CHANGELOG.md").read_bytes()
    before, after = old.split(MARKER.encode())
    assert updated.startswith(before + MARKER.encode())
    assert updated.endswith(after)


def test_marker_in_fragment_cannot_corrupt_changelog(project):
    assert main(["add", "docs", f"Insert {MARKER} in your file"]) == 0
    before = snapshot(project)
    assert main(["release", "--bump", "patch"]) == 1
    assert snapshot(project) == before


def test_interactive_add_and_editor(project, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter(["bugfix", "Fix parsing"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    monkeypatch.setenv("EDITOR", "test-editor --wait")

    def edit(command, check):
        assert command[:2] == ["test-editor", "--wait"]
        path = Path(command[-1])
        path.write_text(path.read_text() + "\nAdditional context.\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", edit)
    assert main(["add", "--edit"]) == 0
    assert main(["release"]) == 0
    assert "  Additional context." in (project / "CHANGELOG.md").read_text()


def test_removal_failure_restores_consumed_fragments(project, monkeypatch):
    assert main(["add", "bugfix", "First correction"]) == 0
    assert main(["add", "bugfix", "Second correction"]) == 0
    before = snapshot(project)
    unlink = Path.unlink
    count = 0

    def fail_second_fragment(path, *args, **kwargs):
        nonlocal count
        if path.suffix == ".md":
            count += 1
            if count == 2:
                raise OSError("Simulated fragment removal failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_fragment)
    assert main(["release"]) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("obstruction", [".ledgr", ".ledgr/changes", "versions"])
def test_init_preflights_directories(tmp_path, monkeypatch, obstruction):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / obstruction
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Existing work")
    before = snapshot(tmp_path)
    assert (
        main(
            ["init", "--version-file", "versions/VERSION", "--initial-version", "0.1.0"]
        )
        == 1
    )
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("filename", [".ledgr", ".ledgr/changes"])
def test_init_rejects_version_directory_collision(tmp_path, monkeypatch, filename):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--version-file", filename, "--initial-version", "0.1.0"]) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "contents", ['tool = "invalid"', "project = 42", "[tool]\nledgr = []"]
)
@pytest.mark.parametrize(
    "arguments", [["check"], ["--config", "pyproject.toml", "check"], ["init"]]
)
def test_malformed_project_tables_are_reported(
    tmp_path, monkeypatch, capsys, contents, arguments
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(contents)
    before = snapshot(tmp_path)
    assert main(arguments) == 1
    error = capsys.readouterr().err
    assert "must be a table" in error
    assert "Traceback" not in error
    assert snapshot(tmp_path) == before


def test_archive_snapshots_release_and_renders_without_pending_inputs(
    project, monkeypatch, capsys
):
    from datetime import datetime

    from ledgr import core

    class ReleaseClock:
        @staticmethod
        def now():
            return datetime(2022, 4, 7, 12).astimezone()

    monkeypatch.setattr(core, "datetime", ReleaseClock)
    (project / "ledgr.toml").write_text("""version-file = "VERSION"
releases = "history/releases"
[types.security]
heading = "Security updates"
bump = "minor"
""")
    assert (
        main(
            [
                "add",
                "security",
                "Fix café access\n\nRotate your token.",
                "--bump",
                "patch",
            ]
        )
        == 0
    )
    fragment = next((project / ".ledgr/changes").glob("*.md"))
    assert main(["add", "docs", "Document token rotation"]) == 0
    docs = next(
        path for path in (project / ".ledgr/changes").glob("*.md") if path != fragment
    )
    assert main(["release"]) == 0
    archive = project / "history/releases/0.3.3.json"
    data = json.loads(archive.read_text(encoding="utf-8"))
    assert data == {
        "schema-version": 1,
        "version": "0.3.3",
        "date": "2022-04-07",
        "sections": [
            {
                "type": "docs",
                "heading": "Documentation",
                "changes": [
                    {
                        "id": docs.stem,
                        "type": "docs",
                        "bump": "none",
                        "body": "Document token rotation",
                    },
                ],
            },
            {
                "type": "security",
                "heading": "Security updates",
                "changes": [
                    {
                        "id": fragment.stem,
                        "type": "security",
                        "bump": "patch",
                        "body": "Fix café access\n\nRotate your token.",
                    },
                ],
            },
        ],
    }
    assert not fragment.exists() and not docs.exists()
    # Rendering depends on archived data, not the version file, pending changes,
    # today's date, or current type definitions.
    monkeypatch.setattr(core, "datetime", datetime)
    (project / "VERSION").unlink()
    (project / ".ledgr/changes/invalid.md").write_text("Not valid front matter")
    (project / "ledgr.toml").write_text("""version-file = "VERSION"
releases = "history/releases"
template = "release.j2"
[types.docs]
heading = "Renamed docs"
bump = "major"
""")
    (project / "release.j2").write_text(
        "{{ version }} / {{ date }}\n{% for section in sections %}{{ section.heading }}\n{% for change in section.changes %}{{ change.body }} [{{ change.bump }}]\n{% endfor %}{% endfor %}"
    )
    before = snapshot(project)
    capsys.readouterr()
    assert main(["render"]) == 0
    rendered = capsys.readouterr().out
    assert "0.3.3 / 2022-04-07" in rendered
    assert "Security updates\nFix café access\n\nRotate your token. [patch]" in rendered
    assert "Documentation\nDocument token rotation [none]" in rendered
    assert "Renamed docs" not in rendered
    assert snapshot(project) == before


def test_render_orders_releases_semantically_and_excludes_pending(project, capsys):
    assert main(["add", "bugfix", "Older correction"]) == 0
    assert main(["release", "--version", "0.9.0"]) == 0
    first = (project / ".ledgr/releases/0.9.0.json").read_bytes()
    assert main(["add", "feature", "New functionality"]) == 0
    assert main(["release"]) == 0
    assert (project / ".ledgr/releases/0.9.0.json").read_bytes() == first
    assert main(["add", "feature", "Not released"]) == 0
    capsys.readouterr()
    before = snapshot(project)
    assert main(["render"]) == 0
    rendered = capsys.readouterr().out
    assert rendered.index("## 0.10.0") < rendered.index("## 0.9.0")
    assert "Older correction" in rendered and "New functionality" in rendered
    assert "Not released" not in rendered
    assert snapshot(project) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_release_refuses_existing_archive(project, dry_run):
    directory = project / ".ledgr/releases"
    directory.mkdir()
    (directory / "0.3.3.json").write_text("Existing archive, even if malformed")
    assert main(["add", "bugfix", "New correction"]) == 0
    before = snapshot(project)
    assert main(["release", *(["--dry-run"] if dry_run else [])]) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("partial_write", [False, True])
def test_archive_creation_failure_does_not_change_release_files(
    project, monkeypatch, partial_write
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    assert main(["add", "bugfix", "Fix parsing"]) == 0
    before = snapshot(project)
    original = Path.open

    @contextmanager
    def broken_stream(path, *args, **kwargs):
        with original(path, *args, **kwargs) as stream:

            def write(content):
                stream.write(content[:20])
                raise OSError("Archive write failed")

            yield SimpleNamespace(write=write)

    def fail_archive(path, *args, **kwargs):
        if path.suffix == ".json" and args and args[0] == "x":
            if partial_write:
                return broken_stream(path, *args, **kwargs)
            raise OSError("Archive creation failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_archive)
    assert main(["release"]) == 1
    assert snapshot(project) == before
    assert not (project / ".ledgr/releases").exists()


def test_failed_rollback_retains_archive_for_recovery(project, monkeypatch):
    from ledgr import core

    assert main(["add", "bugfix", "Recoverable change"]) == 0

    def fail_write(path, content):
        raise OSError("Cannot write or restore release files")

    monkeypatch.setattr(core, "atomic_write", fail_write)
    assert main(["release"]) == 1
    archive = json.loads((project / ".ledgr/releases/0.3.3.json").read_text())
    assert archive["sections"][0]["changes"][0]["body"] == "Recoverable change"
    assert list((project / ".ledgr/changes").glob("*.md"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema-version", 99),
        ("schema-version", True),
        ("version", "0.4.0"),
        ("date", "2022-02-30"),
        ("date", None),
        ("sections", []),
        (
            "sections",
            [
                {
                    "type": "bugfix",
                    "heading": "Fixes",
                    "changes": [
                        {"id": "x", "type": "bugfix", "bump": "invalid", "body": "Fix"}
                    ],
                }
            ],
        ),
    ],
)
def test_invalid_archives_fail_check_and_render_without_partial_output(
    project, capsys, field, value
):
    assert main(["add", "bugfix", "First correction"]) == 0
    assert main(["release"]) == 0
    assert main(["add", "bugfix", "Second correction"]) == 0
    assert main(["release"]) == 0
    archive = project / ".ledgr/releases/0.3.3.json"
    data = json.loads(archive.read_text())
    data[field] = value
    archive.write_text(json.dumps(data))
    before = snapshot(project)
    capsys.readouterr()
    assert main(["render"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "invalid release archive" in output.err
    assert main(["check"]) == 1
    assert snapshot(project) == before


def test_render_without_archives_is_an_error(project, capsys):
    capsys.readouterr()
    assert main(["render"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "No archived releases" in output.err
