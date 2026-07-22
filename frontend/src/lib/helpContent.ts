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
  title: "Avandad — Layout Helper Workflow Guide",
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
      heading: "Slab Calibration",
      blocks: [
        {
          kind: "p",
          text:
            "Every slab photo goes through automatic calibration "
            + "before it can be assigned to a piece. Calibration "
            + "finds the slab's real edges in the photo and "
            + "produces a corrected, straightened image sized to "
            + "the slab's usable area.",
        },
        {
          kind: "ul",
          intro: "Supported photo types:",
          items: [
            "Existing green boundary — a bright green rectangle "
              + "marks the slab in the photo. Approved automatically.",
            "Already scanned / cropped — the photo is already a "
              + "clean, cropped shot of just the slab. Approved "
              + "automatically.",
            "Raw photograph — an uncropped photo of the slab. The "
              + "system detects the four corners automatically; "
              + "high-confidence detections are approved "
              + "automatically, low-confidence ones need a quick "
              + "manual check.",
          ],
        },
        {
          kind: "p",
          text:
            "The Excel width and height are always the physical, "
            + "real-world slab size — that number never changes "
            + "and is kept for traceability. The system "
            + "automatically removes 20 mm from every side (40 mm "
            + "off each dimension) to get the usable size — the "
            + "area that's actually safe to cut pieces from. This "
            + "20 mm/side deduction happens once, automatically, "
            + "during calibration; you never need to account for "
            + "it yourself anywhere else.",
        },
        {
          kind: "p",
          text:
            "\"Usable\" dimensions are what Layout Helper, the fit "
            + "checker, and the factory DXF all plan against. "
            + "\"Excel\" (physical) dimensions are shown alongside "
            + "for traceability and appear on the factory DXF "
            + "labels, but never drive the cutting geometry.",
        },
        {
          kind: "ul",
          intro: "A slab needs manual review when:",
          items: [
            "the detector's confidence is low,",
            "the photo's proportions don't closely match the "
              + "Excel width/height,",
            "the slab's edge looks irregular, broken, or "
              + "non-rectangular,",
            "no clear outline could be detected at all.",
          ],
        },
        {
          kind: "p",
          text:
            "Needs Review is not a rejection — open the slab, "
            + "check or adjust the four corners, and approve or "
            + "reject it yourself. A slab is only ever rejected "
            + "automatically for a hard mismatch; borderline cases "
            + "always wait for a person.",
        },
        {
          kind: "p",
          text:
            "A slab uploaded with no photo is blocked (\"Missing "
            + "Photo\") until you add one — Layout Helper can't "
            + "confirm a slab's real shape without a photo, so it "
            + "can't be assigned or exported. Step 4 stays locked "
            + "until every slab in the project is either Approved "
            + "or Rejected — only Approved slabs can be assigned "
            + "to a piece.",
        },
        {
          kind: "p",
          text:
            "When more than one piece is cut from the same slab, "
            + "the factory plan leaves exactly 5 mm of spacing "
            + "between neighbouring cuts — enough room for the "
            + "blade, already built into the layout.",
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
      heading: "کالیبراسیون اسلب",
      blocks: [
        {
          kind: "p",
          text:
            "هر عکس اسلب پیش از تخصیص به قطعه، به‌صورت خودکار "
            + "کالیبره می‌شود. کالیبراسیون لبه‌های واقعی اسلب را در "
            + "عکس تشخیص می‌دهد و یک تصویر اصلاح‌شده و صاف، متناسب "
            + "با ابعاد قابل استفادهٔ اسلب، تولید می‌کند.",
        },
        {
          kind: "ul",
          intro: "انواع عکس پشتیبانی‌شده:",
          items: [
            "کادر سبز موجود — یک مستطیل سبز روشن اسلب را در عکس "
              + "مشخص می‌کند. به‌صورت خودکار تأیید می‌شود.",
            "اسکن‌شده یا برش‌خورده — عکس از قبل فقط شامل اسلب و "
              + "بدون حاشیهٔ اضافه است. به‌صورت خودکار تأیید می‌شود.",
            "عکس خام — عکسی بدون برش از اسلب. سیستم چهار گوشهٔ "
              + "اسلب را به‌صورت خودکار تشخیص می‌دهد؛ تشخیص‌های با "
              + "اطمینان بالا خودکار تأیید می‌شوند و موارد با "
              + "اطمینان پایین نیاز به بررسی دستی سریع دارند.",
          ],
        },
        {
          kind: "p",
          text:
            "عرض و طول اکسل همیشه ابعاد فیزیکی و واقعی اسلب هستند "
            + "— این عدد هرگز تغییر نمی‌کند و برای ردیابی نگه‌داشته "
            + "می‌شود. سیستم به‌صورت خودکار ۲۰ میلی‌متر از هر طرف "
            + "(۴۰ میلی‌متر از هر بُعد) کم می‌کند تا ابعاد قابل "
            + "استفاده به‌دست آید — ناحیه‌ای که برش قطعات از آن "
            + "واقعاً ایمن است. این کسر ۲۰ میلی‌متری از هر طرف فقط "
            + "یک‌بار و به‌صورت خودکار، در زمان کالیبراسیون انجام "
            + "می‌شود؛ نیازی نیست خودتان آن را جای دیگری دوباره "
            + "محاسبه کنید.",
        },
        {
          kind: "p",
          text:
            "ابعاد «قابل استفاده» همان چیزی است که چیدمان، بررسی "
            + "تناسب و فایل DXF کارخانه بر اساس آن برنامه‌ریزی "
            + "می‌کنند. ابعاد «اکسل» (فیزیکی) نیز برای ردیابی در "
            + "کنار آن نمایش داده می‌شود و روی برچسب‌های DXF کارخانه "
            + "دیده می‌شود، اما هرگز هندسهٔ برش را تعیین نمی‌کند.",
        },
        {
          kind: "ul",
          intro: "بررسی دستی اسلب در این موارد لازم است:",
          items: [
            "اطمینان تشخیص پایین باشد،",
            "نسبت ابعاد عکس با عرض/طول اکسل همخوانی نزدیکی نداشته باشد،",
            "لبهٔ اسلب نامنظم، شکسته یا غیرمستطیلی به نظر برسد،",
            "هیچ خط مرزی مشخصی اصلاً قابل تشخیص نباشد.",
          ],
        },
        {
          kind: "p",
          text:
            "«نیاز به بررسی» به معنای رد شدن نیست — اسلب را باز "
            + "کنید، چهار گوشه را بررسی یا تنظیم کنید و خودتان آن "
            + "را تأیید یا رد کنید. سیستم فقط در ناهماهنگی‌های شدید "
            + "به‌صورت خودکار اسلب را رد می‌کند؛ موارد مرزی همیشه "
            + "منتظر بررسی انسانی می‌مانند.",
        },
        {
          kind: "p",
          text:
            "اسلبی که بدون عکس بارگذاری شده باشد («بدون عکس») "
            + "مسدود می‌ماند تا زمانی که عکسی برای آن اضافه کنید — "
            + "چیدمان بدون عکس نمی‌تواند شکل واقعی اسلب را تأیید "
            + "کند، بنابراین قابل تخصیص یا خروجی گرفتن نیست. مرحلهٔ "
            + "۴ تا زمانی که همهٔ اسلب‌های پروژه یا تأیید یا رد شده "
            + "باشند قفل می‌ماند — فقط اسلب‌های تأییدشده قابل "
            + "تخصیص به قطعات هستند.",
        },
        {
          kind: "p",
          text:
            "وقتی بیش از یک قطعه از یک اسلب برش می‌خورد، نقشهٔ "
            + "کارخانه دقیقاً ۵ میلی‌متر فاصله بین برش‌های مجاور در "
            + "نظر می‌گیرد — فضای کافی برای تیغهٔ برش که از قبل در "
            + "چیدمان لحاظ شده است.",
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
