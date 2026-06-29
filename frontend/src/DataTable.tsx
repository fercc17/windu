// Interactive data table: global search + per-column sort + auto filter-dropdowns
// for low-cardinality columns + pagination over the FULL dataset. Replaces the old
// 25-row static table so browse tabs (CMDB Juju models, etc.) work like the originals.
import { useMemo, useState } from "react";

import { fmt } from "./DataPanel";

type Row = Record<string, unknown>;
const PAGE_SIZE = 50;

// A colored cell {v, c}: the backend bands a value green/yellow/red. We sort,
// filter and search on the underlying `v`, and render it with the colour.
type Cell = { v: unknown; c: string };
function isCell(v: unknown): v is Cell {
  return !!v && typeof v === "object" && !Array.isArray(v) && "v" in (v as object);
}
function cellVal(v: unknown): unknown {
  return isCell(v) ? v.v : v;
}
function renderCell(x: unknown) {
  if (isCell(x)) return <span className={"dt-c dt-c--" + x.c}>{fmt(x.v)}</span>;
  return fmt(x);
}

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
      if (isCell(v)) return true;
      return !Array.isArray(v) && (typeof v !== "object" || v === null);
    });
  }, [data]);

  // Columns worth a dropdown filter: 2–25 distinct values, not unique-per-row.
  const filterCols = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const c of cols) {
      const set = new Set<string>();
      for (const r of data) {
        const v = cellVal(r[c]);
        if (isEmpty(v)) continue;
        set.add(String(v));
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
        if (val && String(cellVal(r[c]) ?? "") !== val) return false;
      }
      if (!q) return true;
      return cols.some((c) => String(cellVal(r[c]) ?? "").toLowerCase().includes(q));
    });
  }, [data, cols, query, filters]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const numeric = allNumeric(filtered.map((r) => cellVal(r[sortKey])));
    return [...filtered].sort((a, b) => {
      const av = cellVal(a[sortKey]), bv = cellVal(b[sortKey]);
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
              <tr key={i}>{cols.map((c) => <td key={c}>{renderCell(r[c])}</td>)}</tr>
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
