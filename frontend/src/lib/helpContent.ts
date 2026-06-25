// Bilingual help / user-guide content (0.1.51).
//
// Two principles drove the shape of this module:
//
//   1. No hardcoded strings in JSX. Every piece of copy — including
//      UI labels, the close button's aria-label, and the version
//      footer — lives in this file. That's what makes adding a
//      third language a one-block diff in the future.
//
//   2. Content blocks are structured, not raw markdown. The modal
//      renders a small set of block kinds (``p`` / ``ul`` / ``ol``);
//      we don't ship a markdown renderer just to support six
//      slightly different headings. Keeps the modal component
//      tiny and predictable.

export type Lang = "en" | "fa";

export const SUPPORTED_LANGS: readonly Lang[] = ["en", "fa"] as const;


/** Block-level content the modal can render. Add more kinds here
 *  (e.g. ``code`` blocks, ``img``) when content needs it. */
export type HelpBlock =
  | { kind: "p"; text: string }
  | { kind: "ul"; intro?: string; items: string[] }
  | { kind: "ol"; intro?: string; items: string[] };


export interface HelpSection {
  heading: string;
  blocks: HelpBlock[];
}


export interface HelpContent {
  /** When true the modal body is rendered with ``dir="rtl"``.
   *  Drives text alignment + bullet placement for languages that
   *  read right-to-left. */
  rtl: boolean;
  title: string;
  intro: string;
  sections: HelpSection[];
  /** Visible at the bottom of the modal — "User Guide v1.0" in the
   *  current language. */
  footer: string;
}


export interface HelpUiStrings {
  /** Label of the header button that opens the modal. */
  helpButton: string;
  /** Modal aria-label / title slot. */
  modalTitle: string;
  /** Close-button label / aria-label. */
  closeLabel: string;
  /** Toggle labels — one per supported language. */
  langEnglish: string;
  langFarsi: string;
}


// ---------------------------------------------------------------------------
// UI strings — kept separate from CONTENT so the modal can render its
// chrome (close button, language toggle) in the current language
// without having to thread the language through every block.
// ---------------------------------------------------------------------------

export const HELP_UI: Record<Lang, HelpUiStrings> = {
  en: {
    helpButton: "Help",
    modalTitle: "User Guide",
    closeLabel: "Close",
    langEnglish: "English",
    langFarsi: "فارسی",
  },
  fa: {
    helpButton: "راهنما",
    modalTitle: "راهنمای کاربر",
    closeLabel: "بستن",
    langEnglish: "English",
    langFarsi: "فارسی",
  },
};


// ---------------------------------------------------------------------------
// English guide
// ---------------------------------------------------------------------------

const EN: HelpContent = {
  rtl: false,
  title: "Stone Layout Workflow Guide",
  intro:
    "This application helps designers and factory teams create stone "
    + "layouts, assign slabs, and export production files.",
  sections: [
    {
      heading: "Step 1 — Upload Plan",
      blocks: [
        {
          kind: "ul",
          intro: "Upload:",
          items: ["DXF floor plan (preferred)", "or", "Sample layout"],
        },
        { kind: "p", text: "The plan should represent the final floor geometry." },
        {
          kind: "ul",
          intro: "After upload:",
          items: [
            "Floor boundaries are detected.",
            "Rooms and shapes are imported.",
          ],
        },
      ],
    },
    {
      heading: "Step 2 — Edit & Validate",
      blocks: [
        { kind: "p", text: "Review the generated layout." },
        {
          kind: "ul",
          intro: "You can:",
          items: ["Add seams", "Move seams", "Remove seams", "Validate piece sizes"],
        },
        {
          kind: "ul",
          intro: "Check:",
          items: ["Door openings", "Columns", "Narrow pieces", "Layout direction"],
        },
        { kind: "p", text: "Only continue when the layout reflects the intended design." },
      ],
    },
    {
      heading: "Step 3 — Upload Slabs",
      blocks: [
        {
          kind: "ol",
          intro: "Upload:",
          items: ["Inventory Excel file", "Slab photos"],
        },
        {
          kind: "ul",
          intro: "Excel should contain:",
          items: ["Item code", "Serial number", "Width", "Height"],
        },
        {
          kind: "ul",
          intro: "Photos should:",
          items: [
            "Be photographed from above",
            "Have consistent lighting",
            "Show the full slab",
          ],
        },
        {
          kind: "p",
          text:
            "The system detects the usable stone area and prepares slabs for matching.",
        },
      ],
    },
    {
      heading: "Step 4 — Assign Slabs",
      blocks: [
        { kind: "p", text: 'Use "Auto Assign Best Slabs".' },
        {
          kind: "ul",
          intro: "The system matches slabs based on:",
          items: ["Dimensions", "Fit", "Waste", "Rotation requirements"],
        },
        { kind: "p", text: "Review every assignment." },
        {
          kind: "ul",
          intro: "For each piece you can see:",
          items: [
            "Piece size",
            "Assigned slab",
            "Original slab size",
            "Final cut size",
            "Waste percentage",
          ],
        },
        { kind: "p", text: "Green indicators mean a valid match." },
      ],
    },
    {
      heading: "Export Client PNG",
      blocks: [
        { kind: "p", text: "Creates a presentation image for clients." },
        {
          kind: "ul",
          intro: "Includes:",
          items: [
            "Floor layout",
            "Assigned stone surfaces",
            "Seams",
            "Overall design",
          ],
        },
        { kind: "p", text: "Use this for approvals and presentations." },
      ],
    },
    {
      heading: "Export Factory DXF",
      blocks: [
        { kind: "p", text: "Creates a production drawing for fabrication." },
        {
          kind: "ul",
          intro: "Includes:",
          items: [
            "Cut geometry",
            "Piece identifiers",
            "Slab references",
            "Factory information",
          ],
        },
        { kind: "p", text: "Use this file for cutting and manufacturing." },
      ],
    },
    {
      heading: "Best Practices",
      blocks: [
        {
          kind: "ul",
          items: [
            "Use high-quality slab photos.",
            "Verify dimensions before assignment.",
            "Minimize unnecessary waste.",
            "Review edge pieces carefully.",
            "Confirm all assignments before exporting.",
          ],
        },
      ],
    },
  ],
  footer: "User Guide v1.0",
};


// ---------------------------------------------------------------------------
// Farsi guide. Numerals use Eastern Arabic forms (۱..۹) as in the
// spec; left as plain string content so the renderer doesn't have to
// transliterate.
// ---------------------------------------------------------------------------

const FA: HelpContent = {
  rtl: true,
  title: "راهنمای استفاده از سیستم چیدمان سنگ",
  intro:
    "این نرم‌افزار برای طراحی چیدمان سنگ، انتخاب اسلب‌ها و تهیه "
    + "فایل‌های تولید کارخانه استفاده می‌شود.",
  sections: [
    {
      heading: "مرحله ۱ — بارگذاری نقشه",
      blocks: [
        {
          kind: "ul",
          intro: "بارگذاری:",
          items: ["فایل DXF", "یا", "نمونه طرح"],
        },
        { kind: "p", text: "نقشه باید هندسه نهایی کف را نشان دهد." },
        {
          kind: "ul",
          intro: "پس از بارگذاری:",
          items: [
            "مرزهای فضا شناسایی می‌شوند.",
            "شکل کلی پروژه وارد سیستم می‌شود.",
          ],
        },
      ],
    },
    {
      heading: "مرحله ۲ — ویرایش و بررسی",
      blocks: [
        { kind: "p", text: "طرح تولید شده را بررسی کنید." },
        {
          kind: "ul",
          intro: "امکانات:",
          items: ["افزودن درز", "جابه‌جایی درز", "حذف درز", "بررسی ابعاد قطعات"],
        },
        {
          kind: "ul",
          intro: "موارد مهم:",
          items: ["محل درها", "ستون‌ها", "قطعات باریک", "جهت چیدمان"],
        },
        { kind: "p", text: "پس از تأیید ادامه دهید." },
      ],
    },
    {
      heading: "مرحله ۳ — بارگذاری اسلب‌ها",
      blocks: [
        {
          kind: "ol",
          intro: "بارگذاری:",
          items: ["فایل اکسل موجودی", "تصاویر اسلب‌ها"],
        },
        {
          kind: "ul",
          intro: "اکسل باید شامل موارد زیر باشد:",
          items: ["کد کالا", "شماره سریال", "عرض", "طول"],
        },
        {
          kind: "ul",
          intro: "تصاویر باید:",
          items: [
            "از بالا گرفته شده باشند",
            "نور یکنواخت داشته باشند",
            "کل اسلب را نمایش دهند",
          ],
        },
        {
          kind: "p",
          text: "سیستم ناحیه قابل استفاده سنگ را تشخیص می‌دهد.",
        },
      ],
    },
    {
      heading: "مرحله ۴ — تخصیص اسلب",
      blocks: [
        { kind: "p", text: "از گزینه «Auto Assign Best Slabs» استفاده کنید." },
        {
          kind: "ul",
          intro: "سیستم بر اساس موارد زیر اسلب مناسب را انتخاب می‌کند:",
          items: ["ابعاد", "میزان تطابق", "پرت مصالح", "نیاز به چرخش"],
        },
        { kind: "p", text: "هر قطعه را بررسی کنید." },
        {
          kind: "ul",
          intro: "اطلاعات نمایش داده شده:",
          items: [
            "ابعاد قطعه",
            "اسلب انتخاب شده",
            "ابعاد اصلی اسلب",
            "ابعاد برش نهایی",
            "درصد پرت",
          ],
        },
      ],
    },
    {
      heading: "خروجی PNG مشتری",
      blocks: [
        { kind: "p", text: "تصویری برای ارائه به مشتری ایجاد می‌کند." },
        {
          kind: "ul",
          intro: "شامل:",
          items: ["پلان", "سنگ‌های تخصیص یافته", "درزها", "نمای نهایی طراحی"],
        },
      ],
    },
    {
      heading: "خروجی DXF کارخانه",
      blocks: [
        { kind: "p", text: "فایل تولیدی برای کارخانه ایجاد می‌کند." },
        {
          kind: "ul",
          intro: "شامل:",
          items: [
            "هندسه برش",
            "شناسه قطعات",
            "اطلاعات اسلب",
            "اطلاعات تولید",
          ],
        },
        { kind: "p", text: "برای برش و ساخت استفاده می‌شود." },
      ],
    },
    {
      heading: "توصیه‌ها",
      blocks: [
        {
          kind: "ul",
          items: [
            "از تصاویر باکیفیت استفاده کنید.",
            "ابعاد را قبل از تخصیص بررسی کنید.",
            "پرت مصالح را کاهش دهید.",
            "قطعات لبه‌ای را با دقت کنترل کنید.",
            "قبل از خروجی گرفتن تمام تخصیص‌ها را بررسی کنید.",
          ],
        },
      ],
    },
  ],
  footer: "راهنمای کاربر v1.0",
};


export const HELP_CONTENT: Record<Lang, HelpContent> = { en: EN, fa: FA };


/** Detect a sensible default language from the browser. Falls back
 *  to English when ``navigator.language`` doesn't match any
 *  supported language. Safe to call during SSR — guards
 *  ``typeof navigator``. */
export function defaultHelpLang(): Lang {
  if (typeof navigator === "undefined") return "en";
  const raw = (navigator.language || "en").toLowerCase();
  if (raw.startsWith("fa")) return "fa";
  return "en";
}
