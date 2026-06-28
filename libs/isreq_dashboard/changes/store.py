"""Create/list CRs and maintenance windows, and seed realistic samples.

The only place that writes the ``chg`` schema. Plain SQLAlchemy on the chg models;
reused by the Streamlit pages. ``seed_samples`` is idempotent and lays down one CR in
every lifecycle stage (plus standard/emergency examples) and a few windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.chg_models import ChgChangeRequest, ChgMaintenanceWindow
from isreq_dashboard.domain import changes as ch


# --- change requests --------------------------------------------------------
def list_crs(session: Session, *, change_type: str | None = None, stage: str | None = None) -> list[ChgChangeRequest]:
    q = select(ChgChangeRequest)
    if change_type:
        q = q.where(ChgChangeRequest.change_type == change_type)
    if stage:
        q = q.where(ChgChangeRequest.stage == stage)
    return list(session.scalars(q.order_by(ChgChangeRequest.change_type, ChgChangeRequest.number)))


def next_cr_id(session: Session, change_type: str) -> tuple[str, int]:
    nums = session.scalars(
        select(ChgChangeRequest.number).where(ChgChangeRequest.change_type == change_type)
    )
    n = ch.next_number(change_type, nums)
    return ch.cr_id(change_type, n), n


def create_cr(
    session: Session,
    *,
    change_type: str,
    title: str,
    stage: str = ch.DRAFT,
    description: str | None = None,
    risk: str | None = None,
    requested_by: str | None = None,
    assignee: str | None = None,
    service: str | None = None,
    scheduled_start: datetime | None = None,
    scheduled_end: datetime | None = None,
    closure_code: str | None = None,
    now: datetime | None = None,
) -> ChgChangeRequest:
    now = now or datetime.now(timezone.utc)
    cid, n = next_cr_id(session, change_type)
    cr = ChgChangeRequest(
        id=cid, number=n, change_type=change_type, title=title, stage=stage,
        description=description, risk=risk, requested_by=requested_by, assignee=assignee,
        service=service, scheduled_start=scheduled_start, scheduled_end=scheduled_end,
        closure_code=closure_code, created_at=now, updated_at=now,
    )
    session.add(cr)
    session.flush()
    return cr


# --- maintenance windows ----------------------------------------------------
def list_windows(session: Session) -> list[ChgMaintenanceWindow]:
    return list(session.scalars(select(ChgMaintenanceWindow).order_by(ChgMaintenanceWindow.start_at)))


def create_window(
    session: Session,
    *,
    summary: str,
    start_at: datetime,
    end_at: datetime,
    services: list[str] | None = None,
    cr_id: str | None = None,
    created_by: str | None = None,
    description: str | None = None,
    status: str = "scheduled",
    now: datetime | None = None,
) -> ChgMaintenanceWindow:
    now = now or datetime.now(timezone.utc)
    mw = ChgMaintenanceWindow(
        summary=summary, start_at=start_at, end_at=end_at, services=list(services or []),
        cr_id=cr_id or None, status=status, created_by=created_by,
        description=description, created_at=now,
    )
    session.add(mw)
    session.flush()
    return mw


def window_status(mw: ChgMaintenanceWindow, now: datetime) -> str:
    """Live status derived from the window's time range (or its explicit Cancelled)."""
    if (mw.status or "").lower() == "cancelled":
        return "Cancelled"
    if now < mw.start_at:
        return "Scheduled"
    if mw.start_at <= now < mw.end_at:
        return "Active"
    return "Completed"


# --- sample data ------------------------------------------------------------
def seed_samples(session: Session, *, now: datetime | None = None) -> dict:
    """Idempotent: one CR in every lifecycle stage + standard/emergency examples + a few
    maintenance windows. No-op if any CR already exists."""
    now = now or datetime.now(timezone.utc)
    if session.scalar(select(ChgChangeRequest.id).limit(1)) is not None:
        return {"crs": 0, "windows": 0, "note": "already seeded"}

    def at(**kw):  # now-relative helper
        return now + timedelta(**kw)

    crs = {}

    def cr(key, **kw):
        c = create_cr(session, now=now, **kw)
        if key:
            crs[key] = c
        return c

    # --- NORMAL (CR#100..): one per stage of the full ITIL path + both off-flow states,
    #     and a second Closed to exercise a different closure code (backed_out).
    cr(None, change_type=ch.NORMAL, stage=ch.DRAFT, risk="medium",
       title="Upgrade nova-compute to 2024.1 on prodstack6", service="prodstack6 / openstack",
       requested_by="Colin Misare", assignee="Colin Misare",
       description="Rolling hypervisor package upgrade; live-migrate guests per AZ.")
    cr(None, change_type=ch.NORMAL, stage=ch.ASSESS, risk="high",
       title="Ceph cluster expansion (+12 OSDs) on prodstack5", service="prodstack5 / ceph",
       requested_by="Barry Price", assignee="Gianluca Perna")
    cr(None, change_type=ch.NORMAL, stage=ch.APPROVE, risk="medium",
       title="Rotate TLS certificates for is-identity (openldap)", service="is-identity / ldap",
       requested_by="Christos Betzelos", assignee="Christos Betzelos")
    cr("n_sched", change_type=ch.NORMAL, stage=ch.SCHEDULED, risk="medium",
       title="Kernel patching wave for prodstack6 hypervisors", service="prodstack6 / openstack",
       requested_by="Colin Misare", assignee="Alexandre Gomes",
       scheduled_start=at(days=2), scheduled_end=at(days=2, hours=4))
    cr("n_impl", change_type=ch.NORMAL, stage=ch.IMPLEMENT, risk="medium",
       title="MySQL router failover validation on prodstack6", service="prodstack6 / openstack",
       requested_by="Benjamin Allot", assignee="Benjamin Allot",
       scheduled_start=at(hours=-1), scheduled_end=at(hours=1))
    cr(None, change_type=ch.NORMAL, stage=ch.REVIEW, risk="low",
       title="Archive-mirror disk expansion (azure eastus2)", service="azure / archive-mirror",
       requested_by="Loïc Gomez", assignee="Loïc Gomez",
       scheduled_start=at(days=-1), scheduled_end=at(days=-1, hours=2))
    cr("n_closed", change_type=ch.NORMAL, stage=ch.CLOSED, risk="low", closure_code="successful",
       title="Grafana dashboard rollout for content-cache", service="content-cache",
       requested_by="Paul Collins", assignee="Paul Collins",
       scheduled_start=at(days=-7), scheduled_end=at(days=-7, hours=1))
    cr(None, change_type=ch.NORMAL, stage=ch.CLOSED, risk="high", closure_code="backed_out",
       title="MTU change on the prodstack6 fabric (rolled back)", service="prodstack6 / openstack",
       requested_by="Colin Misare", assignee="Colin Misare",
       description="Backed out: tenant traffic degraded; reverted within the window.",
       scheduled_start=at(days=-5), scheduled_end=at(days=-5, hours=2))
    cr(None, change_type=ch.NORMAL, stage=ch.REJECTED, risk="high",
       title="Disable monitoring on prod-launchpad-git", service="prod-launchpad-git",
       requested_by="Matheus Carvalho", assignee="CAB",
       description="Rejected at CAB: removes alerting on a tier-1 service.")
    cr("n_cancelled", change_type=ch.NORMAL, stage=ch.CANCELLED, risk="medium",
       title="ps45 bootstack reboot", service="ps45 / bootstack",
       requested_by="Colin Misare", assignee="Colin Misare",
       scheduled_start=at(days=1), scheduled_end=at(days=1, hours=2),
       description="Cancelled: superseded by the kernel-patching wave.")

    # --- STANDARD (sCR#300..): pre-approved, so Draft -> Scheduled -> Implement -> Review
    #     -> Closed (successful_with_issues), plus Cancelled.
    cr(None, change_type=ch.STANDARD, stage=ch.DRAFT, risk="low",
       title="Add a Grafana panel to the ceph dashboard (ps5)", service="ps5 / ceph",
       requested_by="Barry Price", assignee="Barry Price")
    cr("s_sched", change_type=ch.STANDARD, stage=ch.SCHEDULED, risk="low",
       title="Weekly telegraf config refresh (all clouds)", service="all clouds",
       requested_by="Alexandre Gomes", assignee="Alexandre Gomes",
       scheduled_start=at(days=1), scheduled_end=at(days=1, hours=1))
    cr(None, change_type=ch.STANDARD, stage=ch.IMPLEMENT, risk="low",
       title="Rotate the read-only monitoring API token", service="monitoring",
       requested_by="Christos Betzelos", assignee="Christos Betzelos",
       scheduled_start=at(minutes=-20), scheduled_end=at(minutes=40))
    cr(None, change_type=ch.STANDARD, stage=ch.REVIEW, risk="low",
       title="Prometheus rule reload (ps7)", service="ps7 / prometheus",
       requested_by="Gianluca Perna", assignee="Gianluca Perna",
       scheduled_start=at(hours=-6), scheduled_end=at(hours=-5, minutes=30))
    cr(None, change_type=ch.STANDARD, stage=ch.CLOSED, risk="low",
       closure_code="successful_with_issues",
       title="Bump the content-cache squid config", service="content-cache",
       requested_by="Paul Collins", assignee="Paul Collins",
       description="Completed with minor issues: one node needed a manual restart.",
       scheduled_start=at(days=-2), scheduled_end=at(days=-2, minutes=30))
    cr(None, change_type=ch.STANDARD, stage=ch.CANCELLED, risk="low",
       title="Routine log-rotate tweak", service="all clouds",
       requested_by="Alexandre Gomes", assignee="Alexandre Gomes",
       description="Cancelled: superseded by a config-management change.")

    # --- EMERGENCY (eCR#200..): expedited via ECAB. Draft -> Approve -> Implement ->
    #     Review -> Closed (failed), plus Rejected and Cancelled.
    cr(None, change_type=ch.EMERGENCY, stage=ch.DRAFT, risk="high",
       title="Mitigate a memory leak in nova-api (prodstack6)", service="prodstack6 / openstack",
       requested_by="Colin Misare", assignee="Colin Misare")
    cr(None, change_type=ch.EMERGENCY, stage=ch.APPROVE, risk="high",
       title="ECAB: emergency certificate replacement for Launchpad", service="prod-launchpad",
       requested_by="Gianluca Perna", assignee="ECAB")
    cr("e_impl", change_type=ch.EMERGENCY, stage=ch.IMPLEMENT, risk="high",
       title="Restart stuck nfs-ganesha on prod-launchpad-git", service="prod-launchpad-git",
       requested_by="Colin Misare", assignee="Colin Misare",
       scheduled_start=at(minutes=-30), scheduled_end=at(minutes=30),
       description="ECAB-approved: Git E2E failing; bounce nfs-ganesha to restore service.")
    cr(None, change_type=ch.EMERGENCY, stage=ch.REVIEW, risk="high",
       title="Restored the RabbitMQ cluster on prodstack5", service="prodstack5",
       requested_by="Benjamin Allot", assignee="Benjamin Allot",
       scheduled_start=at(hours=-3), scheduled_end=at(hours=-2))
    cr(None, change_type=ch.EMERGENCY, stage=ch.CLOSED, risk="high", closure_code="failed",
       title="Emergency DNS failover (ps7)", service="ps7 / prod-authoritative-nameservers",
       requested_by="Gianluca Perna", assignee="Gianluca Perna",
       description="Failed: failover did not take; resolved via a follow-up change.",
       scheduled_start=at(days=-2), scheduled_end=at(days=-2, hours=1))
    cr(None, change_type=ch.EMERGENCY, stage=ch.REJECTED, risk="high",
       title="Bypass the change freeze for a non-urgent patch", service="prodstack6",
       requested_by="Matheus Carvalho", assignee="ECAB",
       description="Rejected by ECAB: not a genuine emergency; raise as a normal change.")
    cr(None, change_type=ch.EMERGENCY, stage=ch.CANCELLED, risk="high",
       title="Emergency reboot of a ceph mon (ps5)", service="ps5 / ceph",
       requested_by="Barry Price", assignee="Barry Price",
       description="Cancelled: the mon recovered on its own before action.")

    # --- maintenance windows: one in every status (Scheduled / Active / Completed / Cancelled).
    create_window(session, summary="Kernel patching — prodstack6 hypervisors",
        cr_id=crs["n_sched"].id, services=["prodstack6"],
        start_at=at(days=2), end_at=at(days=2, hours=4), created_by="Alexandre Gomes", now=now)  # Scheduled
    create_window(session, summary="Weekly telegraf config refresh",
        cr_id=crs["s_sched"].id, services=["prodstack5", "prodstack6", "ps7", "azure"],
        start_at=at(days=1), end_at=at(days=1, hours=1), created_by="Alexandre Gomes", now=now)  # Scheduled
    create_window(session, summary="MySQL router failover validation",
        cr_id=crs["n_impl"].id, services=["prodstack6"],
        start_at=at(hours=-1), end_at=at(hours=1), created_by="Benjamin Allot", now=now)  # Active
    create_window(session, summary="EMERGENCY — nfs-ganesha restart (Git)",
        cr_id=crs["e_impl"].id, services=["prod-launchpad-git"],
        start_at=at(minutes=-30), end_at=at(minutes=30), created_by="Colin Misare", now=now)  # Active
    create_window(session, summary="Content-cache Grafana rollout",
        cr_id=crs["n_closed"].id, services=["content-cache"],
        start_at=at(days=-7), end_at=at(days=-7, hours=1), created_by="Paul Collins", now=now)  # Completed
    create_window(session, summary="ps45 bootstack reboot (cancelled)",
        cr_id=crs["n_cancelled"].id, services=["ps45"], status="cancelled",
        start_at=at(days=1), end_at=at(days=1, hours=2), created_by="Colin Misare", now=now)  # Cancelled

    session.commit()
    return {"crs": len(list_crs(session)), "windows": len(list_windows(session))}
