// Status pill — colour-coded chip used across the V1 right panel
// (Piece Details, Pieces Summary, Slab Inventory). Centralises the
// colour map so the Step-4 redesign can reuse it without a second
// inventory of styles.

import type { ReactNode } from "react";

export type StatusTone =
  | "green"   // valid / ok
  | "amber"   // high waste / oversized / absorbed sliver
  | "red"     // critical / no slab / too small
  | "blue"    // edge / bookmatched / photo / informational
  | "grey"    // neutral / unassigned
  | "solid-blue"; // best match

interface Props {
  tone: StatusTone;
  children: ReactNode;
  title?: string;
}

export default function StatusPill({ tone, children, title }: Props) {
  return (
    <span className={`av-pill av-pill-${tone}`} title={title}>
      {children}
    </span>
  );
}
