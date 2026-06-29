import { useEffect, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getPage } from "./api/client";
import { Kv } from "./DataPanel";
import { DataTable } from "./DataTable";
import { StandupBoard, PulseHistory, type SuSection } from "./StandupBoard";
import type { Page } from "./pages";

type Row = Record<string, unknown>;
type SeriesSpec = { key: string; label: string; color: string; mark: "bar" | "line" | "area" };
type Mark = { x: string; label: string };
type Section =
  | { type: "kv"; title: string; values: Record<string, unknown> }
  | { type: "table"; title: string; data: Row[] }
  | {
      type: "chart";
      title: string;
      x: string;
      data: Row[];
      series: SeriesSpec[];
      height?: number;
      stacked?: boolean;
      marks?: Mark[];
    }
  | SuSection
  | { type: "pulse_history"; title: string; rows: Record<string, unknown>[]; regions: string[] };

// Recharts renders axis ticks/grid as SVG with light-theme defaults (#666 / #eee),
// which vanish on the dark canvas — pin readable dark-mode colours here.
const tick = { fontSize: 12, fill: "#b0b0b0" } as const;
const GRID = "#333";
const tooltipStyle = { background: "#1b1b1f", border: "1px solid #333", color: "#eee" } as const;
// Sprint-mark rule colour — matches the Streamlit MARK_COLOR (charts.py).
const MARK_COLOR = "#e8590c";

// Renormalise each row's stacked series to sum to 100 (the "Stacked bars as %"
// display toggle). Only meaningful for stacked charts; rows that sum to 0 pass through.
function toPct(data: Row[], series: SeriesSpec[]): Row[] {
  return data.map((row) => {
    const total = series.reduce((a, se) => a + (Number(row[se.key]) || 0), 0);
    if (!total) return row;
    const out: Row = { ...row };
    for (const se of series) out[se.key] = ((Number(row[se.key]) || 0) / total) * 100;
    return out;
  });
}

function ChartSection({ s, stackPct }: { s: Extract<Section, { type: "chart" }>; stackPct: boolean }) {
  const pct = stackPct && !!s.stacked;
  const data = pct ? toPct(s.data, s.series) : s.data;

  // A date axis (burndown / recovery projection) has ISO-date x values and often
  // hundreds of points — render short "Jun 26" labels and thin the ticks instead of
  // the angled per-category labels used for the (few) weekly/pulse period charts.
  const first = data.length ? String((data[0] as Row)[s.x] ?? "") : "";
  const isDateAxis = /^\d{4}-\d{2}-\d{2}/.test(first);
  const fmtDateTick = (v: unknown): string => {
    const d = new Date(String(v));
    return Number.isNaN(d.getTime())
      ? String(v)
      : d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
  };
  const xInterval = isDateAxis
    ? Math.max(0, Math.ceil(data.length / 12))   // ~12 dated labels, no overlap
    : data.length > 30
      ? Math.ceil(data.length / 15)
      : 0;                                        // few categories: show them all

  return (
    <section className="panel__block">
      <h3 className="panel__h">{s.title}{pct ? " (%)" : ""}</h3>
      <ResponsiveContainer width="100%" height={(s.height ?? 300) + 36}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 24, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
          <XAxis
            dataKey={s.x}
            tickFormatter={isDateAxis ? fmtDateTick : undefined}
            tick={isDateAxis ? tick : { ...tick, angle: -35, textAnchor: "end" }}
            interval={xInterval}
            minTickGap={isDateAxis ? 20 : 0}
            height={isDateAxis ? 40 : 64}
          />
          <YAxis tick={tick} domain={pct ? [0, 100] : undefined} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "#eee" }}
            labelFormatter={isDateAxis ? (v) => fmtDateTick(v) : undefined}
          />
          {s.series.length > 1 && <Legend />}
          {s.series.map((se) =>
            se.mark === "bar" ? (
              <Bar
                key={se.key}
                dataKey={se.key}
                name={se.label}
                fill={se.color}
                stackId={s.stacked ? "stack" : undefined}
              />
            ) : se.mark === "area" ? (
              <Area
                key={se.key}
                dataKey={se.key}
                name={se.label}
                stroke={se.color}
                fill={se.color}
                fillOpacity={0.2}
              />
            ) : (
              <Line
                key={se.key}
                dataKey={se.key}
                name={se.label}
                stroke={se.color}
                dot={false}
                strokeWidth={2}
              />
            ),
          )}
          {(s.marks ?? []).map((m) => (
            <ReferenceLine
              key={m.x}
              x={m.x}
              stroke={MARK_COLOR}
              strokeDasharray="4 3"
              strokeWidth={2}
              ifOverflow="extendDomain"
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
      {s.marks && s.marks.length > 0 && (
        <p className="panel__mark">
          ◆ sprint marks: {s.marks.map((m) => `${m.x} → ${m.label}`).join("  ·  ")}
        </p>
      )}
    </section>
  );
}

function TableSection({ s }: { s: Extract<Section, { type: "table" }> }) {
  return (
    <section className="panel__block">
      <h3 className="panel__h">{s.title}</h3>
      <DataTable data={s.data} />
    </section>
  );
}

function SectionView({ s, stackPct, reload }: { s: Section; stackPct: boolean; reload?: () => void }) {
  if (s.type === "standup") return <StandupBoard section={s} reload={reload} />;
  if (s.type === "pulse_history") return <PulseHistory section={s} />;
  if (s.type === "chart") return <ChartSection s={s} stackPct={stackPct} />;
  if (s.type === "table") return <TableSection s={s} />;
  return (
    <section className="panel__block">
      <h3 className="panel__h">{s.title}</h3>
      <Kv obj={s.values} />
    </section>
  );
}

export function PageView({
  page,
  params,
  stackPct,
}: {
  page: Page;
  params: Record<string, string | number>;
  stackPct: boolean;
}) {
  const [sections, setSections] = useState<Section[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  // Refetch whenever the endpoint or any View control changes. Serialising params
  // keeps the effect from refiring on referentially-new-but-equal objects.
  const key = `${page.endpoint}?${JSON.stringify(params)}#${nonce}`;
  useEffect(() => {
    let alive = true;
    setSections(null);
    setErr(null);
    getPage(page.endpoint, params)
      .then((d) => alive && setSections((d.sections as Section[]) ?? []))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (err) {
    return (
      <p className="app__muted">
        Couldn't load <code>{page.endpoint}</code>: {err}. Is the API running?
      </p>
    );
  }
  if (!sections) {
    return <p className="app__muted">Loading {page.endpoint}…</p>;
  }
  return (
    <div className="panel">
      {sections.map((s, i) => (
        <SectionView key={`${s.type}:${s.title}:${i}`} s={s} stackPct={stackPct} reload={reload} />
      ))}
    </div>
  );
}
