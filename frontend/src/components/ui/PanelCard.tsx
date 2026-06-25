// Generic right-stack card shell. A header row (icon + title + optional
// kind chip + optional action slot) and a padded body. Used by Pieces
// Summary, Slab Inventory, Step-4 export bar, and any other right-stack
// surface that should match the V1 mockup.

import type { ReactNode } from "react";

interface Props {
  title: string;
  icon?: ReactNode;
  /** Right-aligned action(s) in the header — typically a button or
   *  a status chip. */
  headerAction?: ReactNode;
  /** Card body. Inherits the card's padding; pass children with a
   *  spaced layout (flex/grid) so siblings get vertical rhythm. */
  children?: ReactNode;
  /** Optional explicit className for callers that want to extend
   *  the base ``.panel-card`` styles (e.g. accent borders). */
  className?: string;
  /** When true the body wrapper is omitted — useful for cards that
   *  render their own list region (e.g. PiecesPanel which manages
   *  its own scroll area). */
  bare?: boolean;
}

export default function PanelCard({
  title, icon, headerAction, children, className = "", bare = false,
}: Props) {
  return (
    <section className={`panel-card ${className}`}>
      <header className="panel-card-head">
        {icon && (
          <span className="panel-card-head-icon" aria-hidden="true">
            {icon}
          </span>
        )}
        <span className="panel-card-title">{title}</span>
        {headerAction && (
          <span className="panel-card-head-action">{headerAction}</span>
        )}
      </header>
      {bare ? children : <div className="panel-card-body">{children}</div>}
    </section>
  );
}
