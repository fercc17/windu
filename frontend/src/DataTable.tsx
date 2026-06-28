// Interactive data table: global search + per-column sort + auto filter-dropdowns
// for low-cardinality columns + pagination over the FULL dataset. Replaces the old
// 25-row static table so browse tabs (CMDB Juju models, etc.) work like the originals.
import { useMemo, useState } from "react";

import { fmt } from "./DataPanel";

type Row = Record<string, unknown>;
const PAGE_SIZE = 50;

function isEmpty(v: unknown) {
  return v === null || v === undefined || v === "";
}

function allNumeric(vals: unknown[]) {
  const present = vals.filter((v) => !isEmpty(v));
  return present.length > 0 &&
    present.every((v) => typeof v === "number" || !Number.isNaN(Number(v)));
}

export function DataTable({ data }: { data: Row[] }) {
  const cols = useMemo(() => {
    if (!data.length) return [];
    return Object.keys(data[0]).filter((c) => {
      const v = data[0][c];
      return !Array.isArray(v) && (typeof v !== "object" || v === null);
    });
  }, [data]);

  // Columns worth a dropdown filter: 2–25 distinct values, not unique-per-row.
  const filterCols = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const c of cols) {
      const set = new Set<string>();
      for (const r of data) {
        if (isEmpty(r[c])) continue;
        set.add(String(r[c]));
        if (set.size > 25) break;
      }
      if (set.size >= 2 && set.size <= 25 && set.size < data.length) {
        out[c] = [...set].sort();
      }
    }
    return out;
  }, [cols, data]);

  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data.filter((r) => {
      for (const [c, val] of Object.entries(filters)) {
        if (val && String(r[c] ?? "") !== val) return false;
      }
      if (!q) return true;
      return cols.some((c) => String(r[c] ?? "").toLowerCase().includes(q));
    });
  }, [data, cols, query, filters]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const numeric = allNumeric(filtered.map((r) => r[sortKey]));
    return [...filtered].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (isEmpty(av) && isEmpty(bv)) return 0;
      if (isEmpty(av)) return 1;
      if (isEmpty(bv)) return -1;
      const cmp = numeric
        ? Number(av) - Number(bv)
        : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [filtered, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const cur = Math.min(page, pageCount - 1);
  const rows = sorted.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  const toggleSort = (c: string) => {
    if (sortKey === c) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(c);
      setSortDir("asc");
    }
  };

  if (!data.length) return <p className="app__muted">No rows.</p>;

  return (
    <div className="dt">
      <div className="dt__controls">
        <input
          className="dt__search"
          placeholder={`Search ${data.length} rows…`}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setPage(0); }}
          spellCheck={false}
        />
        {Object.entries(filterCols).map(([c, opts]) => (
          <select
            key={c}
            className="dt__filter"
            value={filters[c] ?? ""}
            onChange={(e) => { setFilters((f) => ({ ...f, [c]: e.target.value })); setPage(0); }}
          >
            <option value="">{c}: all</option>
            {opts.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        ))}
        <span className="dt__count">{sorted.length.toLocaleString()} / {data.length.toLocaleString()}</span>
      </div>

      <div className="panel__tablewrap">
        <table className="panel__table dt__table">
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c} className="dt__th" onClick={() => toggleSort(c)}>
                  {c}{sortKey === c ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{cols.map((c) => <td key={c}>{fmt(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="dt__pager">
          <button disabled={cur === 0} onClick={() => setPage(cur - 1)}>‹ Prev</button>
          <span>Page {cur + 1} / {pageCount}</span>
          <button disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)}>Next ›</button>
        </div>
      )}
    </div>
  );
}
