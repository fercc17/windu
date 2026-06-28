"""Change Management module: local CRs + maintenance windows (the ``chg`` schema).

A third co-tenant analysis/tool in the IS Operations console. Unlike the read-only
ISReq and PagerDuty analyses, this one is interactive (CRs and windows are created from
the UI), but it owns its own data and never writes to Jira or PagerDuty.
"""
