"""Stub identity + IS-membership for phase 1.

Real auth (Canonical identity-charmers / OIDC) comes later. For now the current
user is taken from the ``X-Windu-User`` header or ``?as=`` query param (falling
back to ``WINDU_DEV_USER`` or the first roster member), and "IS member" is
decided against the roster seeded from the standup data (the 23 IS engineers).
This is what gates the IS-only tabs.
"""
from django.conf import settings

from cmdb.apps.standup.models import RoleSchedule, RosterAddition


def is_roster():
    """Set of lower-cased IS-member emails (the standup roster)."""
    emails = set(RoleSchedule.objects.values_list('engineer_email', flat=True).distinct())
    emails |= set(RosterAddition.objects.values_list('email', flat=True))
    return {e.lower() for e in emails if e}


def current_identity(request):
    email = (request.headers.get('X-Windu-User')
             or request.GET.get('as')
             or getattr(settings, 'WINDU_DEV_USER', '') or '').strip()
    roster = is_roster()
    if not email:
        # Default dev user = a roster member so IS-only tabs are visible out of the box.
        email = sorted(roster)[0] if roster else 'dev@canonical.com'
    member = email.lower() in roster
    return {
        'email': email,
        'display_name': email.split('@')[0].replace('.', ' ').title(),
        'is_is_member': member,
        'roles': ['is_member'] if member else [],
        'roster_size': len(roster),
    }
