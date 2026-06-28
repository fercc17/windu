"""Unified-shell API: identity + per-page sections."""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .identity import current_identity
from .sections import PAGES


@api_view(['GET'])
def me(request):
    """Stub identity for the React shell (drives IS-only tab visibility)."""
    return Response(current_identity(request))


@api_view(['GET'])
def page(request, page_id):
    """Return {title, sections:[...]} for a page id, gating IS-only pages."""
    entry = PAGES.get(page_id)
    if entry is None:
        return Response({"error": f"unknown page '{page_id}'"}, status=404)
    title, member_only, builder = entry

    if member_only and not current_identity(request)['is_is_member']:
        return Response({"error": "forbidden", "title": title,
                         "sections": [{"type": "kv", "title": title,
                                       "values": {"access": "IS members only"}}]},
                        status=403)
    try:
        sections = builder(request, request.GET)
    except Exception as exc:  # keep one bad page from 500-ing the shell
        sections = [{"type": "kv", "title": title,
                     "values": {"error": str(exc)}}]
    return Response({"title": title, "sections": sections})
