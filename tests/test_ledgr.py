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
