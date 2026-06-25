// Bilingual help / user-guide modal.
//
// Driven entirely by ``lib/helpContent``: every visible string —
// title, body, language toggle labels, close button, version
// footer — comes from the translation map. Adding a third
// language is just appending a new key there; this component
// requires no changes.
//
// Behaviour:
//   * Default language detected from ``navigator.language`` once on
//     first open (English fallback).
//   * Backdrop click + Escape close the modal.
//   * RTL languages set ``dir="rtl"`` on the body so list bullets
//     and headings align correctly.
//   * Modal body is the only scrollable region; the title strip
//     stays pinned so the language toggle is always reachable.

import { useEffect, useRef, useState } from "react";

import {
  HELP_CONTENT, HELP_UI, type HelpBlock, type Lang,
  SUPPORTED_LANGS, defaultHelpLang,
} from "../lib/helpContent";


interface Props {
  open: boolean;
  onClose: () => void;
}


export default function HelpModal({ open, onClose }: Props) {
  const [lang, setLang] = useState<Lang>(defaultHelpLang);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Move focus into the modal when it opens (Escape works whether
  // the close button has focus or not, but a screen reader needs
  // the modal to OWN focus on appear).
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => closeBtnRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  // Escape closes from anywhere in the document while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const ui = HELP_UI[lang];
  const content = HELP_CONTENT[lang];

  return (
    <div
      className="help-modal-backdrop"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="help-modal"
        role="dialog"
        aria-modal="true"
        aria-label={ui.modalTitle}
        dir={content.rtl ? "rtl" : "ltr"}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="help-modal-head">
          <h2 className="help-modal-title">{content.title}</h2>
          <div className="help-modal-lang">
            {SUPPORTED_LANGS.map((code) => {
              const label = code === "en" ? ui.langEnglish : ui.langFarsi;
              const isActive = lang === code;
              return (
                <button
                  key={code}
                  type="button"
                  lang={code}
                  className={
                    "help-modal-lang-btn"
                    + (isActive ? " help-modal-lang-btn-active" : "")
                  }
                  onClick={() => setLang(code)}
                  aria-pressed={isActive}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="help-modal-close"
            onClick={onClose}
            aria-label={ui.closeLabel}
            title={ui.closeLabel}
          >
            ×
          </button>
        </header>

        <div className="help-modal-body">
          <p className="help-modal-intro">{content.intro}</p>
          {content.sections.map((section, i) => (
            <section key={`s${i}`} className="help-modal-section">
              <h3 className="help-modal-h3">{section.heading}</h3>
              {section.blocks.map((block, j) => (
                <HelpBlockView key={`b${i}-${j}`} block={block} />
              ))}
            </section>
          ))}
        </div>

        <footer className="help-modal-foot">
          <span className="help-modal-version">{content.footer}</span>
        </footer>
      </div>
    </div>
  );
}


function HelpBlockView({ block }: { block: HelpBlock }) {
  if (block.kind === "p") {
    return <p className="help-modal-p">{block.text}</p>;
  }
  // Both ul + ol render an intro paragraph + the list of items.
  // Numbered lists use the ``ol`` element so the browser supplies
  // language-appropriate numerals (Arabic-indic for fa-IR locales,
  // Latin for en).
  const items = (
    <>
      {block.intro && (
        <p className="help-modal-list-intro">{block.intro}</p>
      )}
      {block.kind === "ul" ? (
        <ul className="help-modal-ul">
          {block.items.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      ) : (
        <ol className="help-modal-ol">
          {block.items.map((t, i) => <li key={i}>{t}</li>)}
        </ol>
      )}
    </>
  );
  return <div className="help-modal-list-block">{items}</div>;
}
