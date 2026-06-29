import { useEffect, useMemo, useState } from "react";

import { getMe, type Identity } from "./api/client";
import { PageView } from "./PageView";
import { NAV, type NavGroup, type Page, groupSlug, resolvePath } from "./pages";
import "./App.css";

function IdentityChip({ me }: { me: Identity | null }) {
  if (!me) return <span className="app__status"><span className="app__dot" /> identifying…</span>;
  return (
    <span className="app__status" title={`Roster size: ${me.roster_size}`}>
      <span className={"app__dot " + (me.is_is_member ? "app__dot--up" : "app__dot--down")} />
      {me.display_name} · {me.is_is_member ? "IS member" : "guest"}
    </span>
  );
}

/** First path segment, e.g. "/standup?region=AMER" -> "standup". */
function currentSeg(): string {
  return window.location.pathname.replace(/^\/+/, "").split("/")[0] || "";
}

/** Embed an original view (chrome-less) in an iframe.
 *  - "/charms/"                 -> is-cmdb Django (:8010, ?embed=1)
 *  - "8501:/PagerDuty_Overview" -> jira-analysis Streamlit (:8501, ?embed=true)
 *  - "{email}" in the path is substituted with the current user's email. */
function EmbeddedView({ embed, userEmail }: { embed: string; userEmail: string }) {
  const { protocol, hostname } = window.location;
  let port = "8010";
  let path = embed;
  let param = "embed=1";
  if (embed.startsWith("8501:")) {
    port = "8501";
    path = embed.slice(5);
    param = "embed=true";
  }
  path = path.replace("{email}", encodeURIComponent(userEmail));
  const src = `${protocol}//${hostname}:${port}${path}${path.includes("?") ? "&" : "?"}${param}`;
  return <iframe className="app__embed" src={src} title="embedded view" />;
}

/** Which data source backs a page (for the refresh bar). */
function pageSource(id: string): string {
  if (id === "standup") return "standup";
  if (id === "isreq" || id === "isdb") return "jira";
  if (id === "pagerduty") return "pd";
  return "cmdb"; // all CMDB-family tabs
}

function fmtUpdated(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? String(iso)
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Last-updated time + incremental fetch button, per data source. */
function RefreshBar({ source }: { source: string }) {
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setLastUpdated(null);
    fetch(`/api/refresh/${source}/`)
      .then((r) => r.json())
      .then((d) => alive && setLastUpdated(d.last_updated ?? null))
      .catch(() => {});
    return () => { alive = false; };
  }, [source]);

  const onRefresh = async () => {
    setBusy(true);
    try {
      const r = await fetch(`/api/refresh/${source}/`, { method: "POST" });
      const d = await r.json();
      setLastUpdated(d.last_updated ?? lastUpdated);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app__refresh">
      <button className="app__refresh__btn" onClick={onRefresh} disabled={busy} title="Incremental fetch">
        {busy ? "Fetching…" : "↻ Refresh"}
      </button>
      <span className="app__refresh__t">Last updated: <b>{fmtUpdated(lastUpdated)}</b></span>
    </div>
  );
}

function App() {
  const [me, setMe] = useState<Identity | null>(null);
  const [viewAs, setViewAs] = useState("");
  const [route, setRoute] = useState(currentSeg());

  useEffect(() => {
    getMe(viewAs || undefined).then(setMe).catch(() => setMe(null));
  }, [viewAs]);

  // Back/forward buttons.
  useEffect(() => {
    const onPop = () => setRoute(currentSeg());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const visibleNav = useMemo(
    () => NAV.filter((g) => !g.memberOnly || me?.is_is_member),
    [me],
  );

  const resolved = useMemo(() => resolvePath(route), [route]);
  const isVisible = (g: NavGroup | undefined) =>
    !!g && visibleNav.some((v) => v.label === g.label);

  const activeGroup: NavGroup | undefined =
    resolved && isVisible(resolved.group) ? resolved.group : visibleNav[0];
  const activePage: Page | undefined =
    resolved && activeGroup && resolved.group.label === activeGroup.label
      ? resolved.page
      : activeGroup?.pages[0];

  const navigate = (seg: string) => {
    window.history.pushState(null, "", "/" + seg);
    setRoute(seg);
  };

  // Pass URL query params through to the page (e.g. ?region=AMER), plus the
  // impersonated identity so server-side gating agrees.
  const params: Record<string, string | number> = useMemo(() => {
    const out: Record<string, string | number> = {};
    new URLSearchParams(window.location.search).forEach((v, k) => (out[k] = v));
    if (viewAs) out.as = viewAs;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, viewAs]);

  return (
    <div className="app">
      <header className="app__bar">
        <span className="app__brand">windu</span>
        <span className="app__spacer" />
        <input
          className="app__viewas"
          placeholder="view as email…"
          value={viewAs}
          onChange={(e) => setViewAs(e.target.value.trim())}
          spellCheck={false}
        />
        <IdentityChip me={me} />
      </header>

      <nav className="app__topbar" aria-label="Sections">
        {visibleNav.map((g) => (
          <button
            key={g.label}
            type="button"
            className={"app__tab" + (g.label === activeGroup?.label ? " is-active" : "")}
            onClick={() => navigate(groupSlug(g.label))}
          >
            {g.label}
            {g.memberOnly && <span className="app__isbadge">IS</span>}
          </button>
        ))}
      </nav>

      <div className="app__body">
        <nav className="app__nav" aria-label={activeGroup?.label}>
          {activeGroup?.pages.map((pg) => (
            <button
              key={pg.id}
              type="button"
              className={"app__navitem" + (pg.id === activePage?.id ? " is-active" : "")}
              onClick={() => navigate(pg.id)}
            >
              {pg.label}
            </button>
          ))}
        </nav>

        <div className="app__content">
          {activePage && <RefreshBar source={pageSource(activePage.id)} />}
          <main className={"app__main" + (activePage?.embed ? " app__main--embed" : "")}>
            {activePage && (activePage.embed ? (
              <EmbeddedView key={activePage.id} embed={activePage.embed} userEmail={viewAs || me?.email || ""} />
            ) : (
              <section className="app__panel">
                <h1 className="app__title">{activePage.label}</h1>
                <PageView page={activePage} params={params} stackPct={false} />
              </section>
            ))}
          </main>
        </div>
      </div>
    </div>
  );
}

export default App;
