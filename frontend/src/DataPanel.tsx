// Generic JSON renderers (tables + key/value) reused as the "raw data" fallback
// beneath the per-page charts in PageView.

type Json = Record<string, unknown>;

export function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(1);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function isRecordArray(v: unknown): v is Json[] {
  return Array.isArray(v) && v.length > 0 && typeof v[0] === "object" && v[0] !== null;
}

function Table({ rows }: { rows: Json[] }) {
  const cols = Object.keys(rows[0]).filter((c) => c !== "keys");
  const shown = rows.slice(0, 25);
  return (
    <>
      <div className="panel__tablewrap">
        <table className="panel__table">
          <thead>
            <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>{cols.map((c) => <td key={c}>{fmt(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > shown.length && (
        <p className="app__muted">…{rows.length - shown.length} more rows</p>
      )}
    </>
  );
}

// snake_case key -> human label, matching the Streamlit st.metric labels.
function humanize(k: string): string {
  const s = k.replace(/_/g, " ").replace(/\bpct\b/gi, "%").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Render key/value sections as Streamlit-style metric cards (small uppercase label
// above a large value) so the charm UI matches the non-charm dashboard.
export function Kv({ obj }: { obj: Json }) {
  return (
    <div className="panel__metrics">
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="metric">
          <span className="metric__label">{humanize(k)}</span>
          <span className="metric__value">{fmt(v)}</span>
        </div>
      ))}
    </div>
  );
}

export function RawTables({ data }: { data: Json }) {
  return (
    <div className="panel">
      {Object.entries(data).map(([key, val]) => (
        <section key={key} className="panel__block">
          <h3 className="panel__h">{key}</h3>
          {isRecordArray(val) ? (
            <Table rows={val} />
          ) : typeof val === "object" && val !== null ? (
            <Kv obj={val as Json} />
          ) : (
            <p>{fmt(val)}</p>
          )}
        </section>
      ))}
    </div>
  );
}
