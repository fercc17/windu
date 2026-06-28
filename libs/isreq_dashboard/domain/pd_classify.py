"""Derive cloud / juju model / charm / alert-type from a PagerDuty alert payload.

PagerDuty has no "cloud" or "juju model" fields. For Canonical IS the signal comes
from Alertmanager / the Canonical Observability Stack, and (confirmed against real
payloads) arrives as an Alertmanager **text block** inside the CEF custom details,
e.g. ``body.cef_details.details.firing``::

    Labels:
     - alertname = ArchiveServerIdleWorkerMetricMissing
     - juju_model = prod-archive-servers
     - juju_unit = ubuntu-mirror-1ss/2
     - juju_controller = prodstack-45-bootstack-ps45-prodstack-is
    Annotations:
     - summary = ...

So the labels are parsed out of that block. There is **no cloud label**; the cloud
is *derived* from ``juju_controller`` (``...ps5...`` -> ``ps5``, ``azure-<region>...``
-> ``azure``) via ``cloud_from_controller``. Many alerts are nagios/non-juju and
carry no juju labels at all -> their model/charm/cloud are honestly ``Unknown``.

This module is pure logic (no DB, no network) and stays backward compatible with a
structured dict-of-labels payload (used by the recorded fixture and any integration
that sends labels directly). Anything unmapped or unparseable becomes ``Unknown``
(never guessed). The raw payload is stored on the row (``pd_alert.raw_details``) so
this classifier can be re-run as the rules tighten, without re-syncing.

Region is intentionally NOT derived here: it is a function of the trigger timestamp
and the PD region windows, computed at read time via ``regions.region_from_timestamp``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from isreq_dashboard.domain.regions import UNKNOWN

# Candidate locations of the CEF details, most specific first. Each may be a dict of
# labels OR a dict whose ``firing``/``resolved`` values are Alertmanager text blocks.
DETAIL_PATHS: tuple[tuple[str, ...], ...] = (
    ("body", "cef_details", "details"),
    ("body", "cef_details", "custom_details"),
    ("body", "cef", "details"),
    ("body", "details"),
    ("body", "custom_details"),
    ("details",),
    ("custom_details",),
)

# Where the aggregated Alertmanager text blocks live within a details dict.
_BLOB_KEYS = ("firing", "resolved")

# Each field maps to the label keys that may carry it (matched case-insensitively).
# Order matters: the first non-empty value wins.
LABEL_KEYS: dict[str, tuple[str, ...]] = {
    "alertname": ("alertname", "alert_name", "alert"),
    "juju_model": ("juju_model", "model", "juju_model_name"),
    "juju_model_uuid": ("juju_model_uuid", "model_uuid"),
    "charm": ("juju_application", "juju_app", "application", "juju_charm", "charm"),
    "juju_unit": ("juju_unit", "unit"),
    "juju_controller": ("juju_controller", "controller"),
    "severity": ("severity", "level"),
    "cloud": ("cloud", "juju_cloud", "substrate", "cloud_name"),
}

# Fields that count toward "did we manage to classify this alert" coverage.
COVERAGE_FIELDS: tuple[str, ...] = ("alertname", "cloud", "juju_model", "charm")

_AM_LABEL_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.]+)\s*=\s*(.*?)\s*$")
# "[FIRING:2] ... (AlertName)" -> AlertName (the trailing parenthesised group name).
_MSG_ALERTNAME_RE = re.compile(r"\(([^()]+)\)\s*$")
# A prodstack id token in a juju controller/model name: prodstack5, prodstack-45, ps7.
_PS_RE = re.compile(r"(?:prodstack-?|ps)(\d+)")


@dataclass(frozen=True)
class AlertClass:
    """The derived classification of one alert. Every field is ``Unknown`` until a
    matching label is found (or, for cloud, derived from the controller)."""

    alertname: str = UNKNOWN
    severity: str = UNKNOWN
    cloud: str = UNKNOWN
    juju_model: str = UNKNOWN
    juju_model_uuid: str = UNKNOWN
    charm: str = UNKNOWN
    juju_unit: str = UNKNOWN
    juju_controller: str = UNKNOWN


def _dig(obj: Any, path: Sequence[str]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _parse_am_blob(blob: str) -> dict[str, str]:
    """Parse the FIRST ``Labels:`` block of an Alertmanager text notification into a
    ``{label: value}`` dict (lower-cased keys). Stops at ``Annotations:``/``Source:``
    or the next ``Labels:`` block (an aggregated notification lists several)."""
    out: dict[str, str] = {}
    in_labels = False
    for line in blob.splitlines():
        s = line.strip()
        if s == "Labels:":
            if out:  # a second block begins -> keep only the first (representative)
                break
            in_labels = True
            continue
        if s.startswith("Annotations:") or s.startswith("Source:"):
            if in_labels:
                break
            continue
        if in_labels:
            m = _AM_LABEL_RE.match(line)
            if m:
                out.setdefault(m.group(1).lower(), m.group(2))
    return out


def extract_labels(alert: Mapping[str, Any]) -> dict[str, str]:
    """Merge labels from every candidate detail location into one case-insensitive map.

    Handles both shapes: a direct dict of labels, and a dict whose ``firing``/
    ``resolved`` values are Alertmanager text blocks (the real Canonical shape).
    """
    merged: dict[str, str] = {}
    for path in DETAIL_PATHS:
        found = _dig(alert, path)
        if isinstance(found, Mapping):
            for k, v in found.items():
                if v is None:
                    continue
                if k in _BLOB_KEYS and isinstance(v, str) and "Labels:" in v:
                    for lk, lv in _parse_am_blob(v).items():
                        merged.setdefault(lk, lv)
                    continue
                sv = str(v).strip()
                if sv:
                    merged.setdefault(str(k).strip().lower(), sv)
        elif isinstance(found, str) and "Labels:" in found:
            for lk, lv in _parse_am_blob(found).items():
                merged.setdefault(lk, lv)
    return merged


def _first(labels: Mapping[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        v = labels.get(key.lower())
        if v:
            return v
    return UNKNOWN


def _match_known_cloud(s: str) -> str | None:
    """Canonical cloud token in a controller/label/model string, or None.

    Taxonomy locked with IS: prodstack collapses to the short ``ps{N}`` form (so the
    ``prodstack7`` label and a ``...ps7`` controller are one cloud), and every Azure
    region collapses to a single ``azure``.
    """
    v = s.lower()
    # Public-cloud providers collapse to one bucket each (the locked Azure rule,
    # extended to GCE/AWS for consistency — confirmed by the full backfill, which
    # surfaced per-region gce-*/aws-* labels alongside azure-*).
    if v.startswith("azure") or "azure-cloud" in v or "-azure-" in v:
        return "azure"
    if v.startswith("gce") or "gce-cloud" in v or "-gce-" in v:
        return "gce"
    if v.startswith("aws") or "aws-cloud" in v or "-aws-" in v:
        return "aws"
    m = _PS_RE.search(v)
    if m:
        return f"ps{m.group(1)}"
    return None


def cloud_from_controller(controller: str) -> str:
    """Derive the cloud from a juju controller name (the real cloud signal). An
    unrecognised controller yields ``Unknown`` (never guessed)."""
    if not controller or controller == UNKNOWN:
        return UNKNOWN
    return _match_known_cloud(controller) or UNKNOWN


def canonical_cloud(value: str) -> str:
    """Normalize an explicit ``cloud`` label to the locked taxonomy (``prodstack7`` ->
    ``ps7``, ``azure-eastus2`` -> ``azure``). An unrecognised but non-empty label is
    kept as-is (it is a real cloud name we just don't have a rule for) rather than
    discarded to ``Unknown``."""
    if not value or value == UNKNOWN:
        return UNKNOWN
    return _match_known_cloud(value) or value.strip()


def _alertname_from_message(alert: Mapping[str, Any]) -> str:
    """Fallback alertname from the trailing ``(GroupName)`` in the CEF message/summary."""
    for path in (("body", "cef_details", "message"), ("body", "cef_details", "description"), ("summary",)):
        v = _dig(alert, path)
        if isinstance(v, str):
            m = _MSG_ALERTNAME_RE.search(v.strip())
            if m:
                return m.group(1).strip()
    return UNKNOWN


def classify(alert: Mapping[str, Any]) -> AlertClass:
    """Classify one PagerDuty alert payload into cloud/model/charm/alertname/etc."""
    labels = extract_labels(alert)

    alertname = _first(labels, LABEL_KEYS["alertname"])
    if alertname == UNKNOWN:
        alertname = _alertname_from_message(alert)

    juju_unit = _first(labels, LABEL_KEYS["juju_unit"])
    charm = _first(labels, LABEL_KEYS["charm"])
    if charm == UNKNOWN and juju_unit != UNKNOWN:
        charm = juju_unit.split("/")[0]  # app name is the unit without its /N index

    controller = _first(labels, LABEL_KEYS["juju_controller"])
    cloud_label = _first(labels, LABEL_KEYS["cloud"])
    cloud = canonical_cloud(cloud_label) if cloud_label != UNKNOWN else cloud_from_controller(controller)

    severity = _first(labels, LABEL_KEYS["severity"])
    if severity == UNKNOWN:
        body_sev = _dig(alert, ("body", "cef_details", "severity"))
        if body_sev:
            severity = str(body_sev).strip()

    return AlertClass(
        alertname=alertname,
        severity=severity,
        cloud=cloud,
        juju_model=_first(labels, LABEL_KEYS["juju_model"]),
        juju_model_uuid=_first(labels, LABEL_KEYS["juju_model_uuid"]),
        charm=charm,
        juju_unit=juju_unit,
        juju_controller=controller,
    )


def coverage(items: Sequence[AlertClass]) -> dict[str, float]:
    """Fraction of alerts with a parseable value (not ``Unknown``) per coverage field.

    Lets a thin breakdown be shown honestly: a per-charm chart over 40%-covered data
    is not the same as one over 95%-covered data. Empty input -> all 0.0.
    """
    n = len(items)
    if n == 0:
        return {f: 0.0 for f in COVERAGE_FIELDS}
    out: dict[str, float] = {}
    for f in COVERAGE_FIELDS:
        known = sum(1 for it in items if getattr(it, f) != UNKNOWN)
        out[f] = known / n
    return out
