#!/usr/bin/osascript -l JavaScript
// Symbols for Alfred: Unicode search and inspection, kaomoji, fancy text, LaTeX and typography.
// Usage: osascript -l JavaScript symbols.js <command> [query]
ObjC.import("Foundation");

const ENV = $.NSProcessInfo.processInfo.environment;
function env(name, fallback) {
  const v = ENV.objectForKey(name);
  return v.isNil() ? fallback : v.js;
}
// Workflow Configuration values may arrive padded, empty or as "1"/"0"
function conf(name, fallback) {
  const v = String(env(name, "")).trim();
  return v === "" ? fallback : v;
}
const TEST = env("SYM_TEST_MODE", "") === "1";
const FM = $.NSFileManager.defaultManager;

// Own-property lookup: user-controlled keys ("constructor", "__proto__") must never hit Object.prototype
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const lookup = (o, k) => (own(o, k) ? o[k] : undefined);

const MAX_RESULTS = Math.min(200, Math.max(10, parseInt(conf("max_results", "50"), 10) || 50));
const SHOW_RECENT = conf("show_recent", "1") !== "0";
const ICON_STYLE = conf("icon_style", "light") === "dark" ? "dark" : "light";
const ALT_FORMAT = lookup({ html: 1, hex: 1, js: 1, uplus: 1, python: 1, css: 1 }, conf("alt_format", "html")) ? conf("alt_format", "html") : "html";
const TEX_PRIMARY = conf("tex_primary", "command") === "character" ? "character" : "command";
const RENDER_PER_RUN = 10; // new glyph icons drawn per run; the rest follow on an automatic rerun
const MAX_INPUT = 20000; // characters inspected at most
const MAX_FANCY = 2000; // characters styled at most
const MAX_FIX = 500000; // characters the typography fixer takes
const LARGE = 50000; // longer results go through a cache file (see act.sh)

// ---------- text helpers ----------

// Lone UTF-16 surrogates break NSString and JSON consumers
const wellFormed = String.prototype.toWellFormed
  ? (s) => s.toWellFormed()
  : (s) => s.replace(/[\ud800-\udbff][\udc00-\udfff]|[\ud800-\udfff]/g, (m) => (m.length === 2 ? m : "\ufffd"));

// Display strings only: control characters and bidi overrides (which can disguise text) are removed
function displayText(s) {
  return String(s)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g, "")
    .replace(/[\t\n\r\u2028\u2029]/g, " ")
    .replace(/[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]/g, "");
}

function oneLine(s, max = 120) {
  const t = String(s).replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  let end = max - 1;
  if (/[\ud800-\udbff]/.test(t[end - 1])) end--; // don't split a surrogate pair
  return t.slice(0, end) + "…";
}

function plural(n, word) {
  return `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
}

const hex4 = (cp) => cp.toString(16).toUpperCase().padStart(4, "0");
const uplus = (cp) => `U+${hex4(cp)}`;
const cpsOf = (s) => Array.from(s, (c) => c.codePointAt(0));
const isSurrogate = (cp) => cp >= 0xd800 && cp <= 0xdfff;
const strOf = (cps) => cps.map((c) => (isSurrogate(c) ? "\ufffd" : String.fromCodePoint(c))).join("");

// Upper-case ASCII search tokens: accents removed ("Café" → CAFE)
function tokens(q) {
  return String(q)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .split(/[^A-Z0-9]+/)
    .filter(Boolean);
}

const KEEP_UPPER = new Set(["CJK", "IPA", "APL", "ASCII", "UTF", "OCR", "SOS", "ATM", "DVD", "WC", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"]);
// "RIGHT-POINTING ANGLE" → "Right-Pointing Angle"; single letters, hex and a few acronyms stay upper case
function titleCase(name) {
  return name.replace(/[A-Z0-9']+/g, (w) => {
    if (w.length === 1 || /\d/.test(w) || KEEP_UPPER.has(w)) return w;
    return w[0] + w.slice(1).toLowerCase();
  });
}

function htmlEscape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// FNV-1a: short stable file names for previews of arbitrary text
function hash(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0") + s.length.toString(16);
}

// ---------- files ----------

function trimSlash(p) {
  return p.replace(/\/+$/, "");
}
function safeDir(p) {
  return /^\/[^/]/.test(p) && !/(^|\/)\.\.?(\/|$)/.test(p);
}
function mkdirp(dir) {
  FM.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir, true, $(), $());
}
function cacheDir() {
  let dir = trimSlash(env("alfred_workflow_cache", ""));
  if (!safeDir(dir)) dir = `${trimSlash($.NSTemporaryDirectory().js)}/alfred-symbols`;
  mkdirp(dir);
  return dir;
}
function dataDir() {
  const dir = trimSlash(env("alfred_workflow_data", ""));
  if (!safeDir(dir)) return cacheDir();
  mkdirp(dir);
  return dir;
}
function exists(p) {
  return FM.fileExistsAtPath(p);
}
function readText(p) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, $());
  return s.isNil() ? null : s.js;
}
// Atomic: a parallel run never sees a half-written file
function writeText(p, text) {
  mkdirp(p.replace(/\/[^/]*$/, ""));
  return $(text).writeToFileAtomicallyEncodingError(p, true, $.NSUTF8StringEncoding, $());
}
function readJSON(p, fallback) {
  const t = readText(p);
  if (t === null) return fallback;
  try {
    return JSON.parse(t);
  } catch (e) {
    return fallback;
  }
}

const DATA = {};
function data(name) {
  if (!own(DATA, name)) {
    const t = readText(`./data/${name}`);
    if (t === null) throw new Error(`The data file ${name} is missing: reinstall the workflow`);
    DATA[name] = t;
  }
  return DATA[name];
}
function dataLines(name) {
  const key = `${name}#lines`;
  if (!own(DATA, key)) DATA[key] = data(name).split("\n").filter(Boolean).map((l) => l.split("\t"));
  return DATA[key];
}

let appKit = false;
function useAppKit() {
  if (!appKit) ObjC.import("AppKit");
  appKit = true;
}

function clipboard() {
  if (TEST) return env("SYM_TEST_CLIPBOARD", ""); // the tests never read the real pasteboard
  useAppKit();
  const s = $.NSPasteboard.generalPasteboard.stringForType($.NSPasteboardTypeString);
  return s.isNil() ? "" : s.js;
}

// ---------- favourites and recents ----------
// Keys: "u:2192" (code points, "-" between them), "k:<kaomoji>", "x:\alpha" (LaTeX command)

function favPath() {
  return `${dataDir()}/favourites.json`;
}
function favourites() {
  const v = readJSON(favPath(), []);
  return Array.isArray(v) ? v.filter((k) => typeof k === "string") : [];
}
function toggleFavourite(key) {
  if (!/^[ukx]:./.test(key) || key.length > 300 || /\n/.test(key)) return "Couldn’t update the favourites";
  const list = favourites();
  const i = list.indexOf(key);
  if (i >= 0) list.splice(i, 1);
  else list.unshift(key);
  writeText(favPath(), JSON.stringify(list));
  return i >= 0 ? `Removed ${describeKey(key)} from favourites` : `Added ${describeKey(key)} to favourites`;
}
function describeKey(key) {
  if (key.startsWith("u:")) return key.slice(2).split("-").map((h) => uplus(parseInt(h, 16))).join(" ");
  return key.slice(2);
}

// act.sh appends one key per line to recent.log; the newest are last
function recents(prefix, n = 12) {
  if (!SHOW_RECENT) return [];
  const p = `${dataDir()}/recent.log`;
  const t = readText(p);
  if (!t) return [];
  const lines = t.split("\n").filter(Boolean);
  const out = [];
  for (let i = lines.length - 1; i >= 0 && out.length < n; i--) {
    if (lines[i].startsWith(prefix) && !out.includes(lines[i])) out.push(lines[i]);
  }
  if (lines.length > 400) {
    // compact: keep the newest distinct keys of every kind
    const keep = [];
    for (let i = lines.length - 1; i >= 0 && keep.length < 150; i--) if (!keep.includes(lines[i])) keep.push(lines[i]);
    writeText(p, keep.reverse().join("\n") + "\n");
  }
  return out;
}

// ---------- Script Filter output ----------

function info(title, subtitle, icon = "info", extra = {}) {
  return Object.assign({ title, subtitle: subtitle || "", valid: false, icon: { path: `icons/${icon}.png` } }, extra);
}

function stableUids(items) {
  items.forEach((it, i) => {
    if (it && !it.uid) it.uid = `${i}|${String(it.title || "").replace(/[0-9]+/g, "#")}`;
  });
  return items;
}

function output(items, extra = {}) {
  stableUids(items);
  if (pendingIcons && !renderFailed) extra = Object.assign({ rerun: 0.2 }, extra);
  return JSON.stringify(Object.assign({ skipknowledge: true, items }, extra), (k, v) =>
    typeof v !== "string" ? v : k === "title" || k === "subtitle" ? wellFormed(displayText(v)) : wellFormed(v)
  );
}

const NO_MOD = { valid: false, arg: "", subtitle: "Not available for this row" };

// A plain copy/paste row (fancy text, formats, typography)
// Values over 50 KB are written to the cache and passed as "symfile:<path>", which act.sh reads back,
// so the Script Filter JSON stays small
function largeValue(v) {
  const dir = trimSlash(env("alfred_workflow_cache", ""));
  if (v.length <= LARGE || !safeDir(dir)) return null;
  const path = `${cacheDir()}/results/${hash(v)}.txt`;
  return writeText(path, v) ? `symfile:${path}` : null;
}

function textRow(title, value, subtitle, icon, extra = {}) {
  let v = String(value);
  const ok = v !== "" && !v.includes("\u0000");
  const copy = v.length > LARGE ? "Too long for ⌘C: press ↩ to copy it" : v;
  const largetype = v.length > 3000 ? v.slice(0, 3000) + "…" : v;
  v = largeValue(v) || v;
  return Object.assign(
    {
      title: oneLine(title),
      subtitle: subtitle || "↩ Copy · ⌘↩ Paste",
      arg: v,
      valid: ok,
      icon: typeof icon === "string" ? { path: `icons/${icon}.png` } : icon,
      text: { copy, largetype },
      // the favourite, format and show-all keys only apply to character rows
      mods: { cmd: { arg: v, valid: ok, subtitle: "Paste into the frontmost app" }, alt: NO_MOD, ctrl: NO_MOD, "cmd+alt": NO_MOD },
    },
    extra
  );
}

// ---------- glyph icons and previews ----------

const ICON_VERSION = 1; // bump when the drawing changes, so cached icons are redrawn
let rendered = 0;
let pendingIcons = false;
let renderFailed = false; // e.g. an unwritable cache: don't rerun forever

function color(rgb, a = 1) {
  return $.NSColor.colorWithSRGBRedGreenBlueAlpha(rgb[0], rgb[1], rgb[2], a);
}
function attributed(str, size, rgb) {
  const a = $.NSMutableAttributedString.new;
  a.mutableString.setString($(str));
  a.setAttributesRange($({ NSFont: $.NSFont.systemFontOfSize(size), NSColor: color(rgb) }), $.NSMakeRange(0, a.length));
  return a;
}

// Draws text centred on a rounded tile with NSAttributedString (font fallback included) and writes a PNG.
// label: a short name drawn instead of an invisible character (NBSP, ZWJ…), inside a dashed frame.
function drawGlyph(str, path, label) {
  useAppKit();
  const size = 96;
  const dark = ICON_STYLE === "dark";
  const fg = dark ? [0.95, 0.95, 0.96] : [0.11, 0.11, 0.13];
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
    null, size, size, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  if (rep.isNil()) return false;
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));
  const inset = size * 0.04;
  color(dark ? [0.16, 0.16, 0.18] : [0.96, 0.96, 0.97]).setFill;
  $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset), size * 0.18, size * 0.18).fill;
  let text = str, fs = size * 0.6;
  if (label) {
    const frame = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(size * 0.14, size * 0.3, size * 0.72, size * 0.4), 6, 6);
    frame.lineWidth = 2;
    frame.setLineDashCountPhase([4, 3], 2, 0);
    color(fg, 0.45).setStroke;
    frame.stroke;
    text = label;
    fs = size * 0.22;
  }
  // Device metrics: the ink box of the glyphs, relative to the baseline origin, so the ink is centred
  const opts = 8; // NSStringDrawingUsesDeviceMetrics
  let a = attributed(text, fs, fg);
  let b = a.boundingRectWithSizeOptions($.NSMakeSize(1e5, 1e5), opts);
  const max = size * (label ? 0.62 : 0.74);
  const k = Math.min(1, max / Math.max(b.size.width, 1), max / Math.max(b.size.height, 1));
  if (k < 1) {
    a = attributed(text, fs * k, fg);
    b = a.boundingRectWithSizeOptions($.NSMakeSize(1e5, 1e5), opts);
  }
  const x = size / 2 - (b.origin.x + b.size.width / 2);
  const y = size / 2 - (b.origin.y + b.size.height / 2);
  if (b.size.width > 0) a.drawWithRectOptions($.NSMakeRect(x, y, 1e5, 1e5), opts);
  $.NSGraphicsContext.restoreGraphicsState;
  const png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
  if (png.isNil()) return false;
  mkdirp(path.replace(/\/[^/]*$/, ""));
  return png.writeToFileAtomically(path, true);
}

// Icon for a character row: rendered lazily into the cache and reused afterwards
function glyphIcon(key, str, label) {
  const path = `${cacheDir()}/glyphs-${ICON_VERSION}-${ICON_STYLE}/${key}.png`;
  if (exists(path)) return { path };
  if (rendered >= RENDER_PER_RUN) {
    pendingIcons = true;
    return { path: "icons/char.png" };
  }
  rendered++;
  try {
    if (drawGlyph(str, path, label)) return { path };
  } catch (e) {
    // fall through to the generic icon
  }
  renderFailed = true;
  return { path: "icons/char.png" };
}

// ⌘Y: a Quick Look page with the character drawn large and its details
function previewPage(key, glyph, title, rowsHtml) {
  const path = `${cacheDir()}/preview/${key}.html`;
  if (exists(path)) return path;
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${htmlEscape(title)}</title><style>
:root{color-scheme:light dark}body{font:15px -apple-system,system-ui,sans-serif;margin:0;padding:28px;background:Canvas;color:CanvasText}
.g{font-size:180px;line-height:1.15;text-align:center;padding:24px 0 12px;word-break:break-all}
h1{font-size:20px;text-align:center;margin:0 0 20px;font-weight:600}table{border-collapse:collapse;margin:0 auto;min-width:60%}
td{padding:5px 10px;border-top:1px solid rgba(127,127,127,.25);vertical-align:top}td:first-child{opacity:.6;white-space:nowrap}
code{font:14px ui-monospace,Menlo,monospace;word-break:break-all}</style></head><body>
<div class="g">${htmlEscape(glyph)}</div><h1>${htmlEscape(title)}</h1><table>${rowsHtml}</table></body></html>`;
  return writeText(path, html) ? path : undefined; // no Quick Look rather than a missing file
}
const tr = (k, v) => `<tr><td>${htmlEscape(k)}</td><td><code>${htmlEscape(v)}</code></td></tr>`;

// Now and then, icons and previews older than 60 days and large results older than a day are removed
function pruneCache() {
  if (TEST ? env("SYM_TEST_PRUNE", "") !== "1" : Math.random() > 0.01) return;
  const now = Date.now();
  const subs = FM.contentsOfDirectoryAtPathError(cacheDir(), $());
  if (subs.isNil()) return;
  for (let j = 0; j < subs.count; j++) {
    const sub = subs.objectAtIndex(j).js;
    if (!/^(glyphs-|preview$|results$)/.test(sub)) continue;
    if (/^glyphs-/.test(sub) && !sub.startsWith(`glyphs-${ICON_VERSION}-`)) {
      FM.removeItemAtPathError(`${cacheDir()}/${sub}`, $()); // icons of an older version
      continue;
    }
    const limit = now - (sub === "results" ? 1 : 60) * 86400 * 1000;
    const dir = `${cacheDir()}/${sub}`;
    const names = FM.contentsOfDirectoryAtPathError(dir, $());
    if (names.isNil()) continue;
    for (let i = 0; i < names.count; i++) {
      const p = `${dir}/${names.objectAtIndex(i).js}`;
      const attrs = FM.attributesOfItemAtPathError(p, $());
      if (!attrs.isNil() && attrs.fileModificationDate.timeIntervalSince1970 * 1000 < limit) FM.removeItemAtPathError(p, $());
    }
  }
}

// ---------- Unicode data ----------

let BLOCKS = null;
function blockOf(cp) {
  if (!BLOCKS) BLOCKS = dataLines("blocks.txt").map((f) => [parseInt(f[0], 16), parseInt(f[1], 16), f[2]]);
  let lo = 0, hi = BLOCKS.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cp < BLOCKS[mid][0]) hi = mid - 1;
    else if (cp > BLOCKS[mid][1]) lo = mid + 1;
    else return BLOCKS[mid][2];
  }
  return "No block";
}

const GC = {
  Lu: "Uppercase letter", Ll: "Lowercase letter", Lt: "Titlecase letter", Lm: "Modifier letter", Lo: "Letter",
  Mn: "Combining mark", Mc: "Spacing mark", Me: "Enclosing mark", Nd: "Digit", Nl: "Letter number", No: "Number",
  Pc: "Connector punctuation", Pd: "Dash", Ps: "Opening punctuation", Pe: "Closing punctuation", Pi: "Opening quote",
  Pf: "Closing quote", Po: "Punctuation", Sm: "Math symbol", Sc: "Currency symbol", Sk: "Modifier symbol", So: "Symbol",
  Zs: "Space", Zl: "Line separator", Zp: "Paragraph separator", Cc: "Control", Cf: "Format", Cs: "Surrogate",
  Co: "Private use", Cn: "Unassigned", Emoji: "Emoji sequence",
};

// One record per character or emoji sequence
function parseLine(line) {
  const f = line.split("\t");
  const cps = f[0].split(" ").map((h) => parseInt(h, 16));
  const keys = f[5] ? f[5].split(";") : [];
  return { cps, name: f[1], aliases: f[2] ? f[2].split(";") : [], gc: f[3], entity: f[4] || "", abbr: keys[0] || "", keys };
}

let CHAR_INDEX = null;
function charByHex(hexKey) {
  // hexKey: lower-case hex, space-separated for sequences
  if (!CHAR_INDEX) {
    CHAR_INDEX = new Map();
    const s = data("chars.txt");
    let start = 0;
    while (start < s.length) {
      let end = s.indexOf("\n", start);
      if (end < 0) end = s.length;
      const tab = s.indexOf("\t", start);
      if (tab > start && tab < end) CHAR_INDEX.set(s.slice(start, tab), [start, end]);
      start = end + 1;
    }
  }
  const pos = CHAR_INDEX.get(hexKey);
  return pos ? parseLine(data("chars.txt").slice(pos[0], pos[1])) : null;
}

let RANGES = null;
function charInfo(cp) {
  const rec = charByHex(cp.toString(16));
  if (rec) return rec;
  if (!RANGES) RANGES = dataLines("ranges.txt").map((f) => [parseInt(f[0], 16), parseInt(f[1], 16), f[2], f[3]]);
  for (const r of RANGES) if (cp >= r[0] && cp <= r[1]) return { cps: [cp], name: r[2] + hex4(cp), aliases: [], gc: r[3], entity: "" };
  const block = blockOf(cp);
  let name = "UNASSIGNED", gc = "Cn";
  if (isSurrogate(cp)) (name = "SURROGATE"), (gc = "Cs");
  else if (/Private Use/.test(block)) (name = "PRIVATE USE"), (gc = "Co");
  else if ((cp & 0xfffe) === 0xfffe || (cp >= 0xfdd0 && cp <= 0xfdef)) name = "NONCHARACTER";
  return { cps: [cp], name: `<${name}-${hex4(cp)}>`, aliases: [], gc, entity: "", unnamed: true };
}

// ---------- formats ----------

function utf8Bytes(cps) {
  const out = [];
  for (const c of cps) {
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out;
}
function utf16Units(cps) {
  const out = [];
  for (const c of cps) {
    if (c < 0x10000) out.push(c);
    else out.push(0xd800 + ((c - 0x10000) >> 10), 0xdc00 + ((c - 0x10000) & 0x3ff));
  }
  return out;
}
const hx = (n, w) => n.toString(16).toUpperCase().padStart(w, "0");

let ENTITY_OF = null;
function entityName(cp, rec) {
  if (rec && rec.cps.length === 1 && rec.cps[0] === cp) return rec.entity;
  if (!ENTITY_OF) {
    ENTITY_OF = new Map();
    for (const f of dataLines("entities.txt")) {
      if (f[1].includes(" ")) continue;
      const c = parseInt(f[1], 16), prev = ENTITY_OF.get(c);
      const rank = (n) => [n !== n.toLowerCase() ? 1 : 0, n.length];
      if (!prev || rank(f[0])[0] < rank(prev)[0] || (rank(f[0])[0] === rank(prev)[0] && f[0].length < prev.length)) ENTITY_OF.set(c, f[0]);
    }
  }
  return ENTITY_OF.get(cp) || "";
}

// Every notation of a character or sequence
function formats(cps, rec) {
  const named = (c) => entityName(c, rec);
  const single = cps.length === 1 && rec && !rec.unnamed;
  return {
    html: cps.map((c) => (named(c) ? `&${named(c)};` : `&#x${hx(c, 0)};`)).join(""),
    hex: cps.map((c) => `&#x${hx(c, 0)};`).join(""),
    dec: cps.map((c) => `&#${c};`).join(""),
    js: cps.map((c) => `\\u{${hx(c, 0)}}`).join(""),
    js16: utf16Units(cps).map((u) => `\\u${hx(u, 4)}`).join(""),
    uplus: cps.map(uplus).join(" "),
    python: single && /^[A-Z0-9 -]+$/.test(rec.name) ? `\\N{${rec.name}}` : cps.map((c) => (c > 0xffff ? `\\U${hx(c, 8)}` : `\\u${hx(c, 4)}`)).join(""),
    css: cps.map((c) => `\\${hx(c, 0)}`).join(" "),
    utf8: utf8Bytes(cps).map((b) => hx(b, 2)).join(" "),
    utf16: utf16Units(cps).map((u) => hx(u, 4)).join(" "),
    url: utf8Bytes(cps).map((b) => (b < 0x80 && /[A-Za-z0-9\-_.~]/.test(String.fromCharCode(b)) ? String.fromCharCode(b) : `%${hx(b, 2)}`)).join(""),
    decimal: cps.map(String).join(" "),
  };
}
const ALT_LABEL = { html: "HTML entity", hex: "hex HTML entity", js: "\\u{…} escape", uplus: "U+XXXX", python: "Python escape", css: "CSS escape" };

// ---------- character rows ----------

function uniKey(cps) {
  return "u:" + cps.map((c) => c.toString(16)).join("-");
}

function invisibleLabel(rec) {
  if (!/^(Z|C|Emoji)/.test(rec.gc) || rec.gc === "Emoji") return null;
  if (rec.abbr && rec.abbr.length <= 6) return rec.abbr;
  if (rec.entity && rec.entity.length <= 6) return rec.entity.toUpperCase();
  const first = rec.name.split(" ")[0];
  return !rec.unnamed && first.length <= 6 ? first : hex4(rec.cps[0]);
}

// The character as shown in a title or drawn in an icon
function shownGlyph(rec) {
  const s = strOf(rec.cps);
  if (/^M/.test(rec.gc)) return "\u25cc" + s; // combining marks sit on a dotted circle
  return s;
}

// In icons a mark sits on a no-break space: fonts with the dotted circle drop the mark when drawing
function iconGlyph(rec) {
  const s = strOf(rec.cps);
  return /^M/.test(rec.gc) ? "\u00a0" + s : s;
}

function charRow(rec, opts = {}) {
  const cps = rec.cps;
  const key = uniKey(cps);
  const str = strOf(cps);
  const bad = cps.some(isSurrogate) || cps.includes(0) || (rec.unnamed && /UNASSIGNED|SURROGATE/.test(rec.name));
  const label = invisibleLabel(rec);
  const fav = opts.favs && opts.favs.has(key);
  const name = rec.unnamed ? rec.name : titleCase(rec.name);
  const glyph = label ? "" : shownGlyph(rec);
  const block = cps.length === 1 ? blockOf(cps[0]) : GC.Emoji;
  const f = formats(cps, rec);
  const alias = rec.aliases.find((a) => a.length > 6 && titleCase(a) !== name);
  const parts = [f.uplus, block];
  if (alias) parts.push(`= ${titleCase(alias).toLowerCase()}`);
  const note = bad ? (cps.includes(0) ? "U+0000 can’t be passed through Alfred" : "Can’t be copied on its own") : "";
  const hexKey = cps.map((c) => c.toString(16)).join("-");
  const detail = [
    tr("Code point", f.uplus), tr("Block", block), tr("Category", lookup(GC, rec.gc) || rec.gc),
    ...rec.aliases.slice(0, 6).map((a) => tr("Also known as", titleCase(a))),
    tr("HTML", f.html), tr("JavaScript", f.js), tr("UTF-16", f.js16), tr("Python", f.python), tr("CSS", f.css),
    tr("UTF-8 bytes", f.utf8), tr("URL encoded", f.url),
  ].join("");
  const item = {
    uid: key,
    title: `${glyph ? glyph + "  " : label ? `[${label}]  ` : ""}${name}`,
    subtitle: `${fav ? "★ " : opts.recent ? "↺ " : ""}${note || parts.join(" · ")}`,
    arg: bad ? "" : str,
    valid: !bad,
    icon: bad ? { path: "icons/char.png" } : glyphIcon(hexKey, label ? "" : iconGlyph(rec), label),
    variables: { sym_rec: key },
    text: { copy: bad ? f.uplus : str, largetype: glyph || name },
    quicklookurl: previewPage(`v${ICON_VERSION}-${hexKey}`, glyph || label || "", name, detail),
    mods: {
      cmd: { arg: bad ? "" : str, valid: !bad, subtitle: "Paste into the frontmost app", variables: { sym_rec: key } },
      alt: { arg: f[ALT_FORMAT], valid: true, subtitle: `Copy as ${lookup(ALT_LABEL, ALT_FORMAT)}: ${f[ALT_FORMAT]}`, variables: { sym_rec: key } },
      ctrl: { arg: key, valid: !bad, subtitle: fav ? "Remove from favourites" : "Add to favourites" },
      "cmd+alt": { arg: bad ? "" : str, valid: !bad, subtitle: "Show every format (HTML entity, \\u{…}, U+XXXX, UTF-8…)" },
    },
  };
  if (!bad && str.length === 1 && /[\s]/.test(str)) item.text.largetype = name;
  return item;
}

function favSet() {
  return new Set(favourites());
}

function keyToCps(key) {
  const cps = key.slice(2).split("-").map((h) => parseInt(h, 16));
  return cps.every((c) => Number.isInteger(c) && c >= 0 && c <= 0x10ffff) ? cps : null;
}
function recFromCps(cps) {
  if (cps.length === 1) return charInfo(cps[0]);
  const rec = charByHex(cps.map((c) => c.toString(16)).join(" "));
  return rec || { cps, name: `Sequence of ${cps.length} code points`, aliases: [], gc: "Emoji", entity: "", unnamed: true };
}

// ---------- Unicode search ----------

const CP_RE = /^(?:u\+|U\+|\\u\{|\\u|\\U|0x|&#x|%u)([0-9a-fA-F]{1,8})(?:\}|;)?$|^[uU]([0-9a-fA-F]{4,6})$/;

// U+2192, u2192, \u{1F600}, \U0001F600, 0x2192, &#x2192;, &#8594; → code point
function parseCodePoint(t) {
  let m = CP_RE.exec(t);
  let cp = null;
  if (m) cp = parseInt(m[1] || m[2], 16); // a bare "u" needs 4 digits: "ue" is a word, not U+000E
  else if ((m = /^&#(\d{1,7});?$/.exec(t))) cp = parseInt(m[1], 10);
  return cp !== null && cp <= 0x10ffff ? cp : null;
}

// "arrow right" matches the everyday name "RIGHT ARROW": the same words in any order
function sameWords(k, toks) {
  const w = k.split(" ");
  return w.length === toks.length && w.every((x) => toks.includes(x));
}

function nameSearch(toks, favs, recentSet) {
  if (!toks.length) return [];
  const s = data("chars.txt");
  const anchor = toks.slice().sort((a, b) => b.length - a.length)[0];
  // tokens are [A-Z0-9]+ only, so they are safe inside a pattern; hex and categories are lower/mixed case
  const scan = new RegExp(`^[^\\t\\n]*\\t[^\\n]*\\b${anchor}[^\\n]*`, "gm");
  const pre = toks.map((t) => new RegExp(`\\b${t}`));
  const whole = toks.map((t) => new RegExp(`\\b${t}\\b`));
  const joined = toks.join(" ");
  const res = [];
  let m, order = 0;
  while ((m = scan.exec(s))) {
    order++;
    const line = m[0];
    const t1 = line.indexOf("\t"), t2 = line.indexOf("\t", t1 + 1), t3 = line.indexOf("\t", t2 + 1);
    const name = line.slice(t1 + 1, t2), aliases = line.slice(t2 + 1, t3);
    const abbr = line.slice(line.lastIndexOf("\t") + 1);
    let score = 0, ok = true, inName = 0;
    for (let i = 0; i < toks.length; i++) {
      if (pre[i].test(name)) {
        score += whole[i].test(name) ? 5 : 2;
        inName++;
      } else if (pre[i].test(aliases)) score += whole[i].test(aliases) ? 2 : 1;
      else {
        ok = false;
        break;
      }
    }
    if (!ok) continue;
    if (name === joined) score += 40;
    else if (abbr && abbr.split(";").some((k) => k === joined || sameWords(k, toks))) score += 8; // NBSP, ZWJ, everyday names
    else if (aliases.split(";").includes(joined)) score += 2;
    if (inName === toks.length) {
      score += 2;
      if (name.indexOf(toks[toks.length - 1]) >= name.indexOf(toks[0])) score += 1;
    }
    if (/^(MATHEMATICAL|TAG|CJK COMPATIBILITY|SIGNWRITING|TANGUT|KHITAN|BYZANTINE MUSICAL)/.test(name) && !toks.some((t) => name.startsWith(t))) score -= 2;
    const hexKey = line.slice(0, t1);
    if (/^M/.test(line.slice(t3 + 1, t3 + 3)) && !toks.includes("COMBINING")) score -= 1.5;
    const key = "u:" + hexKey.replace(/ /g, "-");
    if (favs.has(key)) score += 50;
    if (recentSet.has(key)) score += 4;
    // shorter names, and the scripts and symbols most people look for, come first
    const cp = parseInt(hexKey, 16);
    if (cp < 0x2b0 || (cp >= 0x370 && cp < 0x530)) score += 2;
    else if (cp < 0x3000 || (cp >= 0x1f300 && cp <= 0x1faff)) score += 1;
    score -= name.length / 20;
    res.push({ score, order, line });
  }
  res.sort((a, b) => b.score - a.score || a.order - b.order);
  return res.slice(0, MAX_RESULTS).map((r) => parseLine(r.line));
}

function uniEmpty(favs) {
  const items = [];
  const seen = new Set();
  for (const k of favourites()) {
    const cps = k.startsWith("u:") && keyToCps(k);
    if (!cps || seen.has(k)) continue;
    seen.add(k);
    items.push(charRow(recFromCps(cps), { favs }));
  }
  for (const k of recents("u:")) {
    const cps = keyToCps(k);
    if (!cps || seen.has(k)) continue;
    seen.add(k);
    items.push(charRow(recFromCps(cps), { favs, recent: true }));
  }
  const meta = readJSON("./data/meta.json", {});
  const total = (meta.characters || 0).toLocaleString("en-US");
  const hints = [
    info(`Search ${total} Unicode ${meta.unicode || ""} characters`, "Type a name like “arrow”, a code point like U+2192, or paste a character", "uni"),
    info("arrow", "Search by name or alias", "uni", { autocomplete: "arrow" }),
    info("U+1F600", "Look up a code point (also 1f600, \\u{1f600}, &#x1f600;, &hearts;)", "format", { autocomplete: "U+1F600" }),
    info("→", "Paste characters to identify each one", "inspect", { autocomplete: "→" }),
  ];
  return items.length ? items : hints;
}

function uniItems(query) {
  const q = query.trim();
  const favs = favSet();
  if (q === "") return uniEmpty(favs);
  // pasted characters: identify each one ("what is this character?")
  if (/[^\x00-\x7f]/.test(q) || !/[A-Za-z0-9]/.test(q)) return inspectItems(q);
  const items = [];
  const seen = new Set();
  const push = (rec) => {
    const k = uniKey(rec.cps);
    if (seen.has(k)) return;
    seen.add(k);
    items.push(charRow(rec, { favs }));
  };
  // code points and entities
  const parts = q.split(/\s+/);
  const cpList = parts.map(parseCodePoint);
  if (cpList.every((c) => c !== null)) {
    if (cpList.length === 1) push(charInfo(cpList[0]));
    else push(recFromCps(cpList));
    if (cpList.length > 1) for (const c of cpList) push(charInfo(c));
  }
  const ent = /^&([A-Za-z][A-Za-z0-9]{1,31});?$/.exec(q);
  if (ent) {
    const hit = dataLines("entities.txt").find((f) => f[0] === ent[1]);
    if (hit) push(recFromCps(hit[1].split(" ").map((h) => parseInt(h, 16))));
    else items.push(info(`No HTML entity named &${ent[1]};`, "Entity names are case-sensitive, like &rarr; or &Alpha;", "error"));
  }
  // a single ASCII letter or digit: that character first
  if (q.length === 1) push(charInfo(q.codePointAt(0)));
  // a bare hex number with a digit (2192, 1f600): the code point, before the name matches
  const toks = tokens(q);
  for (const t of toks) {
    if (/^[0-9A-F]{4,6}$/.test(t) && /\d/.test(t)) {
      const cp = parseInt(t, 16);
      if (cp <= 0x10ffff) push(charInfo(cp));
    }
  }
  const recentSet = new Set(recents("u:", 30));
  if (!/^[&\\]|^(?:u\+|0x)/i.test(q) || !items.length) for (const rec of nameSearch(toks, favs, recentSet)) push(rec);
  if (!items.length) return [info(`No characters match “${oneLine(q, 60)}”`, "Try fewer or shorter words, or a code point like U+2192", "info")];
  return items.slice(0, MAX_RESULTS + 5);
}

// ---------- inspect: what is this character? ----------

// Grapheme-ish clusters for the summary: a base plus its marks and joiners
function graphemeCount(s) {
  if (typeof Intl !== "undefined" && Intl.Segmenter) {
    let n = 0;
    for (const _ of new Intl.Segmenter("en", { granularity: "grapheme" }).segment(s)) n++;
    return n;
  }
  return (s.match(/\P{M}\p{M}*/gu) || []).length;
}

function inspectItems(query) {
  let text = query, source = "text";
  if (text === "") {
    text = clipboard();
    source = "clipboard";
    if (text === "") return [info("Clipboard is empty", "Type or paste characters after the keyword to identify them", "inspect")];
  }
  let truncated = false;
  if (text.length > MAX_INPUT) {
    text = text.slice(0, MAX_INPUT);
    truncated = true;
  }
  const favs = favSet();
  const cps = cpsOf(text); // a lone surrogate comes out as its own code point
  const items = [];
  if (cps.length === 1 || (cps.length > 1 && charByHex(cps.map((c) => c.toString(16)).join(" ")))) {
    const rec = recFromCps(cps);
    items.push(charRow(rec, { favs }));
    return items.concat(formatRows(rec));
  }
  const utf16 = text.length, utf8 = utf8Bytes(cps.map((c) => (isSurrogate(c) ? 0xfffd : c))).length;
  const hidden = cps.filter((c) => {
    const r = c < 0x80 ? null : charInfo(c);
    return (c < 0x20 && c !== 10 && c !== 9) || (r && /^(Cf|Zs|Zl|Zp|Cc|Co|Cn|Cs)$/.test(r.gc));
  }).length;
  const f = formats(cps.map((c) => (isSurrogate(c) ? 0xfffd : c)), null);
  const summary = [plural(cps.length, "code point"), plural(graphemeCount(text), "character"), `${utf16} UTF-16 units`, `${utf8} UTF-8 bytes`];
  items.push(textRow(`${source === "clipboard" ? "Clipboard: " : ""}${summary.join(" · ")}`, f.uplus,
    hidden ? `⚠︎ ${plural(hidden, "invisible or unusual character")} · ↩ Copy as U+XXXX list` : "↩ Copy as U+XXXX list", "inspect"));
  items.push(textRow(`Copy as ${oneLine(f.js, 60)}`, f.js, "\\u{…} escapes (JavaScript, Swift, Rust, PHP)", "format"));
  items.push(textRow(`Copy as ${oneLine(f.html, 60)}`, f.html, "HTML entities", "format"));
  const limit = 200;
  for (const cp of cps.slice(0, limit)) {
    const rec = isSurrogate(cp) ? { cps: [cp], name: `<LONE SURROGATE-${hex4(cp)}>`, aliases: [], gc: "Cs", entity: "", unnamed: true } : charInfo(cp);
    const row = charRow(rec, { favs });
    row.uid = `${items.length}|${row.uid}`; // the same character can appear twice
    items.push(row);
  }
  if (cps.length > limit || truncated) items.push(info(`Showing the first ${limit} code points`, "Inspect a shorter text to see the rest", "info"));
  return items;
}

let LATEX_BY_CP = null;
function latexFor(cp) {
  if (!LATEX_BY_CP) {
    LATEX_BY_CP = new Map();
    for (const f of dataLines("latex.txt")) {
      const c = parseInt(f[1], 16);
      if (!LATEX_BY_CP.has(c)) LATEX_BY_CP.set(c, []);
      LATEX_BY_CP.get(c).push(f[0]);
    }
  }
  return LATEX_BY_CP.get(cp) || [];
}

function formatRows(rec) {
  const cps = rec.cps;
  const f = formats(cps, rec);
  const rows = [
    [f.uplus, "Code point"],
    [f.html, rec.cps.length === 1 && entityName(cps[0], rec) ? "HTML named entity" : "HTML entity"],
    [f.hex, "HTML hex entity"],
    [f.dec, "HTML decimal entity"],
    [f.js, "\\u{…} escape (JavaScript, Swift, Rust, PHP)"],
    [f.js16, "UTF-16 escape (JavaScript, JSON, Java, C#)"],
    [f.python, "Python escape"],
    [f.css, "CSS escape"],
    [f.url, "URL encoded"],
    [f.utf8, "UTF-8 bytes"],
    [f.utf16, "UTF-16 code units"],
    [f.decimal, "Decimal"],
  ];
  if (cps.length === 1) for (const cmd of latexFor(cps[0]).slice(0, 3)) rows.push([cmd, "LaTeX"]);
  const items = rows.map(([v, label]) => textRow(v, v, `${label} · ↩ Copy · ⌘↩ Paste`, "format"));
  if (cps.length === 1) {
    items.push(info(blockOf(cps[0]), `Block · ${lookup(GC, rec.gc) || rec.gc}`, "info"));
    for (const a of rec.aliases.slice(0, 8)) items.push(info(titleCase(a), "Also known as", "info"));
  }
  return items;
}

// ---------- kaomoji ----------

function kaoRow(f, favs, recent) {
  const key = `k:${f[0]}`;
  const fav = favs.has(key);
  const h = hash(f[0]);
  return {
    uid: key,
    title: f[0],
    subtitle: `${fav ? "★ " : recent ? "↺ " : ""}${f[1]} · ${f[2].split(" ").slice(0, 6).join(", ")}`,
    arg: f[0],
    icon: { path: "icons/kao.png" },
    variables: { sym_rec: key },
    text: { copy: f[0], largetype: f[0] },
    quicklookurl: previewPage(`kao-${h}`, f[0], f[1], tr("Keywords", f[2].split(" ").join(", "))),
    mods: {
      cmd: { arg: f[0], subtitle: "Paste into the frontmost app", variables: { sym_rec: key } },
      ctrl: { arg: key, subtitle: fav ? "Remove from favourites" : "Add to favourites" },
    },
  };
}

function kaoItems(query) {
  const all = dataLines("kaomoji.txt");
  const favs = favSet();
  const q = query.trim().toLowerCase();
  const byText = new Map(all.map((f) => [f[0], f]));
  if (q === "") {
    const items = [];
    const seen = new Set();
    for (const k of favourites()) if (k.startsWith("k:") && byText.has(k.slice(2)) && !seen.has(k)) seen.add(k), items.push(kaoRow(byText.get(k.slice(2)), favs));
    for (const k of recents("k:")) if (byText.has(k.slice(2)) && !seen.has(k)) seen.add(k), items.push(kaoRow(byText.get(k.slice(2)), favs, true));
    const cats = [];
    for (const f of all) if (!cats.includes(f[1])) cats.push(f[1]);
    for (const c of cats) {
      const n = all.filter((f) => f[1] === c).length;
      const sample = all.find((f) => f[1] === c)[0];
      items.push(info(c, `${n} kaomoji · ${sample}`, "category", { autocomplete: `${c.toLowerCase()} `, valid: false }));
    }
    return items;
  }
  const toks = q.split(/[^a-z0-9]+/).filter(Boolean);
  // a category name typed in full (or picked with ↩) lists that category first
  const cat = all.find((f) => f[1].toLowerCase() === q);
  const res = [];
  all.forEach((f, order) => {
    const inCat = cat && f[1] === cat[1];
    if (!toks.length) {
      if (f[0].includes(query.trim())) res.push({ f, score: 1, order }); // symbols typed: search the kaomoji itself
      return;
    }
    const words = f[2].split(" ");
    const catWords = f[1].toLowerCase().split(/[^a-z]+/);
    let score = inCat ? 10 : 0;
    for (const t of toks) {
      if (words.includes(t)) score += 3;
      else if (words.some((w) => w.startsWith(t))) score += 2;
      else if (catWords.some((w) => w.startsWith(t))) score += 1;
      else if (!inCat) return;
    }
    if (favs.has(`k:${f[0]}`)) score += 20;
    res.push({ f, score, order });
  });
  res.sort((a, b) => b.score - a.score || a.order - b.order);
  if (!res.length) return [info(`No kaomoji match “${oneLine(query.trim(), 60)}”`, "Try a feeling or an animal, like happy, shrug or cat", "kao")];
  return res.slice(0, Math.max(MAX_RESULTS, 60)).map((r) => kaoRow(r.f, favs));
}

// ---------- fancy text ----------

function mathAlpha(upper, lower, digits, holes = {}) {
  return (c) => {
    const code = c.codePointAt(0);
    if (own(holes, c)) return holes[c];
    if (upper !== null && code >= 65 && code <= 90) return String.fromCodePoint(upper + code - 65);
    if (lower !== null && code >= 97 && code <= 122) return String.fromCodePoint(lower + code - 97);
    if (digits !== null && code >= 48 && code <= 57) return String.fromCodePoint(digits + code - 48);
    return null;
  };
}
function fromTable(from, to) {
  const a = Array.from(from), b = Array.from(to);
  const map = new Map(a.map((c, i) => [c, b[i]]));
  return (c) => (map.has(c) ? map.get(c) : null);
}
const SCRIPT_HOLES = { B: "ℬ", E: "ℰ", F: "ℱ", H: "ℋ", I: "ℐ", L: "ℒ", M: "ℳ", R: "ℛ", e: "ℯ", g: "ℊ", o: "ℴ" };
const FRAKTUR_HOLES = { C: "ℭ", H: "ℌ", I: "ℑ", R: "ℜ", Z: "ℨ" };
const DOUBLE_HOLES = { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ", R: "ℝ", Z: "ℤ" };
const ABC = "abcdefghijklmnopqrstuvwxyz";
const UPSIDE = fromTable(
  "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,'\"!?()[]{}<>_&;",
  "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz∀ꓭƆꓷƎℲ⅁HIſꓘ⅂WNOԀΌꓤS⊥∩ΛMX⅄Z0ƖᄅƐㄣϛ9ㄥ86˙'„,¡¿)(][}{><‾⅋؛"
);

const FANCY = [
  { id: "bold", name: "Bold", map: mathAlpha(0x1d400, 0x1d41a, 0x1d7ce) },
  { id: "italic", name: "Italic", map: mathAlpha(0x1d434, 0x1d44e, null, { h: "ℎ" }) },
  { id: "bolditalic", name: "Bold italic", map: mathAlpha(0x1d468, 0x1d482, null) },
  { id: "script", name: "Script", map: mathAlpha(0x1d49c, 0x1d4b6, null, SCRIPT_HOLES) },
  { id: "boldscript", name: "Bold script", map: mathAlpha(0x1d4d0, 0x1d4ea, null) },
  { id: "fraktur", name: "Fraktur", map: mathAlpha(0x1d504, 0x1d51e, null, FRAKTUR_HOLES) },
  { id: "boldfraktur", name: "Bold fraktur", map: mathAlpha(0x1d56c, 0x1d586, null) },
  { id: "double", name: "Double-struck", map: mathAlpha(0x1d538, 0x1d552, 0x1d7d8, DOUBLE_HOLES) },
  { id: "sans", name: "Sans-serif", map: mathAlpha(0x1d5a0, 0x1d5ba, 0x1d7e2) },
  { id: "sansbold", name: "Sans-serif bold", map: mathAlpha(0x1d5d4, 0x1d5ee, 0x1d7ec) },
  { id: "sansitalic", name: "Sans-serif italic", map: mathAlpha(0x1d608, 0x1d622, null) },
  { id: "sansbolditalic", name: "Sans-serif bold italic", map: mathAlpha(0x1d63c, 0x1d656, null) },
  { id: "mono", name: "Monospace", map: mathAlpha(0x1d670, 0x1d68a, 0x1d7f6) },
  { id: "fullwidth", name: "Fullwidth", map: (c) => { const n = c.codePointAt(0); return n === 32 ? "\u3000" : n > 32 && n < 127 ? String.fromCodePoint(n + 0xfee0) : null; } },
  { id: "smallcaps", name: "Small caps", map: fromTable(ABC, "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘꞯʀꜱᴛᴜᴠᴡxʏᴢ"), lower: true },
  { id: "circled", name: "Circled", map: (c) => { const n = c.codePointAt(0); if (n >= 65 && n <= 90) return String.fromCodePoint(0x24b6 + n - 65); if (n >= 97 && n <= 122) return String.fromCodePoint(0x24d0 + n - 97); if (n === 48) return "⓪"; if (n >= 49 && n <= 57) return String.fromCodePoint(0x2460 + n - 49); return null; } },
  { id: "negcircled", name: "Negative circled", map: (c) => { const n = c.toUpperCase().codePointAt(0); if (n >= 65 && n <= 90) return String.fromCodePoint(0x1f150 + n - 65); if (n === 48) return "⓿"; if (n >= 49 && n <= 57) return String.fromCodePoint(0x2776 + n - 49); return null; } },
  { id: "squared", name: "Squared", map: (c) => { const n = c.toUpperCase().codePointAt(0); return n >= 65 && n <= 90 ? String.fromCodePoint(0x1f130 + n - 65) : null; } },
  { id: "negsquared", name: "Negative squared", map: (c) => { const n = c.toUpperCase().codePointAt(0); return n >= 65 && n <= 90 ? String.fromCodePoint(0x1f170 + n - 65) : null; } },
  { id: "paren", name: "Parenthesized", map: (c) => { const n = c.codePointAt(0); if (n >= 97 && n <= 122) return String.fromCodePoint(0x249c + n - 97); if (n >= 65 && n <= 90) return String.fromCodePoint(0x1f110 + n - 65); if (n >= 49 && n <= 57) return String.fromCodePoint(0x2474 + n - 49); return null; } },
  { id: "superscript", name: "Superscript", map: fromTable("abcdefghijklmnopqrstuvwxyzABDEGHIJKLMNOPRTUVW0123456789+-=()", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖ𐞥ʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾") },
  { id: "subscript", name: "Subscript", map: fromTable("aehijklmnoprstuvx0123456789+-=()", "ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎") },
  { id: "upsidedown", name: "Upside down", map: UPSIDE, reverse: true },
  { id: "strike", name: "Strikethrough", mark: "\u0336" },
  { id: "slash", name: "Slashed", mark: "\u0338" },
  { id: "underline", name: "Underlined", mark: "\u0332" },
  { id: "doubleunderline", name: "Double underlined", mark: "\u0333" },
];

// Clusters: a base character with its combining marks (and emoji joiners), so marks and surrogate pairs stay whole
function clusters(s) {
  return s.match(/(?:\P{M}|^\p{M})\p{M}*(?:\u200d\P{M}\p{M}*)*/gu) || [];
}

function fancy(style, text) {
  if (style.mark) return clusters(text).map((g) => (/^\s+$/.test(g) ? g : g + style.mark)).join("");
  // letters with accents are decomposed so that é becomes 𝐞 + ◌́
  const src = text.normalize("NFD");
  let out = clusters(src).map((g) => {
    const base = Array.from(g)[0];
    const mapped = style.map(style.lower ? base.toLowerCase() : base);
    return mapped === null ? g : mapped + g.slice(base.length);
  });
  if (style.reverse) out = out.reverse();
  return out.join("").normalize("NFC");
}

// Styled text back to plain: NFKC folds mathematical letters, fullwidth, circled, super- and subscripts
function unfancy(text) {
  return text.replace(/[\u0332\u0333\u0336\u0338]/g, "").normalize("NFKC").replace(/\u3000/g, " ");
}

function fancyItems(query) {
  let text = query, source = "";
  if (text.trim() === "") {
    text = clipboard();
    source = "clipboard";
    if (text.trim() === "") return [info("Type the text to style", "Or copy it first, or use the Universal Action on selected text", "fancy")];
  }
  // 27 styled copies go into the JSON: styled text is for short snippets
  if (text.length > MAX_FANCY) return [info(`Fancy text works on up to ${MAX_FANCY.toLocaleString("en-US")} characters`, "Select or type less text", "error")];
  const items = FANCY.map((st) => {
    const v = fancy(st, text);
    return textRow(v, v, `${st.name}${source ? " · from the clipboard" : ""} · ↩ Copy · ⌘↩ Paste`, "fancy", { uid: `fancy-${st.id}` });
  });
  const plain = unfancy(text);
  if (plain !== text) items.unshift(textRow(plain, plain, "Plain text: styling removed · ↩ Copy · ⌘↩ Paste", "ok", { uid: "fancy-plain" }));
  return items;
}

// ---------- LaTeX ----------

const PKG_LABEL = { LaTeX: "LaTeX", amssymb: "amssymb", mathrsfs: "mathrsfs", text: "text mode", "unicode-math": "unicode-math", other: "needs a package" };

function texRow(f, favs, recent) {
  const cp = parseInt(f[1], 16);
  const ch = String.fromCodePoint(cp);
  const key = `x:${f[0]}`;
  const fav = favs.has(key);
  const primary = TEX_PRIMARY === "character" ? ch : f[0];
  const secondary = TEX_PRIMARY === "character" ? f[0] : ch;
  const rec = { cps: [cp], name: f[3], aliases: [], gc: cp >= 0x300 && cp <= 0x36f ? "Mn" : "So", entity: "" };
  const glyph = shownGlyph(rec);
  return {
    uid: key,
    title: `${f[0]}   ${glyph}`,
    subtitle: `${fav ? "★ " : recent ? "↺ " : ""}${titleCase(f[3])} · ${lookup(PKG_LABEL, f[2]) || f[2]} · ${uplus(cp)}`,
    arg: primary,
    icon: glyphIcon(cp.toString(16), iconGlyph(rec)),
    variables: { sym_rec: key },
    text: { copy: primary, largetype: `${glyph}  ${f[0]}` },
    quicklookurl: previewPage(`v${ICON_VERSION}-tex-${hash(f[0])}`, glyph, f[0], tr("Character", titleCase(f[3])) + tr("Code point", uplus(cp)) + tr("Package", lookup(PKG_LABEL, f[2]) || f[2])),
    mods: {
      cmd: { arg: primary, subtitle: "Paste into the frontmost app", variables: { sym_rec: key } },
      alt: { arg: secondary, subtitle: TEX_PRIMARY === "character" ? `Copy the command ${f[0]}` : `Copy the character ${ch}`, variables: { sym_rec: key } },
      ctrl: { arg: key, subtitle: fav ? "Remove from favourites" : "Add to favourites" },
    },
  };
}

function texItems(query) {
  const all = dataLines("latex.txt");
  const favs = favSet();
  const q = query.trim();
  if (q === "") {
    const byCmd = new Map(all.map((f) => [f[0], f]));
    const items = [];
    const seen = new Set();
    for (const k of favourites()) if (k.startsWith("x:") && byCmd.has(k.slice(2)) && !seen.has(k)) seen.add(k), items.push(texRow(byCmd.get(k.slice(2)), favs));
    for (const k of recents("x:")) if (byCmd.has(k.slice(2)) && !seen.has(k)) seen.add(k), items.push(texRow(byCmd.get(k.slice(2)), favs, true));
    if (!items.length) items.push(info(`Search ${all.length.toLocaleString("en-US")} LaTeX commands`, "Type a command like \\alpha or leq, a name like “subset”, or paste a symbol like ∈", "tex"));
    return items;
  }
  const res = [];
  if (/[^\x00-\x7f]/.test(q)) {
    // symbols: the commands that produce them
    const want = new Set(cpsOf(q));
    all.forEach((f, order) => {
      if (want.has(parseInt(f[1], 16))) res.push({ f, score: 0, order });
    });
    if (!res.length) return [info(`No LaTeX command for ${oneLine(q, 40)}`, "Only characters with a known LaTeX command are listed", "tex")];
    return res.slice(0, MAX_RESULTS).map((r) => texRow(r.f, favs));
  }
  const cmdQ = q.replace(/^\\/, "");
  const low = cmdQ.toLowerCase();
  const toks = tokens(q);
  const pre = toks.map((t) => new RegExp(`\\b${t}`));
  const whole = toks.map((t) => new RegExp(`\\b${t}\\b`));
  all.forEach((f, order) => {
    const bare = f[0].slice(1);
    const bl = bare.toLowerCase();
    let score = -1;
    if (bare === cmdQ) score = 100;
    else if (bl === low) score = 80;
    else if (bl.startsWith(low)) score = 50 - Math.min(20, bl.length - low.length);
    else if (low.length >= 3 && bl.includes(low)) score = 25 - Math.min(10, bl.length - low.length);
    if (toks.length) {
      let nameScore = 0;
      for (let i = 0; i < toks.length; i++) {
        if (pre[i].test(f[3])) nameScore += whole[i].test(f[3]) ? 3 : 2;
        else {
          nameScore = -1;
          break;
        }
      }
      if (nameScore > 0) score = Math.max(score, 10 + nameScore);
    }
    if (score < 0) return;
    const penalty = lookup({ LaTeX: 0, amssymb: 1, mathrsfs: 2, text: 2, other: 4, "unicode-math": 5 }, f[2]);
    score -= penalty === undefined ? 3 : penalty; // LaTeX's own commands (penalty 0) first
    if (favs.has(`x:${f[0]}`)) score += 30;
    res.push({ f, score, order });
  });
  res.sort((a, b) => b.score - a.score || a.order - b.order);
  if (!res.length) return [info(`No LaTeX commands match “${oneLine(q, 60)}”`, "Try a command like \\rightarrow or a name like “arrow”", "tex")];
  return res.slice(0, MAX_RESULTS).map((r) => texRow(r.f, favs));
}

// ---------- typography: catalogue ----------

function typoItems(query) {
  const all = dataLines("typography.txt");
  const toks = query.trim().toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const favs = favSet();
  const res = [];
  all.forEach((f, order) => {
    const hay = `${f[2]} ${f[4]} ${f[5]}`.toLowerCase().split(/[^a-z0-9]+/);
    let score = 0;
    for (const t of toks) {
      if (hay.includes(t)) score += 2;
      else if (hay.some((w) => w.startsWith(t))) score += 1;
      else return;
    }
    res.push({ f, score, order });
  });
  res.sort((a, b) => b.score - a.score || a.order - b.order);
  if (!res.length) return [info(`Nothing matches “${oneLine(query.trim(), 60)}”`, "Try quote, dash, space, bullet or fraction", "typo")];
  return res.map(({ f }) => {
    const cp = parseInt(f[1], 16);
    const rec = charInfo(cp);
    const row = charRow(rec, { favs });
    row.subtitle = `${f[3]} · ${uplus(cp)}${f[5] ? ` · &${f[5]};` : ""}`;
    row.title = `${invisibleLabel(rec) ? `[${invisibleLabel(rec)}]` : f[0]}  ${f[2]}`;
    row.uid = `typo-${f[1]}`;
    return row;
  });
}

// ---------- typography: fixer ----------

const QUOTES = {
  en: ["“", "”", "‘", "’"],
  "en-gb": ["‘", "’", "“", "”"],
  de: ["„", "“", "‚", "‘"],
  "de-guillemets": ["»", "«", "›", "‹"],
  fr: ["«\u202f", "\u202f»", "‹\u202f", "\u202f›"],
  ch: ["«", "»", "‹", "›"],
  sv: ["”", "”", "’", "’"],
  pl: ["„", "”", "‚", "’"],
};

// Splits text into parts that must not change (code, URLs, HTML tags) and parts that may
function protectedSplit(text) {
  const re = /```[\s\S]*?(?:```|$)|`[^`\n]*`|\bhttps?:\/\/[^\s<>"]*[^\s<>"'.,;:!?)]|<\/?[A-Za-z][^<>]*>/g;
  const parts = [];
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ t: text.slice(last, m.index), keep: false });
    parts.push({ t: m[0], keep: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ t: text.slice(last), keep: false });
  return parts;
}

function smartTypography(text, opts = {}) {
  const q = lookup(QUOTES, opts.quotes || conf("quote_style", "en")) || QUOTES.en;
  const emStyle = (opts.dashes || conf("dash_style", "em")) !== "en";
  const counts = { quotes: 0, apostrophes: 0, dashes: 0, ellipses: 0, symbols: 0, spaces: 0 };
  const count = (k, n = 1) => (counts[k] += n);
  const onlyQuotes = !!opts.onlyQuotes;
  const parts = protectedSplit(text);
  // the character before each part, so a quote after `code` or a URL still knows its context
  let prevChar = "";
  const out = parts.map((p) => {
    if (p.keep) {
      prevChar = p.t.slice(-1);
      return p.t;
    }
    let s = p.t;
    if (!onlyQuotes) {
      s = s.replace(/(\S) ---? (\S)/g, (m, a, b) => (count("dashes"), emStyle ? `${a}—${b}` : `${a} – ${b}`));
      // unspaced only between words: "--save" (a command-line option) and a Markdown "---" rule stay
      s = s.replace(/([^\s-])---(?=[^\s-])/g, (m, a) => (count("dashes"), a + "—"));
      s = s.replace(/([^\s-])--(?=[^\s-])/g, (m, a) => (count("dashes"), a + (emStyle ? "—" : "–")));
      s = s.replace(/([A-Za-z\u00c0-\u024f,;)]) - (?=[A-Za-z\u00c0-\u024f(])/g, (m, a) => (count("dashes"), emStyle ? `${a}—` : `${a} – `));
      s = s.replace(/\.\.\.|\. \. \./g, () => (count("ellipses"), "…"));
      s = s.replace(/\((c|C)\)/g, () => (count("symbols"), "©")).replace(/\((r|R)\)/g, () => (count("symbols"), "®")).replace(/\((tm|TM|Tm)\)/g, () => (count("symbols"), "™"));
      s = s.replace(/(\d) x (\d)/g, (m, a, b) => (count("symbols"), `${a} × ${b}`));
      s = s.replace(/(^|[\s(])([13])\/([24])(?=$|[\s,.;:!?)])/g, (m, a, n, d) => {
        const v = lookup({ "1/2": "½", "1/4": "¼", "3/4": "¾" }, `${n}/${d}`);
        return v ? (count("symbols"), a + v) : m;
      });
      s = s.replace(/(\d+)'(\d+(?:\.\d+)?)"/g, (m, a, b) => (count("symbols"), `${a}′${b}″`));
      s = s.replace(/(\S) {2,}(?=\S)/g, (m, a) => (count("spaces"), a + " "));
    }
    // apostrophes (always ’, whatever the quote style): rock 'n' roll, don't, it's, '90s, 'em
    s = s.replace(/(^|\s)'n'(?=\s)/g, (m, a) => (count("apostrophes", 2), a + "’n’"));
    s = s.replace(/([A-Za-z\u00c0-\u024f\d])'(?=[A-Za-z\u00c0-\u024f])/g, (m, a) => (count("apostrophes"), a + "’"));
    s = s.replace(/(^|[\s(\[—–-])'(?=\d\ds\b|\d\d\b|em\b|tis\b|twas\b)/g, (m, a) => (count("apostrophes"), a + "’"));
    // the remaining straight quotes open after a space, bracket or dash, and close otherwise
    let res = "";
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c !== '"' && c !== "'") {
        res += c;
        continue;
      }
      const before = res.length ? res[res.length - 1] : prevChar; // already converted: “'hi'” sees the “
      const opening = before === "" || /[\s(\[{<>—–\/-]/.test(before) || q[0].endsWith(before) || q[2].endsWith(before);
      if (c === '"') res += opening ? q[0] : q[1];
      else res += opening ? q[2] : q[3];
      count("quotes");
    }
    prevChar = res.slice(-1);
    return res;
  });
  return { text: out.join(""), counts };
}

function straighten(text) {
  return text
    .replace(/[“”„‟«»]/g, '"')
    .replace(/[‘’‚‛‹›′]/g, "'")
    .replace(/″/g, '"')
    .replace(/—/g, "--")
    .replace(/[–‒―‐‑−]/g, "-")
    .replace(/…/g, "...")
    .replace(/[\u00a0\u202f\u2007\u2009\u200a\u2002\u2003]/g, " ")
    .replace(/[\u200b\u2060\u00ad]/g, "");
}

function describeCounts(c) {
  const parts = [];
  const add = (n, w) => n && parts.push(plural(n, w));
  add(c.quotes, "quote");
  add(c.apostrophes, "apostrophe");
  add(c.dashes, "dash");
  add(c.ellipses, "ellipsis");
  add(c.symbols, "symbol");
  add(c.spaces, "double space");
  return parts.join(", ").replace(/ellipsiss/, "ellipses");
}

function typofixItems(query) {
  let text = query, source = "";
  if (text.trim() === "") {
    text = clipboard();
    source = " · from the clipboard";
    if (text.trim() === "") return [info("Type or copy the text to fix", "Or use the Universal Action on selected text", "typo")];
  }
  if (text.length > MAX_FIX) return [info("The text is too long", `Fix at most ${MAX_FIX.toLocaleString("en-US")} characters at a time`, "error")];
  const full = smartTypography(text);
  const quotesOnly = smartTypography(text, { onlyQuotes: true });
  const changes = describeCounts(full.counts);
  const items = [];
  items.push(textRow(full.text, full.text, changes ? `Smart typography: ${changes}${source}` : `Already typographically correct${source}`, changes ? "typo" : "ok", { uid: "typo-full" }));
  if (quotesOnly.text !== full.text && quotesOnly.text !== text) items.push(textRow(quotesOnly.text, quotesOnly.text, `Only quotes and apostrophes${source}`, "typo", { uid: "typo-quotes" }));
  const straight = straighten(text);
  if (straight !== text) items.push(textRow(straight, straight, `Straight quotes and plain dashes (for code)${source}`, "format", { uid: "typo-straight" }));
  const q = lookup(QUOTES, conf("quote_style", "en")) || QUOTES.en;
  const inner = full.text.trim();
  items.push(textRow(`${q[0]}${inner}${q[1]}`, `${q[0]}${inner}${q[1]}`, `Wrap in quotation marks${source}`, "typo", { uid: "typo-wrap" }));
  return items;
}

// ---------- actions ----------

function alfredSearch(text) {
  if (TEST) return `ALFRED_SEARCH:${text}`;
  Application("com.runningwithcrayons.Alfred").search(text);
  return undefined;
}

function showFormats(str) {
  const kw = conf("keyword_chars", "chars");
  return alfredSearch(`${kw} ${str}`);
}

function run(argv) {
  const [cmd, ...rest] = argv;
  const query = rest.join(" ");
  try {
    let items;
    switch (cmd) {
      case "uni": items = uniItems(query); break;
      case "chars": items = inspectItems(query); break;
      case "kao": items = kaoItems(query); break;
      case "fancy": items = fancyItems(query); break;
      case "tex": items = texItems(query); break;
      case "typo": items = typoItems(query); break;
      case "typofix": items = typofixItems(query); break;
      case "fav": return toggleFavourite(query);
      case "show": return showFormats(query);
      default: items = [info(`Unknown command: ${cmd}`, "", "error")];
    }
    try {
      pruneCache();
    } catch (e) {
      // pruning is best effort
    }
    return output(items);
  } catch (e) {
    return output([info("Couldn’t run Symbols", String(e && e.message ? e.message : e), "error")]);
  }
}
