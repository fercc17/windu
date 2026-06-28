"""PagerDuty analysis: read-only connector + sync into the ``pd`` schema.

A parallel of ``isreq_dashboard/jira`` for the second, independent analysis. GET-only
against PagerDuty REST v2, sync-then-read, additive into ``pd`` only. Shares nothing
with the ISReq data (Art. VIII isolation, PRS "no join").
"""
