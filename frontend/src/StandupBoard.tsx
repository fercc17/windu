// The Stand up board — region toggles (load on demand), role-tagged engineer
// chips grouped Project-then-Operations, expandable detail (tickets with matrix
// colours + ribbons + time, Jira worklog, calendar, today's-role picker), a
// weekend strip + open-counts summary + region-filtered Pulse counts, a colour
// legend, and a weekly role Schedule editor.
import { Fragment, useEffect, useMemo, useState } from "react";

type Cat = "green" | "yellow" | "red";
export type SuTicket = {
  key: string; title: string; color: Cat; ribbon: string; priority: string;
  status: string; project: string; is_review: boolean; url: string;
  touched_24h: boolean; time_label: string; effort_label: string; flagged: boolean;
};
export type SuCalendar = {
  busy: string; open: string; pto: string; pto_days: string; sd_days: string;
};
export type SuWindow = {
  total: string; alerts_overlap: string; alerts_no_overlap: string;
  jira_project: string; jira_ticket: string;
  gh_opened: number; gh_merged: number; gh_touched: number; gh_reviewed: number;
  busy: string; open: string; distractors: string;
};
export type SuEng = {
  name: string; email: string; role: string; manager: boolean; starred: boolean;
  touched_24h: number; touched_pulse: number; assigned_open: number; completed: number;
  alerts_ack_24h: number; alerts_res_24h: number; alerts_ack_pulse: number; alerts_res_pulse: number;
  jira_hours: string; jira_isdb_hours: string; jira_isreq_hours: string;
  calendar: SuCalendar | null;
  github: { created: number; merged: number; updated: number; reviewed: number } | null;
  windows: { "24h": SuWindow; today: SuWindow; pulse: SuWindow };
  sprint: { isreq: number; isdb: number };
  sd_days: string;
  handover?: { to_region: string; from_region: string; to: string; from: string } | null;
  colors: { green: number; yellow: number; red: number };
  groups: Record<string, SuTicket[]>;
};
export type SuSummaryItem = {
  key: string; icon: string; label: string; value: number; url: string; alert?: boolean;
};
export type SuSummary = { items: SuSummaryItem[] };
export type SuWeekend = {
  name: string; start: string; end: string; region: string;
  alerts_in_hours: number; alerts_off_hours: number; alerts_total: number;
};
export type SuPulseCounts = { pulse: number; rows: Record<string, unknown>[] };
export type SuSection = {
  type: "standup"; title: string; last_fetch: string; focus: boolean;
  summary: SuSummary | null; weekend: SuWeekend[];
  pulse_counts: SuPulseCounts | null;
  regions: { key: string; engineers: SuEng[] }[];
  management: SuEng[];
  legend: Record<string, string>[];
};

const GROUPS = ["To Do", "WIP", "Success", "Distractors"];
const SPLIT_GROUPS = new Set(["WIP", "Success", "Distractors"]);
const ROLES = ["PVG", "BVG", "GEN", "Project", "OFF"];
// Operations vs project distinction (#people-grouping).
const OPS_ROLES = new Set(["PVG", "BVG", "GEN"]);

// --- small bits ---
function Cell({ x }: { x: unknown }) {
  if (x && typeof x === "object" && "v" in (x as object)) {
    const c = x as { v: unknown; c: string };
    return <span className={"dt-c dt-c--" + c.c}>{String(c.v)}</span>;
  }
  return <>{String(x ?? "")}</>;
}

function Ribbon({ code, priority }: { code: string; priority: string }) {
  if (!code) return null;
  return <span className={"su-ribbon su-ribbon--" + code.toLowerCase()} title={"Priority: " + priority}>{code}</span>;
}

function TicketRow({ t }: { t: SuTicket }) {
  return (
    <div className={"su-ticket su-ticket--" + t.color + (t.flagged ? " is-flagged" : "")}>
      <span className={"su-dot su-dot--" + t.color} />
      {t.flagged && <span className="su-flag" title="Off-focus: in-progress ISReq that isn't Highest / ps5-blocker / PR-MP">⚑</span>}
      <Ribbon code={t.ribbon} priority={t.priority} />
      <a className="su-ticket__key" href={t.url} target="_blank" rel="noopener">{t.key}</a>
      <span className="su-ticket__title">{t.title}</span>
      {t.is_review && <span className="su-tag">PR/MP</span>}
      {t.status && <span className="su-ticket__status">{t.status}</span>}
      {t.effort_label && <span className="su-ticket__effort" title="ISDB estimate ▸ invested">⏳ {t.effort_label}</span>}
      {t.time_label && <span className="su-ticket__time" title="Time you logged on this ticket">⏱ {t.time_label}</span>}
    </div>
  );
}

function TicketList({ items }: { items: SuTicket[] }) {
  if (!items.length) return <p className="su-empty">none</p>;
  return <>{items.map((t) => <TicketRow key={t.key} t={t} />)}</>;
}

function Group({ name, items }: { name: string; items: SuTicket[] }) {
  if (!items.length) return null;
  const split = SPLIT_GROUPS.has(name);
  const recent = items.filter((t) => t.touched_24h);
  const older = items.filter((t) => !t.touched_24h);
  return (
    <div className="su-group">
      <div className="su-group__h">{name} <span className="su-group__n">×{items.length}</span></div>
      {split ? (
        <div className="su-split">
          <div className="su-split__col"><div className="su-split__h">24H</div><TicketList items={recent} /></div>
          <div className="su-split__col"><div className="su-split__h">Pulse</div><TicketList items={older} /></div>
        </div>
      ) : <TicketList items={items} />}
    </div>
  );
}

function Metrics({ label, touched, ao, done, ack, res }: {
  label: string; touched: number; ao: number; done: number; ack: number; res: number;
}) {
  return (
    <span className="su-meta" title="touched · assigned-open · completed · alerts ack/resolved">
      <span className="su-meta__win">{label}</span>
      <span title="touched">✎{touched}</span>
      <span title="assigned open">📋{ao}</span>
      <span title="completed">✓{done}</span>
      <span title="alerts ack/resolved">🔔{ack}/{res}</span>
    </span>
  );
}

async function postJSON(url: string, body: object) {
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// One window column (24H / TODAY / PULSE) of the per-engineer metric breakdown.
function WindowCol({ head, w }: { head: string; w: SuWindow }) {
  return (
    <div className="su-wc">
      <div className="su-wc__h">{head}</div>
      <span className="su-wc__total" title="Total engaged time = alerts (no overlap) + Jira project + Jira ticket + busy">⏱ total <b>{w.total}</b></span>
      <span>🔥 alerts overlap <b>{w.alerts_overlap}</b></span>
      <span>🔥 alerts no overlap <b>{w.alerts_no_overlap}</b></span>
      <span>📋 Jira project <b>{w.jira_project}</b></span>
      <span>📋 Jira ticket <b>{w.jira_ticket}</b></span>
      <span>🔗 GH PRs opened <b>{w.gh_opened}</b></span>
      <span>🔗 GH PRs merged <b>{w.gh_merged}</b></span>
      <span>🔗 GH PRs touched <b>{w.gh_touched}</b></span>
      <span>🔗 GH PRs reviewed <b>{w.gh_reviewed}</b></span>
      <span>📅 busy <b>{w.busy}</b></span>
      <span>📅 open <b>{w.open}</b></span>
      {w.distractors && <span>🚧 distractors <b>{w.distractors}</b></span>}
    </div>
  );
}

function EngineerCard({ e, reload }: { e: SuEng; reload?: () => void }) {
  const [open, setOpen] = useState(false);
  const c = e.colors;
  const setTodayRole = async (role: string) => {
    await postJSON("/api/standup/role/", { engineer_email: e.email, role });
    reload?.();
  };
  return (
    <div className={"su-card su-role-" + e.role + (open ? " is-open" : "")}>
      <button type="button" className="su-card__head" onClick={() => setOpen((o) => !o)}>
        <span className="su-card__name">
          {e.name}{e.starred && <span className="su-star">★</span>}
          {e.manager && <span className="su-mgr">mgr</span>}
        </span>
        <span className="su-counts">
          {c.red > 0 && <span className="su-cnt su-cnt--red">{c.red}</span>}
          {c.yellow > 0 && <span className="su-cnt su-cnt--yellow">{c.yellow}</span>}
          {c.green > 0 && <span className="su-cnt su-cnt--green">{c.green}</span>}
          <span className="su-role-tag">{e.role}</span>
        </span>
      </button>
      {e.handover && (e.handover.from_region || e.handover.to_region) && (
        <div className="su-ho" title="On-call handover (APAC → EMEA → AMER): receives from / hands over to">
          {e.handover.from_region && (
            <span className={"su-ho__seg" + (e.handover.from ? "" : " is-unassigned")}>
              ← {e.handover.from || "unassigned"} · {e.handover.from_region}
            </span>
          )}
          {e.handover.to_region && (
            <span className={"su-ho__seg" + (e.handover.to ? "" : " is-unassigned")}>
              → {e.handover.to || "unassigned"} · {e.handover.to_region}
            </span>
          )}
        </div>
      )}
      <div className="su-metrics">
        <Metrics label="24h" touched={e.touched_24h} ao={e.assigned_open} done={e.completed}
                 ack={e.alerts_ack_24h} res={e.alerts_res_24h} />
        <Metrics label="Pulse" touched={e.touched_pulse} ao={e.assigned_open} done={e.completed}
                 ack={e.alerts_ack_pulse} res={e.alerts_res_pulse} />
      </div>
      {open && (
        <div className="su-detail">
          <div className="su-detrow">
            <label className="su-rolepick">Today's role:
              <select value={e.role} onChange={(ev) => setTodayRole(ev.target.value)}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
          </div>
          <div className="su-sprint" title="Their tickets in the current active sprint, per project">
            🏃 Current sprint: ISReq <b>{e.sprint.isreq}</b> · ISDB <b>{e.sprint.isdb}</b>
          </div>
          {e.windows && (
            <div className="su-wins">
              <WindowCol head="24H" w={e.windows["24h"]} />
              <WindowCol head="TODAY" w={e.windows.today} />
              <WindowCol head="PULSE" w={e.windows.pulse} />
            </div>
          )}
          {e.sd_days && <div className="su-sd">🎓 SD {e.sd_days}</div>}
          {GROUPS.some((g) => (e.groups[g] || []).length) ? (
            GROUPS.map((g) => <Group key={g} name={g} items={e.groups[g] || []} />)
          ) : <p className="su-empty">No in-pulse tickets.</p>}
        </div>
      )}
    </div>
  );
}

function Column({ title, engineers, reload }: { title: string; engineers: SuEng[]; reload?: () => void }) {
  // Project people first, then Operations, then Off (#people-grouping).
  const project = engineers.filter((e) => e.role === "Project");
  const ops = engineers.filter((e) => OPS_ROLES.has(e.role));
  const off = engineers.filter((e) => e.role === "OFF");
  const section = (label: string, list: SuEng[]) =>
    list.length ? (
      <div className="su-sub" key={label}>
        <div className="su-sub__h">{label}</div>
        {list.map((e) => <EngineerCard key={e.email} e={e} reload={reload} />)}
      </div>
    ) : null;
  return (
    <div className="su-col">
      <div className="su-col__h">{title}<span className="su-col__count">{engineers.length}</span></div>
      {section("Project", project)}
      {section("Operations", ops)}
      {section("Off", off)}
      {!engineers.length && <p className="su-empty">No one here.</p>}
    </div>
  );
}

function Legend({ rows }: { rows: Record<string, string>[] }) {
  if (!rows.length) return null;
  const kinds = Object.keys(rows[0]).filter((k) => k !== "role");
  return (
    <details className="su-legend">
      <summary>Legend — role × ticket colour</summary>
      <table className="su-legend__t">
        <thead><tr><th>Role</th>{kinds.map((k) => <th key={k}>{k}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.role}>
              <td className="su-legend__role">{r.role}</td>
              {kinds.map((k) => <td key={k}><span className={"su-swatch su-swatch--" + r[k]} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

type PdcCell = { v: string | number; c: string | null; delta?: { dir: "up" | "down"; label: string } | null };
type PdcRow = { label: string; is_total: boolean; is_weekend: boolean; values: Record<string, PdcCell> };
type PdcGroup = { label: string; cols: [string, string][] };
type PdcData = {
  pulse?: number; range?: string; groups?: PdcGroup[]; rows: PdcRow[];
  regions?: string[]; selected?: string[];
};

// Per-day pulse counts for the current pulse, read pre-computed from the DB and
// summed over the selected region(s) server-side (no recompute on selection).
function PulseCountsDaily({ regions }: { regions: string[] }) {
  const [data, setData] = useState<PdcData | null>(null);
  useEffect(() => {
    const qs = regions.map((r) => `regions=${encodeURIComponent(r)}`).join("&");
    fetch("/api/standup/pulse-counts/" + (qs ? `?${qs}` : ""))
      .then((r) => r.json()).then(setData).catch(() => setData({ rows: [] }));
  }, [regions]);
  if (!data || !data.groups) return null;
  if (!data.rows.length) return <p className="su-empty su-hint">No pulse counts computed yet — run <code>standup_compute_day_counts</code>.</p>;
  const flatCols = data.groups.flatMap((g) => g.cols);
  return (
    <div className="su-pcd">
      <div className="su-pcd__h">PULSE {data.pulse} COUNTS{data.range ? ` · ${data.range}` : ""}</div>
      <div className="panel__tablewrap">
        <table className="su-pcd__t">
          <thead>
            <tr>
              <th rowSpan={2} className="su-pcd__day">Day</th>
              {data.groups.map((g, i) => (
                <th key={i} colSpan={g.cols.length} className={"su-pcd__grp" + (g.label ? "" : " is-blank")}>{g.label}</th>
              ))}
            </tr>
            <tr>{flatCols.map(([key, label]) => <th key={key}>{label}</th>)}</tr>
          </thead>
          <tbody>
            {data.rows.map((row, ri) => (
              <tr key={ri} className={(row.is_total ? "is-total " : "") + (row.is_weekend ? "is-weekend" : "")}>
                <td className="su-pcd__day">{row.label}</td>
                {flatCols.map(([key]) => {
                  const c = row.values[key];
                  return (
                    <td key={key} className={c && c.c ? "dt-c dt-c--" + c.c : ""}>
                      {c ? c.v : ""}
                      {c && c.delta && (
                        <span className={"su-pcd__d su-pcd__d--" + c.delta.dir}>
                          {c.delta.dir === "down" ? "▼" : "▲"}{c.delta.label}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type SchedWeekRow = {
  slot: string; dow: string; date: string; iso: string;
  role_editable: boolean; note_editable: boolean; new_week: boolean;
};
type SchedRegion = { key: string; engineers: { email: string; name: string }[] };
type SchedData = {
  roles: string[];
  regions: SchedRegion[];
  week: SchedWeekRow[];
  defaults: Record<string, Record<string, string>>; // email -> slot -> role
  notes: Record<string, Record<string, string>>;     // email -> iso -> note
  overrides: Record<string, string>;                 // email -> active today override
};

function ScheduleModal({ onClose, reload }: { onClose: () => void; reload?: () => void }) {
  const [d, setD] = useState<SchedData | null>(null);
  const [paste, setPaste] = useState("");
  const [pasteSummary, setPasteSummary] = useState<{ roles: number; notes: number; errors: string[] } | null>(null);
  const refreshSched = () => fetch("/api/standup/schedule/").then((r) => r.json()).then(setD).catch(() => {});
  useEffect(() => { refreshSched(); }, []);

  const applyPaste = async () => {
    const res = await fetch("/api/standup/paste/", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paste }),
    });
    setPasteSummary(await res.json().catch(() => null));
    refreshSched();
  };

  const setWeekly = (email: string, slot: string, role: string) => {
    setD((p) => p && ({ ...p, defaults: { ...p.defaults, [email]: { ...p.defaults[email], [slot]: role } } }));
    postJSON("/api/standup/schedule/", { engineer_email: email, weekday: slot, role });
  };
  const setOverride = (email: string, role: string) => {
    setD((p) => { if (!p) return p; const o = { ...p.overrides }; if (role) o[email] = role; else delete o[email]; return { ...p, overrides: o }; });
    postJSON("/api/standup/role/", { engineer_email: email, role });
  };
  const setNote = (email: string, iso: string, note: string) =>
    setD((p) => p && ({ ...p, notes: { ...p.notes, [email]: { ...p.notes[email], [iso]: note } } }));
  const saveNote = (email: string, iso: string, note: string) =>
    postJSON("/api/standup/note/", { engineer_email: email, note_date: iso, note });

  return (
    <div className="su-modal__backdrop" onClick={onClose}>
      <div className="su-modal su-modal--wide" onClick={(e) => e.stopPropagation()}>
        <div className="su-modal__head">
          <b>Role schedule</b>
          <button onClick={() => { reload?.(); onClose(); }}>Done</button>
        </div>
        {!d ? <p className="su-empty">Loading…</p> : (
          <div className="su-modal__body">
            <details className="su-paste">
              <summary>Paste from spreadsheet</summary>
              <p className="su-paste__hint">
                Tab-separated. First row = engineer names (first names &amp; aliases work); each later row
                starts with a day (<code>Mon</code>…<code>Fri</code>; weekend rows ignored). <code>PVG</code>,
                {" "}<code>GEN</code>, <code>BVG</code>, <code>OFF</code> map directly; anything else
                (e.g. <code>PS7+</code>) becomes <b>Project</b> (kept as a note).
              </p>
              {pasteSummary && (
                <div className={"su-paste__sum" + (pasteSummary.errors?.length ? " is-warn" : "")}>
                  Applied {pasteSummary.roles} role(s) and {pasteSummary.notes} note(s).
                  {pasteSummary.errors?.length ? (
                    <ul>{pasteSummary.errors.map((er, i) => <li key={i}>{er}</li>)}</ul>
                  ) : null}
                </div>
              )}
              <textarea className="su-paste__ta" rows={5} spellCheck={false} value={paste}
                        onChange={(e) => setPaste(e.target.value)}
                        placeholder={"Date\tAfif\tColin\tNick\nWed, Jun 10\tBVG\tGEN\tOFF"} />
              <div><button type="button" className="su-act" onClick={applyPaste}>Apply paste</button></div>
            </details>
            {d.regions.map((region) => (
              <div key={region.key} className="su-sched__region">
                <div className="su-sched__rh">{region.key}</div>
                <div className="panel__tablewrap">
                  <table className="su-sched">
                    <thead>
                      <tr><th>Day</th>{region.engineers.map((e) => <th key={e.email}>{e.name}</th>)}</tr>
                    </thead>
                    <tbody>
                      {d.week.map((row) => (
                        <Fragment key={row.iso}>
                          {row.new_week && (
                            <tr className="su-sched__sep"><td colSpan={region.engineers.length + 1}>Next week</td></tr>
                          )}
                          <tr className={row.role_editable ? "" : "su-sched__rorow"}>
                            <td className="su-sched__day"><span>{row.dow}</span> <span className="su-sched__date">{row.date}</span></td>
                            {region.engineers.map((e) => {
                              const note = d.notes[e.email]?.[row.iso] ?? "";
                              return (
                                <td key={e.email} title={note || undefined}>
                                  {row.role_editable ? (
                                    <select value={d.defaults[e.email]?.[row.slot] ?? "GEN"}
                                            onChange={(ev) => setWeekly(e.email, row.slot, ev.target.value)}>
                                      {d.roles.map((r) => <option key={r} value={r}>{r}</option>)}
                                    </select>
                                  ) : (
                                    <span className="su-sched__rorole" title="Weekly role (set on this week)">{d.defaults[e.email]?.[row.slot] ?? "GEN"}</span>
                                  )}
                                  {row.note_editable ? (
                                    <input className="su-sched__note" type="text" value={note} placeholder="note…"
                                           maxLength={200} spellCheck={false}
                                           onChange={(ev) => setNote(e.email, row.iso, ev.target.value)}
                                           onBlur={(ev) => saveNote(e.email, row.iso, ev.target.value)} />
                                  ) : note ? <span className="su-sched__notero">{note}</span> : null}
                                </td>
                              );
                            })}
                          </tr>
                        </Fragment>
                      ))}
                      <tr className="su-sched__ovr">
                        <td>Today override</td>
                        {region.engineers.map((e) => (
                          <td key={e.email}>
                            <select value={d.overrides[e.email] ?? ""}
                                    onChange={(ev) => setOverride(e.email, ev.target.value)}>
                              <option value="">—</option>
                              {d.roles.map((r) => <option key={r} value={r}>{r}</option>)}
                            </select>
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
            <p className="su-sched__hint">
              <b>Roles</b> are weekly &amp; recurring (edit this week; next week is read-only).
              <b> Day notes</b> attach to a date (today or future). <b>Overrides</b> apply to today only.
              A calendar day-off auto-sets <b>OFF</b> unless an override is set.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function StandupBoard({ section, reload }: { section: SuSection; reload?: () => void }) {
  const cols = useMemo(() => {
    const c = section.regions.map((r) => ({ title: r.key, engineers: r.engineers }));
    if (section.management.length) c.push({ title: "Management", engineers: section.management });
    return c;
  }, [section]);

  const [selected, setSelected] = useState<Set<string>>(() => {
    const region = new URLSearchParams(window.location.search).get("region");
    const s = new Set<string>();
    if (region) {
      const want = region.toUpperCase();
      for (const r of section.regions) if (r.key.toUpperCase() === want) s.add(r.key);
    }
    return s;
  });
  const [schedOpen, setSchedOpen] = useState(false);
  const [modal, setModal] = useState<null | "offenders" | "aging">(null);
  const toggle = (t: string) =>
    setSelected((sel) => { const n = new Set(sel); n.has(t) ? n.delete(t) : n.add(t); return n; });
  const shown = (t: string) => selected.has(t);
  const selectedRegions = useMemo(
    () => section.regions.filter((r) => selected.has(r.key)).map((r) => r.key),
    [section.regions, selected]);
  const setFocus = async (on: boolean) => {
    await postJSON("/api/standup/focus/", { value: on ? "on" : "off" });
    reload?.();
  };

  return (
    <div className="su">
      <WeekendStrip items={section.weekend} />
      {section.summary && <SummaryStrip s={section.summary} />}
      <div className="su-bar">
        {cols.map((col) => (
          <button key={col.title} type="button"
            className={"su-region-btn" + (selected.has(col.title) ? " is-active" : "")}
            onClick={() => toggle(col.title)}>
            {col.title} <span className="su-region-btn__n">{col.engineers.length}</span>
          </button>
        ))}
        <label className="su-focus" title="When on, flags in-progress ISReq that isn't Highest, a ps5-blocker, or [PR/MP Review]">
          <input type="checkbox" checked={!!section.focus} onChange={(e) => setFocus(e.target.checked)} />
          Focus: Highest/PS5/PR-MP
        </label>
        <button type="button" className="su-act" onClick={() => setModal("offenders")} title="Alerts that fired 10+ times this year">🚨 Repeat alerts</button>
        <button type="button" className="su-act" onClick={() => setModal("aging")} title="Tickets sitting In Progress too long">🚧 Aging WIP</button>
        <button type="button" className="su-sched-btn" onClick={() => setSchedOpen(true)}>🗓 Schedule roles</button>
        <span className="app__muted su-fetch">fetch {section.last_fetch}</span>
      </div>
      <Legend rows={section.legend} />
      <PulseCountsDaily regions={selectedRegions} />
      <div className="su-board">
        {selected.size === 0 ? (
          <p className="su-empty su-hint">Select a region above to load engineers.</p>
        ) : (
          cols.filter((col) => shown(col.title)).map((col) => (
            <Column key={col.title} title={col.title} engineers={col.engineers} reload={reload} />
          ))
        )}
      </div>
      {schedOpen && <ScheduleModal onClose={() => setSchedOpen(false)} reload={reload} />}
      {modal === "offenders" && <OffendersModal onClose={() => setModal(null)} />}
      {modal === "aging" && <AgingModal regions={selectedRegions} onClose={() => setModal(null)} />}
    </div>
  );
}

function SummaryStrip({ s }: { s: SuSummary }) {
  if (!s.items?.length) return null;
  return (
    <div className="su-osum">
      {s.items.map((it) => (
        <a key={it.key} className={"su-os" + (it.alert ? " su-os--alert" : "")}
           href={it.url || undefined} target="_blank" rel="noopener">
          <span className="su-os__i">{it.icon}</span>
          <span className="su-os__l">{it.label}</span>
          <strong className="su-os__n">{it.value}</strong>
        </a>
      ))}
    </div>
  );
}

type OffRow = {
  title: string; year_count: number; recent_count: number;
  number: number | null; url: string; handlers: string[];
};

function OffendersModal({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<OffRow[] | null>(null);
  useEffect(() => {
    fetch("/api/standup/offenders/").then((r) => r.json())
      .then((d) => setRows(d.offenders ?? [])).catch(() => setRows([]));
  }, []);
  return (
    <div className="su-modal__backdrop" onClick={onClose}>
      <div className="su-modal" onClick={(e) => e.stopPropagation()}>
        <div className="su-modal__head"><b>Repeat offenders · this year</b><button onClick={onClose}>Close</button></div>
        <div className="su-modal__body">
          <p className="su-modal__intro">
            Alerts still firing in the <b>last 10 days</b> that have fired <b>more than 10 times this year</b>
            {" "}— chronic offenders to fix at the source. Team-wide; the volatile <code>[FIRING:n]</code> prefix is ignored when grouping.
          </p>
          {!rows ? <p className="su-empty">Loading…</p> : !rows.length ? (
            <p className="su-empty">No alert fired more than 10 times this year. 🎉</p>
          ) : (
            <table className="su-offt">
              <thead><tr>
                <th title="Distinct incidents this calendar year">×/yr</th>
                <th title="Distinct incidents in the last 10 days">10d</th>
                <th>Alert</th><th title="Who handled it in the last 10 days">Handled by (10d)</th>
              </tr></thead>
              <tbody>
                {rows.map((o, i) => (
                  <tr key={i}>
                    <td className="su-offt__n">{o.year_count}</td>
                    <td className="su-offt__n">{o.recent_count}</td>
                    <td className="su-offt__t">
                      {o.url ? <a href={o.url} target="_blank" rel="noopener">{o.number ? `#${o.number} ` : ""}{o.title}</a>
                             : <>{o.number ? `#${o.number} ` : ""}{o.title}</>}
                    </td>
                    <td className="su-offt__h">{o.handlers.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

type AgeRow = {
  key: string; title: string; assignee: string; status: string;
  age_label: string; age_seconds: number | null; url: string; level: Cat | null;
};

function AgingModal({ regions, onClose }: { regions: string[]; onClose: () => void }) {
  const [rows, setRows] = useState<AgeRow[] | null>(null);
  useEffect(() => {
    const qs = regions.map((r) => `regions=${encodeURIComponent(r)}`).join("&");
    fetch("/api/standup/aging-wip/" + (qs ? `?${qs}` : ""))
      .then((r) => r.json()).then((d) => setRows(d.aging ?? [])).catch(() => setRows([]));
  }, [regions]);
  const scope = regions.length ? regions.join(", ") : "all regions";
  return (
    <div className="su-modal__backdrop" onClick={onClose}>
      <div className="su-modal" onClick={(e) => e.stopPropagation()}>
        <div className="su-modal__head"><b>Aging WIP · in progress now</b><button onClick={onClose}>Close</button></div>
        <div className="su-modal__body">
          <p className="su-modal__intro">
            Tickets currently <b>In Progress / In Review</b> for {scope}, oldest first.
            {" "}<span className="su-swatch su-swatch--green" /> ≤2d ·
            {" "}<span className="su-swatch su-swatch--yellow" /> 3–5d ·
            {" "}<span className="su-swatch su-swatch--red" /> &gt;5d. Blocked tickets excluded.
          </p>
          {!rows ? <p className="su-empty">Loading…</p> : !rows.length ? (
            <p className="su-empty">No work-in-progress tickets for {scope}. 🎉</p>
          ) : (
            <table className="su-offt">
              <thead><tr><th title="Time in the current WIP streak">Age</th><th>Ticket</th><th>Assignee</th><th>Status</th></tr></thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.key}>
                    <td className={"su-offt__age" + (t.level ? " su-offt__age--" + t.level : "")}>{t.age_label}</td>
                    <td className="su-offt__t"><a href={t.url} target="_blank" rel="noopener">{t.key}</a> {t.title}</td>
                    <td className="su-offt__h">{t.assignee}</td>
                    <td className="su-offt__h">{t.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function WeekendStrip({ items }: { items: SuWeekend[] }) {
  if (!items.length) return null;
  return (
    <div className="su-weekend">
      <span className="su-weekend__h">🗓 Weekend on-call</span>
      {items.map((w, i) => (
        <span key={i} className="su-weekend__item">
          <b>{w.name}</b>
          <span className="su-weekend__d">{w.start}{w.end ? ` → ${w.end}` : ""}{w.region ? ` · ${w.region}` : ""}</span>
          <span className="su-weekend__a" title="Weekend alerts: in working hours (09–17 local) / outside">
            🔔 {w.alerts_in_hours} in-hrs / {w.alerts_off_hours} off-hrs
          </span>
        </span>
      ))}
    </div>
  );
}

// Pulse history with region toggle buttons (consistent with the board).
export function PulseHistory({ section }: {
  section: { rows: Record<string, unknown>[]; regions: string[] };
}) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set(section.regions));
  const toggle = (t: string) =>
    setSelected((s) => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; });
  const rows = section.rows.filter((r) => selected.has(String(r.region)));
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div className="su">
      <div className="su-bar">
        {section.regions.map((rk) => (
          <button key={rk} type="button"
            className={"su-region-btn" + (selected.has(rk) ? " is-active" : "")}
            onClick={() => toggle(rk)}>{rk}</button>
        ))}
      </div>
      {rows.length ? (
        <div className="panel__tablewrap">
          <table className="panel__table su-pc__t">
            <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{cols.map((c) => <td key={c}><Cell x={r[c]} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="su-empty su-hint">Select a region above.</p>}
    </div>
  );
}
