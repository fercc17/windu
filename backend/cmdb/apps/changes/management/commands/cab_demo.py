"""
Walk a Standard, a Normal, and an Emergency change request end-to-end against
**real** CMDB configuration items — no fabricated data.

Picks real environments / nodes from the database, drives each CR through its
lifecycle via the service layer, and prints a transcript. Rolls everything back
unless --commit is given.

    python manage.py cab_demo            # dry run, rolls back
    python manage.py cab_demo --commit   # persist the demo CRs (real CIs)
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from cmdb.apps.environments.models import Environment, EnvironmentDependency
from cmdb.apps.maintenance.models import MaintenanceWindow
from cmdb.apps.netbox.models import Node
from cmdb.apps.changes import services
from cmdb.apps.changes.models import Change, ChangeTarget, ChangeTemplate


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Demo the CAB (standard / normal / emergency) against real CMDB items."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help="Persist demo data (default: roll back).")

    # -- printing ----------------------------------------------------------- #
    def h(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{'=' * 72}\n{text}\n{'=' * 72}"))

    def step(self, text):
        self.stdout.write(self.style.HTTP_INFO(f"  → {text}"))

    def show(self, c):
        c.refresh_from_db()
        self.stdout.write(
            f"    {c.reference}  type={c.change_type}  impact={c.get_temperature_display()}  "
            f"status={c.status}  risk={c.risk_tier}({c.risk_score})  region={c.region or '—'}"
        )

    def show_impact(self, c):
        for a in c.affected.all()[:12]:
            flag = 'resilient' if a.resilient else 'NON-resilient'
            owners = ' / '.join(x for x in (a.cia_risk_owner, a.cia_owner) if x) or '-'
            self.stdout.write(
                f"      impact: {a.environment_name}  [{a.impact_type} d{a.dependency_depth}]  "
                f"{a.env_type or '-'} crit{a.criticality_tier or '-'}  {flag}  owners={owners}"
            )
        n = c.affected.count()
        if n > 12:
            self.stdout.write(f"      … and {n - 12} more")

    def show_chain(self, c):
        rows = c.approvals.filter(version=c.version).order_by('level', 'party')
        if not rows:
            self.stdout.write("      chain: (none — peer-ack / auto)")
        for r in rows:
            self.stdout.write(f"      L{r.level} {r.role:<14} {r.party:<28} [{r.decision}]")

    # -- pickers (real CIs) ------------------------------------------------- #
    def _depended(self):
        return set(EnvironmentDependency.objects.values_list('depends_on_name', flat=True))

    def pick_dev(self):
        """A dev env with no downstream dependents (impact stays all-dev)."""
        depended = self._depended()
        devs = Environment.objects.filter(env_type='dev').order_by('name')
        for e in devs:
            if e.name not in depended:
                return e
        return devs.first()

    def pick_prod(self):
        """A tier-2 prod env with CIA owners and no downstream (a clean medium CR)."""
        depended = self._depended()
        qs = (Environment.objects.filter(env_type='prod', criticality_tier=2)
              .exclude(cia_owner__isnull=True).exclude(cia_owner='')
              .exclude(cia_risk_owner__isnull=True).exclude(cia_risk_owner='')
              .order_by('name'))
        for e in qs:
            if e.name not in depended:
                return e
        return qs.first() or Environment.objects.filter(env_type='prod').first()

    def pick_node(self):
        """A node hosting a placed env that has downstream dependents (real blast radius)."""
        depended = self._depended()
        e = (Environment.objects.exclude(primary_node__isnull=True)
             .filter(name__in=depended).select_related('primary_node').order_by('name').first())
        if e:
            return e.primary_node
        return (Node.objects.annotate(n=Count('primary_environments'))
                .filter(n__gt=0).order_by('-n').first())

    def window(self, days, **scope):
        start = timezone.now() + timedelta(days=days)
        return MaintenanceWindow.objects.create(
            starts_at=start, ends_at=start + timedelta(hours=2),
            reason='cab-demo window', created_by='cab-demo', **scope,
        )

    def cleanup(self):
        """Drop prior demo CRs + any fabricated cab-demo-* CIs from earlier versions."""
        Change.objects.filter(title__startswith='cab-demo').delete()
        Environment.objects.filter(name__startswith='cab-demo-').delete()
        EnvironmentDependency.objects.filter(environment_name__startswith='cab-demo-').delete()
        Node.objects.filter(hostname__startswith='cab-demo-').delete()
        ChangeTemplate.objects.filter(name__startswith='cab-demo').delete()

    # -- scenarios ---------------------------------------------------------- #
    def run_standard(self, dev):
        self.h(f"1) STANDARD — pre-approved template · real dev model: {dev.name}")
        tpl = ChangeTemplate.objects.create(
            name='cab-demo-dev-reboot', auto_approve=True, requires_all_resilient=False,
            allowed_target_types=['juju_model'], allowed_env_types=['dev'],
            default_execute_commands='juju ...',
        )
        c = Change.objects.create(
            title=f'cab-demo standard: reboot {dev.name}', change_type='standard',
            description=(f'Routine rolling reboot of the {dev.name} dev model to pick up a '
                         'config change. Pre-approved standard; no production impact.'),
            temperature='cold', staging_notes='Pre-stage config the day before.',
            template=tpl, proposer='someone', executer='sre-1',
            precheck_commands='x', execute_commands='x', verify_commands='x', rollback_commands='x',
            maintenance_window=self.window(2, environment=dev),
        )
        ChangeTarget.objects.create(change=c, target_type='juju_model', environment=dev)
        self.step("submit (guardrails hold -> stays standard, auto-approves)")
        services.submit_change(c)
        self.show(c); self.show_impact(c); self.show_chain(c)
        self.step("schedule -> run -> applied -> close")
        services.schedule_change(c); services.start_change(c); services.begin_verify(c)
        services.complete_change(c, passed=True); services.close_change(c)
        self.show(c)

    def run_normal(self, prod):
        self.h(f"2) NORMAL — full chain + notify CIA owners · real prod model: {prod.name}")
        c = Change.objects.create(
            title=f'cab-demo normal: patch {prod.name}', change_type='normal',
            description=(f'Apply the latest security patch to {prod.name} and roll the workers. '
                         'Live change, no downtime expected; consumers notified.'),
            temperature='hot', proposer='someone', executer='sre-1', notify_on_approval=True,
            precheck_commands='x', execute_commands='x', verify_commands='x', rollback_commands='x',
            maintenance_window=self.window(8, environment=prod),  # normal: >=1 week ahead
        )
        ChangeTarget.objects.create(change=c, target_type='juju_model', environment=prod)
        self.step("submit (impact + risk + chain computed from real data)")
        services.submit_change(c)
        self.show(c); self.show_impact(c); self.show_chain(c)
        self.step("approve down the chain (peer -> tech-lead -> [change-mgr if high] -> consumers)")
        services.record_decision(c, level=1, decision='approved', by='sre-peer')
        services.record_decision(c, level=2, decision='approved', by='tech-lead')
        if c.approvals.filter(version=c.version, level=3, decision='pending').exists():
            services.record_decision(c, level=3, decision='approved', by='change-mgr')
        for a in c.approvals.filter(version=c.version, level=4, decision='pending'):
            services.record_decision(c, level=4, decision='approved', by='consumer', party=a.party)
        self.show(c)
        self.step("schedule -> notify -> run -> applied -> close")
        services.schedule_change(c)
        for n in c.notifications.all():
            self.stdout.write(f"      notify[{n.channel}/{n.variant}] -> {n.recipient}")
        services.start_change(c); services.begin_verify(c)
        services.complete_change(c, passed=True); services.close_change(c)
        self.show(c)

    def run_emergency(self, node):
        self.h(f"3) EMERGENCY — single L2 pre-exec, blast radius · real node: {node.hostname}")
        c = Change.objects.create(
            title=f'cab-demo emergency: take down {node.hostname}', change_type='emergency',
            description=(f'Emergency: drain and take {node.hostname} out of service to mitigate a '
                         'failing host. Placed models will be rescheduled; mandatory PIR to follow.'),
            temperature='cold', proposer='oncall', executer='sre-1',
            precheck_commands='x', execute_commands='x', verify_commands='x', rollback_commands='x',
            maintenance_window=self.window(1, node=node),
        )
        ChangeTarget.objects.create(change=c, target_type='node', node=node)
        self.step("submit (node target -> placed envs + downstream dependents)")
        services.submit_change(c)
        self.show(c); self.show_impact(c); self.show_chain(c)
        self.step("single tech-lead (L2) approves pre-exec")
        services.record_decision(c, level=2, decision='approved', by='tech-lead')
        self.show(c)
        self.step("schedule -> run -> verify FAILS -> rolled_back -> close WITH PIR")
        services.schedule_change(c); services.start_change(c); services.begin_verify(c)
        services.complete_change(c, passed=False)
        services.close_change(c, pir_notes='Rolled back; RCA filed (mandatory PIR).')
        self.show(c)

    # -- entrypoint --------------------------------------------------------- #
    def handle(self, *args, **opts):
        try:
            with transaction.atomic():
                self.cleanup()
                dev, prod, node = self.pick_dev(), self.pick_prod(), self.pick_node()
                if dev:
                    self.run_standard(dev)
                else:
                    self.stdout.write(self.style.WARNING("No dev env found — skipping standard."))
                if prod:
                    self.run_normal(prod)
                else:
                    self.stdout.write(self.style.WARNING("No suitable prod env — skipping normal."))
                if node:
                    self.run_emergency(node)
                else:
                    self.stdout.write(self.style.WARNING("No placed node — skipping emergency."))
                if not opts['commit']:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING(
                "\n(dry run — all demo CRs rolled back; pass --commit to keep)"))
            return
        self.stdout.write(self.style.SUCCESS("\nDemo data committed."))
