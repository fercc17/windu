"""Pure change-management vocabulary: change types, lifecycle stages, id prefixes.

A local change-management module (the ``chg`` schema): ITIL-style change requests, each
with a type-prefixed incremental id, moving through a lifecycle. This module is pure
logic (no DB, no network) so the stage model and id scheme are unit-testable directly.
"""

from __future__ import annotations

from typing import Iterable

# --- change types -----------------------------------------------------------
NORMAL = "normal"
STANDARD = "standard"
EMERGENCY = "emergency"
CHANGE_TYPES = (NORMAL, STANDARD, EMERGENCY)

TYPE_LABEL = {NORMAL: "Normal", STANDARD: "Standard", EMERGENCY: "Emergency"}
TYPE_PREFIX = {NORMAL: "CR#", STANDARD: "sCR#", EMERGENCY: "eCR#"}
# First number per type, so an id reads e.g. CR#100 / eCR#200 / sCR#300 then increments.
TYPE_BASE = {NORMAL: 100, EMERGENCY: 200, STANDARD: 300}

# --- lifecycle stages (full ITIL) -------------------------------------------
DRAFT = "Draft"
ASSESS = "Assess"
APPROVE = "Approve"
SCHEDULED = "Scheduled"
IMPLEMENT = "Implement"
REVIEW = "Review"
CLOSED = "Closed"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

# the ordered happy path, then the two terminal off-flow states
HAPPY_PATH = (DRAFT, ASSESS, APPROVE, SCHEDULED, IMPLEMENT, REVIEW, CLOSED)
TERMINAL_OFF = (REJECTED, CANCELLED)
ALL_STAGES = HAPPY_PATH + TERMINAL_OFF
OPEN_STAGES = (DRAFT, ASSESS, APPROVE, SCHEDULED, IMPLEMENT, REVIEW)  # not closed/off-flow

# Per-type happy-path flow: Standard is pre-approved (skips Assess/Approve);
# Emergency is expedited through an ECAB approval (skips Assess + Scheduled).
FLOW = {
    NORMAL: (DRAFT, ASSESS, APPROVE, SCHEDULED, IMPLEMENT, REVIEW, CLOSED),
    STANDARD: (DRAFT, SCHEDULED, IMPLEMENT, REVIEW, CLOSED),
    EMERGENCY: (DRAFT, APPROVE, IMPLEMENT, REVIEW, CLOSED),
}

RISK_LEVELS = ("low", "medium", "high")
CLOSURE_CODES = ("successful", "successful_with_issues", "failed", "backed_out")

# board colours per stage
STAGE_COLOR = {
    DRAFT: "#7f8c8d", ASSESS: "#f39c12", APPROVE: "#2980b9", SCHEDULED: "#8e44ad",
    IMPLEMENT: "#16a085", REVIEW: "#d35400", CLOSED: "#27ae60",
    REJECTED: "#c0392b", CANCELLED: "#95a5a6",
}


def next_number(change_type: str, existing_numbers: Iterable[int]) -> int:
    """Next incremental number for ``change_type`` given the numbers already used by that
    type: ``max(existing)+1``, never below the type's base (so the first id is e.g. 100)."""
    nums = list(existing_numbers)
    base = TYPE_BASE[change_type]
    nxt = (max(nums) + 1) if nums else base
    return max(nxt, base)


def cr_id(change_type: str, number: int) -> str:
    """``("emergency", 200) -> "eCR#200"``."""
    return f"{TYPE_PREFIX[change_type]}{number}"


def is_open(stage: str) -> bool:
    return stage in OPEN_STAGES
