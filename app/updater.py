"""Self-update from the git remote.

check()  fetches the upstream branch and reports new commits, plus anything that makes a safe
         fast-forward impossible (uncommitted changes, local commits that aren't upstream).
apply()  fast-forwards, reinstalls requirements if requirements.txt changed (rolling back if that
         fails), and returns the new version. restart_soon() then re-executes this process in place,
         which works both under systemd and when started from a terminal with ./run.sh.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

CHECK_TTL = 600  # seconds a check result is reused unless forced
MAX_COMMITS = 30

_cache: dict = {"at": 0.0, "data": None}
_check_lock = threading.Lock()
_apply_lock = threading.Lock()


class UpdateError(Exception):
    pass


def _git(root: Path, *args: str, timeout: int = 30) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise UpdateError("git is not installed")
    except subprocess.TimeoutExpired:
        raise UpdateError(f"git {args[0]} timed out. Is GitHub reachable?")
    if r.returncode:
        lines = (r.stderr or r.stdout).strip().splitlines()
        raise UpdateError(lines[-1] if lines else f"git {args[0]} failed")
    return r.stdout.strip()


def _version(root: Path) -> dict | None:
    try:
        sha = _git(root, "rev-parse", "HEAD")
        date = _git(root, "log", "-1", "--format=%cI")
    except UpdateError:
        return None
    return {"sha": sha, "short": sha[:7], "date": date}


# The code this process is actually running (HEAD can move on disk before a restart).
VERSION = _version(ROOT) if (ROOT / ".git").exists() else None


def _req_hash(root: Path) -> str:
    p = root / "requirements.txt"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def _check(root: Path, running: dict | None) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not (root / ".git").exists():
        return {"supported": False, "reason": "This copy isn't a git clone, so it can't update itself.", "checked_at": now}
    out = {"supported": True, "current": running or _version(root), "checked_at": now,
           "available": False, "behind": 0, "ahead": 0, "commits": [], "blocker": None, "error": None,
           "pending_restart": False}
    try:
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            raise UpdateError("This copy isn't on a branch (detached HEAD), so it can't update itself.")
        try:
            upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        except UpdateError:
            raise UpdateError(f"Branch {branch} doesn't track a GitHub branch.")
        remote, _, remote_branch = upstream.partition("/")
        _git(root, "fetch", "--quiet", remote, remote_branch, timeout=45)
        behind = int(_git(root, "rev-list", "--count", f"HEAD..{upstream}"))
        ahead = int(_git(root, "rev-list", "--count", f"{upstream}..HEAD"))
        dirty = [line[3:] for line in _git(root, "status", "--porcelain", "--untracked-files=no").splitlines() if line.strip()]
        commits = []
        if behind:
            log = _git(root, "log", f"--max-count={MAX_COMMITS}", "--format=%h%x1f%s%x1f%an%x1f%cI", f"HEAD..{upstream}")
            for line in log.splitlines():
                sha, subject, author, date = (line.split("\x1f") + ["", "", "", ""])[:4]
                commits.append({"sha": sha, "subject": subject, "author": author, "date": date})
        blocker = None
        if behind and ahead:
            blocker = (f"This copy has {ahead} local commit{'s' if ahead > 1 else ''} that aren't on GitHub, "
                       "so it can't be fast-forwarded. Update it from a terminal.")
        elif behind and dirty:
            shown = ", ".join(dirty[:3]) + (f" and {len(dirty) - 3} more" if len(dirty) > 3 else "")
            blocker = (f"This copy has uncommitted changes to {len(dirty)} file{'s' if len(dirty) > 1 else ''} "
                       f"({shown}). Commit or discard them first.")
        head = _git(root, "rev-parse", "HEAD")
        out.update(branch=branch, upstream=upstream, behind=behind, ahead=ahead, available=behind > 0,
                   commits=commits, blocker=blocker, latest=_git(root, "rev-parse", "--short", upstream),
                   pending_restart=bool(running and running["sha"] != head))
    except UpdateError as e:
        out["error"] = str(e)
    return out


def check(force: bool = False, root: Path = ROOT, running: dict | None = VERSION) -> dict:
    with _check_lock:
        if not force and _cache["data"] is not None and time.time() - _cache["at"] < CHECK_TTL:
            return _cache["data"]
        data = _check(root, running)
        _cache.update(at=time.time(), data=data)
        return data


def _install_requirements(root: Path) -> None:
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements.txt"],
                       cwd=root, capture_output=True, text=True, timeout=900)
    if r.returncode:
        lines = (r.stderr or r.stdout).strip().splitlines()
        raise UpdateError(f"Installing requirements failed: {lines[-1] if lines else 'pip error'}")


def apply(root: Path = ROOT, running: dict | None = VERSION) -> dict:
    """Fast-forward to upstream and install changed requirements. Doesn't restart."""
    if not _apply_lock.acquire(blocking=False):
        raise UpdateError("An update is already in progress")
    try:
        st = _check(root, running)
        if not st["supported"]:
            raise UpdateError(st["reason"])
        if st["error"]:
            raise UpdateError(st["error"])
        if st["blocker"]:
            raise UpdateError(st["blocker"])
        if not st["available"] and not st["pending_restart"]:
            raise UpdateError("Already up to date")
        deps = False
        if st["available"]:
            before = _git(root, "rev-parse", "HEAD")
            old_req = _req_hash(root)
            _git(root, "merge", "--ff-only", "--quiet", st["upstream"], timeout=60)
            if _req_hash(root) != old_req:
                try:
                    _install_requirements(root)
                except UpdateError:
                    _git(root, "reset", "--keep", before)  # back to the version whose packages are installed
                    raise
                deps = True
        _cache.update(at=0.0, data=None)
        return {"ok": True, "version": _version(root), "deps_updated": deps}
    finally:
        _apply_lock.release()


def restart_soon(delay: float = 0.8) -> None:
    """Replace this process with a fresh copy of itself, after the HTTP response has gone out."""
    def go():
        time.sleep(delay)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=go, daemon=True).start()
