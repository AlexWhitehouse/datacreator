"""Self-update: uses throwaway git repos (a bare 'GitHub' remote and clones), never the network."""
import subprocess
from pathlib import Path

import pytest

from app import updater

GIT_ENV = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "init.defaultBranch=main"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *GIT_ENV, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str, msg: str):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)
    git(repo, "push", "-q", "origin", "main")


@pytest.fixture
def repos(tmp_path):
    """dev: where new versions are pushed from; app: the installed copy that updates itself."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    dev = tmp_path / "dev"
    git(tmp_path, "clone", "-q", str(remote), str(dev))
    commit(dev, "requirements.txt", "fastapi\n", "Initial commit")
    app = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(remote), str(app))
    return dev, app


def running(app: Path) -> dict:
    return updater._version(app)


def test_up_to_date(repos):
    _, app = repos
    st = updater._check(app, running(app))
    assert st["supported"] and not st["error"]
    assert not st["available"] and st["behind"] == 0 and not st["blocker"]


def test_new_commits_are_listed_and_applied(repos):
    dev, app = repos
    commit(dev, "a.txt", "1", "Add feature A")
    commit(dev, "b.txt", "2", "Fix bug B")
    before = running(app)
    st = updater._check(app, before)
    assert st["available"] and st["behind"] == 2
    assert [c["subject"] for c in st["commits"]] == ["Fix bug B", "Add feature A"]

    out = updater.apply(app, before)
    assert out["ok"] and not out["deps_updated"]
    assert out["version"]["sha"] == git(dev, "rev-parse", "HEAD")
    assert (app / "b.txt").exists()
    # the process still runs the old code until it restarts
    assert updater._check(app, before)["pending_restart"]


def test_uncommitted_changes_block_update(repos):
    dev, app = repos
    commit(dev, "a.txt", "1", "Upstream change")
    (app / "requirements.txt").write_text("edited locally\n")
    st = updater._check(app, running(app))
    assert st["available"] and "uncommitted changes" in st["blocker"]
    with pytest.raises(updater.UpdateError, match="uncommitted"):
        updater.apply(app, running(app))
    assert not (app / "a.txt").exists()


def test_diverged_history_blocks_update(repos):
    dev, app = repos
    commit(dev, "a.txt", "1", "Upstream change")
    (app / "local.txt").write_text("x")
    git(app, "add", "local.txt")
    git(app, "commit", "-q", "-m", "Local only")
    st = updater._check(app, running(app))
    assert st["ahead"] == 1 and st["behind"] == 1 and "local commit" in st["blocker"]


def test_requirements_change_installs_and_rolls_back_on_failure(repos, monkeypatch):
    dev, app = repos
    calls = []
    monkeypatch.setattr(updater, "_install_requirements", lambda root: calls.append(root))
    commit(dev, "requirements.txt", "fastapi\nnewpkg\n", "Add a dependency")
    out = updater.apply(app, running(app))
    assert out["deps_updated"] and calls == [app]

    commit(dev, "requirements.txt", "fastapi\nbroken\n", "Bad dependency")
    head = git(app, "rev-parse", "HEAD")

    def fail(root):
        raise updater.UpdateError("Installing requirements failed: no such package")
    monkeypatch.setattr(updater, "_install_requirements", fail)
    with pytest.raises(updater.UpdateError, match="Installing requirements failed"):
        updater.apply(app, running(app))
    assert git(app, "rev-parse", "HEAD") == head  # rolled back


def test_not_a_git_clone(tmp_path):
    st = updater._check(tmp_path, None)
    assert st["supported"] is False
