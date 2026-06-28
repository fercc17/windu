"""
Who last edited an environment's definition file in is-infrastructure
(canonical/infrastructure-services), used as an ownership fallback when an
environment has no CIA data.

Reads the git history of the file at ``Environment.git_path`` from a local clone
(``INFRA_REPO_PATH``, default ``<repo>/infrastructure-services``), filtering out
automation/bot authors so only real people remain. The infra repo squash-merges
a lot via ``canonical-is-platform-services[bot]``; files only ever touched by
automation have no human to name (we say so and link to the file's GitHub
history). Results are cached per path.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_BOT_RE = re.compile(r"\[bot\]|github-actions|renovate|canonical-is-bot|platform-services", re.I)
_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.I)
_PR_RE = re.compile(r"\(#(\d+)\)")
CACHE_TTL = 6 * 3600


def _repo_path() -> str | None:
    path = getattr(settings, "INFRA_REPO_PATH", "") or os.environ.get("INFRA_REPO_PATH", "")
    return path if path and os.path.isdir(os.path.join(path, ".git")) else None


def _is_bot(name: str, email: str) -> bool:
    return bool(_BOT_RE.search(name or "") or _BOT_RE.search(email or ""))


def _login_from_email(email: str) -> str:
    """GitHub login encoded in a noreply email (``1234+login@users.noreply.github.com``)."""
    m = _NOREPLY_RE.match(email or "")
    return m.group(1) if m else ""


def github_history_url(git_path: str) -> str:
    repo = getattr(settings, "INFRA_GITHUB_REPO", "canonical/infrastructure-services")
    return f"https://github.com/{repo}/commits/main/{git_path}" if git_path else ""


def file_editors(git_path: str, limit: int = 8) -> dict:
    """Return ``{editors, source, history_url, human_commits}`` for ``git_path``.

    ``editors`` is most-recent-first, deduped, bots excluded. ``source`` is one of
    ``git`` (humans found), ``bot-only`` (only automation), ``unconfigured`` (no
    clone), ``error``, or ``none`` (no path).
    """
    if not git_path:
        return {"editors": [], "source": "none", "history_url": ""}

    ckey = f"infra_editors:{git_path}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached

    history_url = github_history_url(git_path)
    repo = _repo_path()
    if not repo:
        result = {"editors": [], "source": "unconfigured", "history_url": history_url}
        cache.set(ckey, result, CACHE_TTL)
        return result

    try:
        proc = subprocess.run(
            ["git", "-C", repo, "log", "--no-merges", "--format=%an\t%ae\t%ad\t%s",
             "--date=short", "--", git_path],
            capture_output=True, text=True, timeout=15,
        )
        out = proc.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("infra history for %s failed: %s", git_path, exc)
        result = {"editors": [], "source": "error", "history_url": history_url}
        cache.set(ckey, result, 300)
        return result

    editors: list[dict] = []
    seen: set[str] = set()
    human_commits = 0
    prs: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, email, date = parts[0], parts[1], parts[2]
        subject = parts[3] if len(parts) > 3 else ""
        m = _PR_RE.search(subject)
        if m and m.group(1) not in prs:
            prs.append(m.group(1))
        if _is_bot(name, email):
            continue
        human_commits += 1
        key = (email or name).lower()
        if key in seen:
            continue
        seen.add(key)
        editors.append({
            "name": name, "email": email,
            "login": _login_from_email(email), "last": date,
        })

    result = {
        "editors": editors[:limit],
        "source": "git" if editors else "bot-only",
        "human_commits": human_commits,
        "prs": prs[:5],
        "history_url": history_url,
    }
    cache.set(ckey, result, CACHE_TTL)
    return result
