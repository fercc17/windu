import { useMemo, useState } from "react";

import { getMe, type Identity } from "./api/client";
import { PageView } from "./PageView";
import { NAV, type NavGroup } from "./pages";
import { useEffect } from "react";
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

function App() {
  const [me, setMe] = useState<Identity | null>(null);
  const [viewAs, setViewAs] = useState("");
  // Default to Operations (first subtab Stand up); falls back for non-members.
  const [groupLabel, setGroupLabel] = useState(NAV[0].label);
  const [pageId, setPageId] = useState(NAV[0].pages[0].id);

  useEffect(() => {
    getMe(viewAs || undefined).then(setMe).catch(() => setMe(null));
  }, [viewAs]);

  // Top-bar categories visible to this identity (Operations needs IS membership).
  const visibleNav = useMemo(
    () => NAV.filter((g) => !g.memberOnly || me?.is_is_member),
    [me],
  );

  const activeGroup: NavGroup | undefined =
    visibleNav.find((g) => g.label === groupLabel) ?? visibleNav[0];
  const activePage =
    activeGroup?.pages.find((p) => p.id === pageId) ?? activeGroup?.pages[0];

  const selectGroup = (g: NavGroup) => {
    setGroupLabel(g.label);
    setPageId(g.pages[0].id); // selecting a category loads its first subtab
  };

  const params: Record<string, string | number> = viewAs ? { as: viewAs } : {};

  return (
    <div className="app">
      <header className="app__bar">
        <span className="app__brand">windu</span>
        <span className="app__muted" style={{ marginLeft: 8 }}>IS one-stop shop</span>
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
            onClick={() => selectGroup(g)}
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
              onClick={() => setPageId(pg.id)}
            >
              {pg.label}
            </button>
          ))}
        </nav>

        <main className="app__main">
          {activePage && (
            <section className="app__panel">
              <h1 className="app__title">{activePage.label}</h1>
              <PageView page={activePage} params={params} stackPct={false} />
            </section>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
