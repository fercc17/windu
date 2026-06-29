"""Template context processors."""


def embed(request):
    """Expose an ``embed`` flag so base.html renders chrome-less (no navbar) when
    framed inside the windu shell. Sticky in the session so in-iframe drill-down
    navigation keeps the chrome hidden; ``?embed=0`` clears it."""
    val = request.GET.get("embed")
    if val is not None:
        request.session["embed"] = val in ("1", "true", "yes")
    return {"embed": bool(request.session.get("embed", False))}
