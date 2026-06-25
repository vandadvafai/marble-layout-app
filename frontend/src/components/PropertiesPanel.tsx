// Context-sensitive editor for the currently-selected plan object.
//
// The panel renders one of three forms (doorway / column / guide
// line) based on the ``selection.kind``. Edits flow through the
// patch callbacks the parent supplies — App.tsx then debounces a
// re-validate.
//
// Foundation scope: numeric inputs for endpoints/corners, a
// checkbox for ``is_main_entrance``, a "Delete" button. Drag
// handles on the canvas are out of scope for this milestone; the
// numeric inputs cover move + resize + delete.

import type {
  Column, Doorway, GuideLine, Plan, Point, Seam, Selection,
} from "../lib/types";
import {
  findColumn, findDoorway, findGuideLine, polygonBbox, rectanglePolygon,
} from "../lib/planEdits";

// 0.1.36: piece editing moved out of PropertiesPanel into the
// PiecesPanel (right-panel default surface). PropertiesPanel now
// only renders an editor when one of the four architectural object
// kinds is selected; a "piece" or null selection delegates to the
// pieces list higher up in App.tsx.
interface Props {
  seams: Seam[];
  plan: Plan;
  selection: Selection | null;
  onPatchDoorway: (doorway_id: string, patch: Partial<Doorway>) => void;
  onPatchColumn: (column_id: string, patch: Partial<Column>) => void;
  onPatchGuideLine: (guide_line_id: string, patch: Partial<GuideLine>) => void;
  /** Move a seam to a specific position (mm). The handler snaps and
   *  clamps before applying; the result must update both pieces
   *  and selection (the seam_id changes when the position does). */
  onPatchSeamPosition: (seam_id: string, newPosition: number) => void;
  onDelete: (selection: Selection) => void;
}

export default function PropertiesPanel({
  seams, plan, selection,
  onPatchDoorway, onPatchColumn, onPatchGuideLine,
  onPatchSeamPosition, onDelete,
}: Props) {
  // PropertiesPanel only renders when an architectural object is
  // selected. For null or "piece" selections the caller (App)
  // shows the PiecesPanel instead. Returning null here means the
  // App component's conditional doesn't need a second guard.
  if (selection === null || selection.kind === "piece") return null;

  if (selection.kind === "seam") {
    const seam = seams.find((s) => s.seam_id === selection.id);
    if (!seam) return <MissingPanel kind="seam" id={selection.id} />;
    return (
      <SeamForm
        seam={seam}
        onPatchPosition={(pos) =>
          onPatchSeamPosition(seam.seam_id, pos)
        }
      />
    );
  }

  if (selection.kind === "doorway") {
    const d = findDoorway(plan, selection.id);
    if (!d) return <MissingPanel kind="doorway" id={selection.id} />;
    return (
      <DoorwayForm
        doorway={d}
        onPatch={(patch) => onPatchDoorway(d.doorway_id, patch)}
        onDelete={() => onDelete(selection)}
      />
    );
  }

  if (selection.kind === "column") {
    const c = findColumn(plan, selection.id);
    if (!c) return <MissingPanel kind="column" id={selection.id} />;
    return (
      <ColumnForm
        column={c}
        onPatch={(patch) => onPatchColumn(c.column_id, patch)}
        onDelete={() => onDelete(selection)}
      />
    );
  }

  // guide_line
  const g = findGuideLine(plan, selection.id);
  if (!g) return <MissingPanel kind="guide line" id={selection.id} />;
  return (
    <GuideLineForm
      guide={g}
      onPatch={(patch) => onPatchGuideLine(g.guide_line_id, patch)}
      onDelete={() => onDelete(selection)}
    />
  );
}

// ---------------------------------------------------------------------------
// per-kind forms
// ---------------------------------------------------------------------------

function DoorwayForm({
  doorway, onPatch, onDelete,
}: {
  doorway: Doorway;
  onPatch: (patch: Partial<Doorway>) => void;
  onDelete: () => void;
}) {
  const [a, b] = doorway.segment;
  return (
    <section className="properties-panel">
      <header className="pp-header">
        Doorway <code className="pp-id">{doorway.doorway_id}</code>
      </header>
      <div className="pp-form">
        <PointRow
          label="Endpoint A (mm)"
          point={a}
          onChange={(p) => onPatch({ segment: [p, b] })}
        />
        <PointRow
          label="Endpoint B (mm)"
          point={b}
          onChange={(p) => onPatch({ segment: [a, p] })}
        />
        <NumberRow
          label="Width (mm)"
          value={doorway.width_mm}
          step={50}
          onChange={(v) => onPatch({ width_mm: v })}
        />
        <CheckboxRow
          label="Main entrance"
          checked={doorway.is_main_entrance}
          onChange={(v) => onPatch({ is_main_entrance: v })}
        />
        <DeleteRow onDelete={onDelete} />
      </div>
    </section>
  );
}

function ColumnForm({
  column, onPatch, onDelete,
}: {
  column: Column;
  onPatch: (patch: Partial<Column>) => void;
  onDelete: () => void;
}) {
  // For V1 we expose the column as an axis-aligned rectangle even
  // if the underlying polygon has more corners — keeps the form
  // legible. The patch always overwrites with a fresh 4-corner
  // rectangle, so previous non-rectangular shape data is lost.
  const [x0, y0, x1, y1] = polygonBbox(column.polygon);
  const setBbox = (x0p: number, y0p: number, x1p: number, y1p: number) => {
    const a: Point = [x0p, y0p];
    const b: Point = [x1p, y1p];
    onPatch({ polygon: rectanglePolygon(a, b) });
  };
  return (
    <section className="properties-panel">
      <header className="pp-header">
        Column <code className="pp-id">{column.column_id}</code>
      </header>
      <div className="pp-form">
        <NumberRow
          label="x min (mm)"
          value={x0}
          step={50}
          onChange={(v) => setBbox(v, y0, x1, y1)}
        />
        <NumberRow
          label="y min (mm)"
          value={y0}
          step={50}
          onChange={(v) => setBbox(x0, v, x1, y1)}
        />
        <NumberRow
          label="x max (mm)"
          value={x1}
          step={50}
          onChange={(v) => setBbox(x0, y0, v, y1)}
        />
        <NumberRow
          label="y max (mm)"
          value={y1}
          step={50}
          onChange={(v) => setBbox(x0, y0, x1, v)}
        />
        <DeleteRow onDelete={onDelete} />
      </div>
    </section>
  );
}

function GuideLineForm({
  guide, onPatch, onDelete,
}: {
  guide: GuideLine;
  onPatch: (patch: Partial<GuideLine>) => void;
  onDelete: () => void;
}) {
  const [a, b] = guide.segment;
  return (
    <section className="properties-panel">
      <header className="pp-header">
        Guide line <code className="pp-id">{guide.guide_line_id}</code>
      </header>
      <div className="pp-form">
        <PointRow
          label="Endpoint A (mm)"
          point={a}
          onChange={(p) => onPatch({ segment: [p, b] })}
        />
        <PointRow
          label="Endpoint B (mm)"
          point={b}
          onChange={(p) => onPatch({ segment: [a, p] })}
        />
        <NumberRow
          label="Priority"
          value={guide.priority}
          step={1}
          onChange={(v) => onPatch({ priority: Math.round(v) })}
        />
        <TextRow
          label="Name"
          value={guide.name}
          onChange={(v) => onPatch({ name: v })}
        />
        <DeleteRow onDelete={onDelete} />
      </div>
    </section>
  );
}

function SeamForm({
  seam, onPatchPosition,
}: {
  seam: Seam;
  onPatchPosition: (newPosition: number) => void;
}) {
  const axisLabel = seam.orientation === "vertical" ? "x" : "y";
  // Affected piece counts derived directly from the seam — they
  // can't drift from the canvas because the same deriveSeams output
  // feeds both.
  const before = seam.piece_left_ids;
  const after = seam.piece_right_ids;
  const beforeLabel = seam.orientation === "vertical" ? "Left" : "Below";
  const afterLabel = seam.orientation === "vertical" ? "Right" : "Above";
  return (
    <section className="properties-panel">
      <header className="pp-header">
        Seam <code className="pp-id">{seam.seam_id}</code>
      </header>
      <div className="pp-form">
        <div className="pp-row pp-row-static">
          <label>Orientation</label>
          <span>{seam.orientation}</span>
        </div>
        <div className="pp-row pp-row-static">
          <label>Zone</label>
          <span><code>{seam.zone_id}</code></span>
        </div>
        <NumberRow
          label={`Position ${axisLabel} (mm)`}
          value={seam.position}
          step={50}
          onChange={(v) => onPatchPosition(v)}
        />
        <div className="pp-row pp-row-static">
          <label>Drag bounds</label>
          <span className="pp-mono">
            [{seam.min_position}, {seam.max_position}] mm
          </span>
        </div>
        <PiecesList label={`${beforeLabel} pieces`} ids={before} />
        <PiecesList label={`${afterLabel} pieces`} ids={after} />
      </div>
    </section>
  );
}

function PiecesList({ label, ids }: { label: string; ids: string[] }) {
  return (
    <div className="pp-row pp-row-static">
      <label>{label}</label>
      <span
        className="pp-affected"
        title={ids.join(", ")}
      >
        {ids.length === 0 ? "none" : (
          <>
            <strong>{ids.length}</strong>{" "}
            <code>{ids.slice(0, 2).join(", ")}</code>
            {ids.length > 2 && "…"}
          </>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// row primitives — kept inline so the panel is one self-contained file
// ---------------------------------------------------------------------------

function PointRow({
  label, point, onChange,
}: {
  label: string; point: Point; onChange: (p: Point) => void;
}) {
  const [x, y] = point;
  return (
    <div className="pp-row">
      <label>{label}</label>
      <div className="pp-row-pair">
        <input
          type="number"
          step={50}
          value={x}
          onChange={(e) => onChange([Number(e.target.value), y])}
        />
        <input
          type="number"
          step={50}
          value={y}
          onChange={(e) => onChange([x, Number(e.target.value)])}
        />
      </div>
    </div>
  );
}

function NumberRow({
  label, value, step, onChange,
}: {
  label: string; value: number; step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="pp-row">
      <label>{label}</label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function CheckboxRow({
  label, checked, onChange,
}: {
  label: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="pp-row pp-row-checkbox">
      <label>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />{" "}
        {label}
      </label>
    </div>
  );
}

function TextRow({
  label, value, onChange,
}: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="pp-row">
      <label>{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function DeleteRow({ onDelete }: { onDelete: () => void }) {
  return (
    <div className="pp-row pp-row-delete">
      <button
        type="button"
        className="pp-delete-btn"
        onClick={onDelete}
      >
        Delete
      </button>
    </div>
  );
}

function MissingPanel({ kind, id }: { kind: string; id: string }) {
  return (
    <section className="properties-panel">
      <header className="pp-header">Properties</header>
      <div className="pp-empty">
        Selected {kind} <code>{id}</code> no longer exists in the
        plan. Pick another object.
      </div>
    </section>
  );
}
