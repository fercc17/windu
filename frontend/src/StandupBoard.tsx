// The Stand up board — full-parity rebuild: region toggles, role-tagged engineer
// chips with green/yellow/red ticket counts + 24h/pulse metrics, an expandable
// detail panel (To Do / WIP / Success / Distractors, 24H↔Pulse split, per-ticket
// matrix colour + priority ribbon), and a role×ticket colour legend.
import { useMemo, useState } from "react";

type Cat = "green" | "yellow" | "red";
export type SuTicket = {
  key: string;
  title: string;
  color: Cat;
  ribbon: string;
  priority: string;
  status: string;
  project: string;
  is_review: boolean;
  url: string;
  touched_24h: boolean;
};
export type SuEng = {
  name: string;
  email: string;
  role: string;
  manager: boolean;
  starred: boolean;
  touched_24h: number;
  touched_pulse: number;
  assigned_open: number;
  completed: number;
  alerts_ack_24h: number;
  alerts_res_24h: number;
  alerts_ack_pulse: number;
  alerts_res_pulse: number;
  colors: { green: number; yellow: number; red: number };
  groups: Record<string, SuTicket[]>;
};
export type SuSection = {
  type: "standup";
  title: string;
  last_fetch: string;
  regions: { key: string; engineers: SuEng[] }[];
  management: SuEng[];
  legend: Record<string, string>[];
};

const GROUPS = ["To Do", "WIP", "Success", "Distractors"];
const SPLIT_GROUPS = new Set(["WIP", "Success", "Distractors"]);

function Ribbon({ code, priority }: { code: string; priority: string }) {
  if (!code) return null;
  return <span className={"su-ribbon su-ribbon--" + code.toLowerCase()} title={"Priority: " + priority}>{code}</span>;
}

function TicketRow({ t }: { t: SuTicket }) {
  return (
    <div className={"su-ticket su-ticket--" + t.color}>
      <span className={"su-dot su-dot--" + t.color} />
      <Ribbon code={t.ribbon} priority={t.priority} />
      <a className="su-ticket__key" href={t.url} target="_blank" rel="noopener">{t.key}</a>
      <span className="su-ticket__title">{t.title}</span>
      {t.is_review && <span className="su-tag">PR/MP</span>}
      {t.status && <span className="su-ticket__status">{t.status}</span>}
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
      ) : (
        <TicketList items={items} />
      )}
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

function EngineerCard({ e }: { e: SuEng }) {
  const [open, setOpen] = useState(false);
  const c = e.colors;
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
      <div className="su-metrics">
        <Metrics label="24h" touched={e.touched_24h} ao={e.assigned_open} done={e.completed}
                 ack={e.alerts_ack_24h} res={e.alerts_res_24h} />
        <Metrics label="Pulse" touched={e.touched_pulse} ao={e.assigned_open} done={e.completed}
                 ack={e.alerts_ack_pulse} res={e.alerts_res_pulse} />
      </div>
      {open && (
        <div className="su-detail">
          {GROUPS.some((g) => (e.groups[g] || []).length) ? (
            GROUPS.map((g) => <Group key={g} name={g} items={e.groups[g] || []} />)
          ) : (
            <p className="su-empty">No in-pulse tickets.</p>
          )}
        </div>
      )}
    </div>
  );
}

function Column({ title, engineers }: { title: string; engineers: SuEng[] }) {
  const active = engineers.filter((e) => e.touched_pulse > 0 || e.assigned_open > 0).length;
  return (
    <div className="su-col">
      <div className="su-col__h">{title}<span className="su-col__count">{active}/{engineers.length}</span></div>
      {engineers.map((e) => <EngineerCard key={e.email} e={e} />)}
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
              {kinds.map((k) => <td key={k}><span className={"su-swatch su-swatch--" + r[k]} title={r[k]} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

export function StandupBoard({ section }: { section: SuSection }) {
  const cols = useMemo(() => {
    const c = section.regions.map((r) => ({ title: r.key, engineers: r.engineers }));
    if (section.management.length) c.push({ title: "Management", engineers: section.management });
    return c;
  }, [section]);

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (t: string) =>
    setHidden((h) => { const n = new Set(h); n.has(t) ? n.delete(t) : n.add(t); return n; });

  return (
    <div className="su">
      <div className="su-bar">
        {cols.map((col) => (
          <button key={col.title} type="button"
            className={"su-region-btn" + (hidden.has(col.title) ? "" : " is-active")}
            onClick={() => toggle(col.title)}>
            {col.title} <span className="su-region-btn__n">{col.engineers.length}</span>
          </button>
        ))}
        <span className="app__muted su-fetch">fetch {section.last_fetch}</span>
      </div>
      <Legend rows={section.legend} />
      <div className="su-board">
        {cols.filter((col) => !hidden.has(col.title)).map((col) => (
          <Column key={col.title} title={col.title} engineers={col.engineers} />
        ))}
      </div>
    </div>
  );
}
