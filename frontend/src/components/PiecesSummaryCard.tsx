// Compact "Pieces Summary" card that matches the V1 mockup. Sits
// above the full PiecesPanel in the right stack so the designer
// gets a top-of-panel overview without scrolling through every
// risk row. Reuses the colour palette from styles.css (V1 polish
// section) via .av-pill and the legend dot classes here.

import { memo, useMemo } from "react";

import PanelCard from "./ui/PanelCard";
import StatusPill from "./ui/StatusPill";
import { sortPiecesByRisk } from "../lib/pieceRisk";
import type {
  InventoryMatchResponse, Layout, Piece, ValidationResult,
} from "../lib/types";


interface Props {
  pieces: Piece[];
  layout: Layout;
  validation: ValidationResult | null;
  inventoryMatch: InventoryMatchResponse | null;
}


function PiecesSummaryCardImpl({
  pieces, layout, validation, inventoryMatch,
}: Props) {
  const sorted = useMemo(
    () => sortPiecesByRisk(pieces, validation, layout, inventoryMatch),
    [pieces, validation, layout, inventoryMatch],
  );
  const counts = useMemo(() => {
    let critical = 0, high = 0, medium = 0, ok = 0;
    for (const { risk } of sorted) {
      if (risk.level === "critical") critical += 1;
      else if (risk.level === "high") high += 1;
      else if (risk.level === "medium") medium += 1;
      else ok += 1;
    }
    const noSlab = inventoryMatch?.summary.no_match ?? 0;
    return { critical, high, medium, ok, noSlab };
  }, [sorted, inventoryMatch]);

  const total = pieces.length;
  const inventoryCount = inventoryMatch?.inventory_count;

  return (
    <PanelCard
      title="Pieces Summary"
      icon={
        <svg viewBox="0 0 16 16" width="16" height="16">
          <rect
            x="2.5" y="2.5" width="5" height="5" rx="1"
            fill="none" stroke="currentColor" strokeWidth="1.4"
          />
          <rect
            x="8.5" y="2.5" width="5" height="5" rx="1"
            fill="none" stroke="currentColor" strokeWidth="1.4"
          />
          <rect
            x="2.5" y="8.5" width="5" height="5" rx="1"
            fill="none" stroke="currentColor" strokeWidth="1.4"
          />
          <rect
            x="8.5" y="8.5" width="5" height="5" rx="1"
            fill="none" stroke="currentColor" strokeWidth="1.4"
          />
        </svg>
      }
    >
      <div className="pcs-summary-grid">
        <div className="pcs-summary-text">
          <div className="pcs-summary-meta">
            {total} piece{total === 1 ? "" : "s"} · sorted by risk
            {inventoryCount !== undefined && (
              <> · {inventoryCount} slabs in inventory</>
            )}
          </div>
          <div className="pcs-summary-pills">
            {counts.critical > 0 && (
              <StatusPill tone="red">
                {counts.critical} critical
              </StatusPill>
            )}
            {counts.noSlab > 0 && (
              <StatusPill tone="amber">{counts.noSlab} no slab</StatusPill>
            )}
            {counts.high > 0 && (
              <StatusPill tone="amber">{counts.high} high</StatusPill>
            )}
            {counts.medium > 0 && (
              <StatusPill tone="blue">{counts.medium} medium</StatusPill>
            )}
          </div>
          <div className="pcs-legend">
            <LegendDot tone="red" label="Critical" count={counts.critical} />
            <LegendDot tone="amber" label="High risk" count={counts.high} />
            <LegendDot tone="amber" label="No slab" count={counts.noSlab} />
            <LegendDot tone="green" label="OK" count={counts.ok} />
          </div>
        </div>
        <RiskDonut
          total={total}
          critical={counts.critical}
          high={counts.high}
          medium={counts.medium}
          ok={counts.ok}
        />
      </div>
    </PanelCard>
  );
}

const PiecesSummaryCard = memo(PiecesSummaryCardImpl);
export default PiecesSummaryCard;


function LegendDot({
  tone, label, count,
}: { tone: "red" | "amber" | "blue" | "green"; label: string; count: number }) {
  return (
    <div className="pcs-legend-row">
      <span className={`pcs-legend-dot pcs-legend-dot-${tone}`} />
      <span className="pcs-legend-label">{label}</span>
      <span className="pcs-legend-count">{count}</span>
    </div>
  );
}


function RiskDonut({
  total, critical, high, medium, ok,
}: { total: number; critical: number; high: number; medium: number; ok: number }) {
  // SVG donut — circumference based on a 36 radius circle. We
  // render arcs in stacked order: critical → high → medium → ok.
  const r = 36;
  const c = 2 * Math.PI * r;
  const segments: { dash: number; tone: "red" | "amber" | "blue" | "green" }[] = [];
  if (total > 0) {
    const push = (n: number, tone: typeof segments[number]["tone"]) => {
      if (n <= 0) return;
      segments.push({ dash: (n / total) * c, tone });
    };
    push(critical, "red");
    push(high, "amber");
    push(medium, "blue");
    push(ok, "green");
  }
  let offset = 0;
  return (
    <div className="pcs-donut">
      <svg viewBox="0 0 100 100" width="92" height="92" role="presentation">
        <circle
          cx="50" cy="50" r={r}
          stroke="#f1f3f5" strokeWidth="14" fill="none"
        />
        {segments.map((seg, i) => {
          const color = {
            red: "#dc2626", amber: "#d97706",
            blue: "#2563eb", green: "#16a34a",
          }[seg.tone];
          const arc = (
            <circle
              key={i}
              cx="50" cy="50" r={r}
              stroke={color}
              strokeWidth="14"
              fill="none"
              strokeDasharray={`${seg.dash} ${c - seg.dash}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 50 50)"
            />
          );
          offset += seg.dash;
          return arc;
        })}
        <text
          x="50" y="50"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="22"
          fontWeight="700"
          fill="#111827"
        >
          {total}
        </text>
        <text
          x="50" y="64"
          textAnchor="middle"
          fontSize="10"
          fill="#6b7280"
        >
          Pieces
        </text>
      </svg>
    </div>
  );
}
