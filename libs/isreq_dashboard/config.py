"""Configuration & secrets loading (FR-029/030, Art. XI; research R-011).

Three sources, validated at startup:
  - **secrets + connection** from the environment only (never a file),
  - **non-secret tuning** from a TOML file,
  - the **user->region map** from a CSV path.

A missing/malformed required key is a hard error (never a silent default that
flatters the data). Secrets are masked in every repr/str so they cannot leak to logs.

NOTE: the plan names ``pydantic-settings``; to keep this runnable without that
package installed we load env + TOML directly with pydantic v2 + ``tomllib``. The
behaviour (env-only secrets, TOML for non-secret tuning, hard validation) is identical.
"""

from __future__ import annotations

import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, Field, field_validator

from isreq_dashboard.domain.regions import ALLOWED_REGIONS, validate_windows_cover_24h

# Roles we refuse to connect as (defensive guard toward Art. VIII; the real
# superuser check happens at connect time against pg_roles).
FORBIDDEN_DB_USERS = {"postgres", "root"}
REQUIRED_SECRET_ENV = (
    "ISREQ_JIRA_BASE_URL",
    "ISREQ_JIRA_EMAIL",
    "ISREQ_JIRA_API_TOKEN",
)
REQUIRED_DB_ENV = (
    "ISREQ_DB_HOST",
    "ISREQ_DB_PORT",
    "ISREQ_DB_NAME",
    "ISREQ_DB_USER",
    "ISREQ_DB_PASSWORD",
)

# --- 12-factor / paas-charm integration ------------------------------------
# Inside a ``fastapi-framework`` charm the PostgreSQL integration is injected as
# ``POSTGRESQL_DB_*`` env vars (paas-charm naming), not as our ``ISREQ_DB_*``.
# Map the discrete parts (or the single connect string) onto the names the
# loader already validates, so nothing else in the loader has to change.
_PG_PARTS = {
    "ISREQ_DB_HOST": "POSTGRESQL_DB_HOSTNAME",
    "ISREQ_DB_PORT": "POSTGRESQL_DB_PORT",
    "ISREQ_DB_NAME": "POSTGRESQL_DB_NAME",
    "ISREQ_DB_USER": "POSTGRESQL_DB_USERNAME",
    "ISREQ_DB_PASSWORD": "POSTGRESQL_DB_PASSWORD",
}

# Charm config options surfaced by paas-charm (fastapi-framework) as ``APP_*`` env
# vars (verified on ck8s). A plain option keeps its name (jira-base-url ->
# APP_JIRA_BASE_URL); a *secret*-typed option appends the secret's key, so
# jira-api-token backed by a secret with key ``token`` arrives as
# APP_JIRA_API_TOKEN_TOKEN. Map each onto the ``ISREQ_JIRA_*`` the loader validates;
# candidates are tried in order so a plainer name still works if the wiring changes.
_JIRA_PARTS = {
    "ISREQ_JIRA_BASE_URL": ("APP_JIRA_BASE_URL",),
    "ISREQ_JIRA_EMAIL": ("APP_JIRA_EMAIL",),
    "ISREQ_JIRA_API_TOKEN": ("APP_JIRA_API_TOKEN_TOKEN", "APP_JIRA_API_TOKEN"),
}

# PagerDuty config under the charm: the token is a Vault-backed secret option, so
# (like the Jira token) it arrives as APP_PD_API_TOKEN_TOKEN; the team ids are a
# plain option (APP_PD_TEAM_IDS). Mapped onto the names the loader reads from .env
# locally, so the Vault path is a deploy-time swap, not a code change (issue #39).
_PD_PARTS = {
    "PD_API_TOKEN": ("APP_PD_API_TOKEN_TOKEN", "APP_PD_API_TOKEN"),
    "PD_TEAM_IDS": ("APP_PD_TEAM_IDS",),
}


def _overlay_charm_env(src: Mapping[str, str]) -> dict[str, str]:
    """``src`` plus any ``ISREQ_DB_*`` / ``ISREQ_JIRA_*`` derived from the charm's
    PostgreSQL integration and config options. Explicit ``ISREQ_*`` values always
    win; when no integration vars are present the mapping is returned unchanged (so
    direct ``.env`` runs and the test suite behave exactly as before)."""
    env = dict(src)
    for dst, pg in _PG_PARTS.items():
        if not env.get(dst) and env.get(pg):
            env[dst] = env[pg]
    for dst, candidates in (*_JIRA_PARTS.items(), *_PD_PARTS.items()):
        if env.get(dst):
            continue
        for src_key in candidates:
            if env.get(src_key):
                env[dst] = env[src_key]
                break
    cs = env.get("POSTGRESQL_DB_CONNECT_STRING")
    if cs and not all(env.get(k) for k in _PG_PARTS):
        u = urlsplit(cs)
        if u.hostname:
            env.setdefault("ISREQ_DB_HOST", u.hostname)
        if u.port:
            env.setdefault("ISREQ_DB_PORT", str(u.port))
        if u.path:
            env.setdefault("ISREQ_DB_NAME", u.path.lstrip("/"))
        if u.username:
            env.setdefault("ISREQ_DB_USER", unquote(u.username))
        if u.password:
            env.setdefault("ISREQ_DB_PASSWORD", unquote(u.password))
    return env


class PdToml(BaseModel):
    """Non-secret PagerDuty tuning (the ``[pd]`` block). Optional: when the block is
    absent the PagerDuty analysis is simply not configured and ISReq is unaffected."""

    db_schema: str = "pd"
    api_base: str = "https://api.pagerduty.com"
    team_ids: list[str] = []
    since: date = date(2026, 1, 1)
    # EMEA-baseline windows for the PD analysis (own copy; ISReq's are untouched).
    region_windows_utc: dict[str, dict[str, str]]

    @field_validator("region_windows_utc")
    @classmethod
    def _windows_cover_day(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        bad = set(v) - set(ALLOWED_REGIONS)
        if bad:
            raise ValueError(f"unknown region(s) in pd.region_windows_utc: {sorted(bad)}")
        validate_windows_cover_24h(v)  # raises on gap/overlap
        return v


class TomlConfig(BaseModel):
    """Non-secret tuning loaded from the TOML file."""

    project_key: str
    field_area: str
    field_sub_area: str
    field_pulse: str
    highest_priority_name: str = "Highest"
    ps5_blocker_label: str = "ps5-blocker"
    pr_mp_title_substring: str = "[PR/MP Review]"
    closed_statuses: list[str]
    untriaged_status: str = "Untriaged"
    in_review_status: str = "In Review"
    anchor_date: date
    low_n_threshold: int = 5
    reference_timezone: str = "EMEA"
    region_windows_utc: dict[str, dict[str, str]]
    pr_mp_default_visibility: str = "included"
    # Per-cadence sprint marks overlaid on charts: {"weekly": {"W12": "..."},
    # "per_pulse": {"IS Pulse 2026#09": "..."}}.
    period_marks: dict[str, dict[str, str]] = {}
    # PagerDuty analysis tuning (optional; absent -> PD not configured).
    pd: PdToml | None = None

    @field_validator("closed_statuses")
    @classmethod
    def _non_empty_closed(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("closed_statuses must not be empty")
        return v

    @field_validator("region_windows_utc")
    @classmethod
    def _windows_cover_day(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        bad = set(v) - set(ALLOWED_REGIONS)
        if bad:
            raise ValueError(f"unknown region(s) in region_windows_utc: {sorted(bad)}")
        validate_windows_cover_24h(v)  # raises on gap/overlap
        return v


class _Secret(str):
    """A string whose repr is masked so it never appears in logs/tracebacks."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "'***'"


class Settings:
    """Resolved configuration: non-secret TOML + masked secrets + paths."""

    def __init__(
        self,
        toml: TomlConfig,
        *,
        jira_base_url: str,
        jira_email: str,
        jira_api_token: str,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
        db_schema: str = "isreq",
        users_csv: Path | None = None,
        pd_api_token: str | None = None,
        pd_team_ids: list[str] | None = None,
    ) -> None:
        self.toml = toml
        self.jira_base_url = jira_base_url
        self.jira_email = jira_email
        self.jira_api_token = _Secret(jira_api_token)
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = _Secret(db_password) if db_password is not None else None
        self.db_schema = db_schema
        self.users_csv = users_csv
        # PagerDuty (optional; only set when the [pd] block + PD_API_TOKEN are present).
        self.pd_api_token = _Secret(pd_api_token) if pd_api_token else None
        self._pd_team_ids_env = list(pd_team_ids or [])

    # --- PagerDuty helpers ---------------------------------------------------
    @property
    def pd_db_schema(self) -> str:
        return self.toml.pd.db_schema if self.toml.pd else "pd"

    @property
    def pd_team_ids(self) -> list[str]:
        """Effective team ids: PD_TEAM_IDS from the env wins; otherwise the [pd]
        block's defaults (which carry the IS SRE team)."""
        if self._pd_team_ids_env:
            return self._pd_team_ids_env
        return list(self.toml.pd.team_ids) if self.toml.pd else []

    # --- connection helpers --------------------------------------------------
    def sqlalchemy_url(self) -> str:
        if not all([self.db_host, self.db_port, self.db_name, self.db_user, self.db_password]):
            raise ValueError("database connection settings are incomplete")
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def __repr__(self) -> str:  # secrets masked
        return (
            f"Settings(project_key={self.toml.project_key!r}, jira_base_url={self.jira_base_url!r}, "
            f"jira_email={self.jira_email!r}, db_user={self.db_user!r}, "
            f"jira_api_token='***', db_password='***')"
        )

    __str__ = __repr__

    # --- loader --------------------------------------------------------------
    @classmethod
    def load(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        require_db: bool = True,
    ) -> "Settings":
        env = _overlay_charm_env(os.environ if env is None else env)

        missing = [k for k in REQUIRED_SECRET_ENV if not env.get(k)]
        if require_db:
            missing += [k for k in REQUIRED_DB_ENV if not env.get(k)]
        if missing:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")

        config_file = Path(env.get("ISREQ_CONFIG_FILE", "config/config.toml"))
        if not config_file.is_file():
            raise RuntimeError(f"config file not found: {config_file}")
        with config_file.open("rb") as fh:
            toml = TomlConfig(**tomllib.load(fh))

        db_user = env.get("ISREQ_DB_USER")
        if require_db and db_user in FORBIDDEN_DB_USERS:
            raise RuntimeError(
                f"ISREQ_DB_USER={db_user!r} is forbidden; connect as the non-superuser "
                "role 'isreq_app' (Art. VIII)"
            )

        port = env.get("ISREQ_DB_PORT")
        users_csv = Path(env.get("ISREQ_USERS_CSV", "config/users-region.csv"))

        pd_team_ids = [t.strip() for t in (env.get("PD_TEAM_IDS") or "").split(",") if t.strip()]

        return cls(
            toml,
            jira_base_url=env["ISREQ_JIRA_BASE_URL"],
            jira_email=env["ISREQ_JIRA_EMAIL"],
            jira_api_token=env["ISREQ_JIRA_API_TOKEN"],
            db_host=env.get("ISREQ_DB_HOST"),
            db_port=int(port) if port else None,
            db_name=env.get("ISREQ_DB_NAME"),
            db_user=db_user,
            db_password=env.get("ISREQ_DB_PASSWORD"),
            users_csv=users_csv,
            pd_api_token=env.get("PD_API_TOKEN"),
            pd_team_ids=pd_team_ids,
        )
