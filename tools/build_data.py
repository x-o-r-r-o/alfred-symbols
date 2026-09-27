#!/usr/bin/env python3
"""Generate the workflow's data files (developer machine only; nothing here ships).

  python3 tools/build_data.py            # download the sources (once) and write src/data/*
  python3 tools/build_data.py --offline  # use tools/.cache only

Sources (downloaded into tools/.cache/, which is git-ignored):
  * Unicode Character Database 18.0.0: UnicodeData.txt, NameAliases.txt, Blocks.txt, NamesList.txt,
    emoji/emoji-test.txt (Unicode License v3)
  * unicode.xml by David Carlisle (W3C Software Notice and License): LaTeX commands
  * asciilib lib.json by Ian Sinnott (MIT): kaomoji
  * Python's html.entities.html5 (the WHATWG list of HTML named character references)

Outputs in src/data/ (committed: they are data, not code):
  chars.txt    one character or emoji sequence per line: hex<TAB>NAME<TAB>ALIASES<TAB>gc<TAB>entity<TAB>KEYS
               KEYS: the formal abbreviation first (NBSP, ZWJ…), then everyday names; an exact match ranks first
               hex is lower case (space-separated for sequences), NAME and ALIASES are upper-case ASCII,
               aliases are separated by ";". Lower-case hex and mixed-case gc/entity mean an upper-case
               search pattern can only ever match the NAME and ALIASES fields.
  ranges.txt   ranges whose names are derived by rule: start<TAB>end<TAB>PREFIX<TAB>gc
  blocks.txt   start<TAB>end<TAB>Block name
  kaomoji.txt  kaomoji<TAB>Category<TAB>keywords (lower case, space-separated)
  latex.txt    \\command<TAB>hex<TAB>package<TAB>NAME
  meta.json    versions and counts
"""
import hashlib, html.entities, json, os, re, sys, unicodedata, urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
OUT = os.path.join(ROOT, "src", "data")
UNICODE_VERSION = "18.0.0"
UCD = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/"
ASCIILIB_COMMIT = "52b034b55a684251b4f0c4974d707b9de4d198f4"
SOURCES = {
    "UnicodeData.txt": UCD + "UnicodeData.txt",
    "NameAliases.txt": UCD + "NameAliases.txt",
    "Blocks.txt": UCD + "Blocks.txt",
    "NamesList.txt": UCD + "NamesList.txt",
    "emoji-test.txt": f"https://www.unicode.org/Public/{UNICODE_VERSION}/emoji/emoji-test.txt",
    "unicode.xml": "https://www.w3.org/2003/entities/2007xml/unicode.xml",
    "asciilib.json": f"https://raw.githubusercontent.com/iansinnott/asciilib/{ASCIILIB_COMMIT}/lib.json",
}

# UnicodeData.txt "<…, First>" range labels → name prefix (Unicode 18.0 core spec, table 4-8).
# An unknown label stops the build, so a new Unicode version gets checked by hand.
RANGE_PREFIX = [
    (r"CJK Ideograph( Extension [A-Z])?", "CJK UNIFIED IDEOGRAPH-"),
    (r"Tangut Ideograph( Supplement)?", "TANGUT IDEOGRAPH-"),
    (r"Jurchen Character", "JURCHEN CHARACTER-"),
    (r"Seal Character", "SMALL SEAL CHARACTER-"),
]
SKIPPED_RANGES = ("Surrogate", "Private Use")


def fetch(name, offline):
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        if offline:
            sys.exit(f"missing {path} (run without --offline)")
        os.makedirs(CACHE, exist_ok=True)
        print("downloading", SOURCES[name])
        with urllib.request.urlopen(SOURCES[name], timeout=120) as r:
            data = r.read()
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return path


def fold(s):
    """Upper-case ASCII for searching: accents removed, curly apostrophes straightened."""
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.encode("ascii", "ignore").decode().upper()
    return re.sub(r"[\t;\n]+", " ", s).strip()


# ---------- Hangul syllable names (Unicode core spec 3.12) ----------
JAMO_L = "G GG N D DD R M B BB S SS  J JJ C K T P H".split(" ")
JAMO_V = "A AE YA YAE EO E YEO YE O WA WAE OE YO U WEO WE WI YU EU YI I".split(" ")
JAMO_T = [""] + "G GG GS N NJ NH D L LG LM LB LS LT LP LH M B BS S SS NG J C K T P H".split(" ")


def hangul_name(cp):
    s = cp - 0xAC00
    return "HANGUL SYLLABLE " + JAMO_L[s // 588] + JAMO_V[(s % 588) // 28] + JAMO_T[s % 28]


# Everyday names people type that the Unicode names and aliases don't have. An exact match ranks first.
COMMON_NAMES = {
    0x2192: "RIGHT ARROW", 0x2190: "LEFT ARROW", 0x2191: "UP ARROW", 0x2193: "DOWN ARROW", 0x2194: "LEFT RIGHT ARROW",
    0x21D2: "IMPLIES;DOUBLE RIGHT ARROW", 0x2713: "CHECKMARK;TICK", 0x2717: "CROSS MARK;CROSS", 0x263A: "SMILEY",
    0x2665: "HEART", 0x2605: "STAR", 0x2022: "BULLET POINT", 0xD7: "TIMES;MULTIPLY", 0xF7: "DIVIDE",
    0x2248: "APPROX;APPROXIMATELY", 0x2260: "NOT EQUAL;NOT EQUALS", 0xB1: "PLUS MINUS", 0x2318: "COMMAND;CMD",
    0x2325: "OPTION;ALT", 0x21E7: "SHIFT", 0x2303: "CONTROL;CTRL", 0x21A9: "RETURN", 0x23CE: "ENTER",
    0x238B: "ESCAPE;ESC", 0x232B: "DELETE;BACKSPACE", 0x21E5: "TAB KEY", 0x221A: "SQUARE ROOT;ROOT", 0x2122: "TRADEMARK;TM",
    0x3BB: "LAMBDA", 0x3C0: "PI", 0x3A3: "SIGMA;SUM", 0x2211: "SUMMATION", 0xB0: "DEGREES", 0x2026: "ELLIPSIS;DOTS",
    0x2014: "EM DASH", 0x2013: "EN DASH", 0x23F8: "PAUSE", 0x25B6: "PLAY", 0x23F9: "STOP", 0x266A: "MUSIC NOTE;NOTE",
    0x2601: "CLOUD", 0x2602: "UMBRELLA", 0x2600: "SUN", 0x263C: "SUN", 0x2744: "SNOWFLAKE", 0x26A1: "LIGHTNING",
    0x221E: "INFINITY;INFINITE", 0x2202: "PARTIAL", 0x2207: "NABLA;DEL",
    0x2200: "FOR ALL", 0x2203: "EXISTS;THERE EXISTS", 0x2208: "IN;ELEMENT", 0x2205: "EMPTY SET", 0x2229: "INTERSECTION",
    0x222A: "UNION", 0x2264: "LESS EQUAL;LEQ", 0x2265: "GREATER EQUAL;GEQ", 0x2030: "PERMILLE", 0x20AC: "EURO",
    0xA3: "POUND;GBP", 0xA5: "YEN", 0x20BF: "BITCOIN", 0xA9: "COPYRIGHT", 0xAE: "REGISTERED", 0xA7: "SECTION",
    0xB6: "PARAGRAPH", 0x2020: "DAGGER", 0x201C: "OPEN QUOTE;LEFT QUOTE", 0x201D: "CLOSE QUOTE;RIGHT QUOTE",
    0x2019: "APOSTROPHE;CURLY APOSTROPHE", 0xA0: "NBSP;NON-BREAKING SPACE", 0x200B: "ZWSP;ZERO WIDTH SPACE",
}


def build_chars(offline):
    chars = {}  # cp -> dict(name, aliases[list], gc)
    ranges = []
    first = None
    for line in open(fetch("UnicodeData.txt", offline), encoding="utf-8"):
        f = line.rstrip("\n").split(";")
        cp, name, gc = int(f[0], 16), f[1], f[2]
        if name.endswith(", First>"):
            first = (cp, name[1:-8])
            continue
        if name.endswith(", Last>"):
            start, label = first
            assert label == name[1:-7], (label, name)
            if label.startswith("Hangul Syllable"):
                for c in range(start, cp + 1):
                    chars[c] = {"name": hangul_name(c), "aliases": [], "gc": gc}
                continue
            if any(k in label for k in SKIPPED_RANGES):
                continue
            prefix = next((p for pat, p in RANGE_PREFIX if re.fullmatch(pat, label)), None)
            if prefix is None:
                sys.exit(f"unknown range label {label!r}: add its name prefix to RANGE_PREFIX")
            ranges.append((start, cp, prefix, gc))
            continue
        chars[cp] = {"name": "" if name.startswith("<") else name, "aliases": [], "gc": gc}

    # formal aliases: corrections and control names first (they become the name of <control> characters)
    for line in open(fetch("NameAliases.txt", offline), encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        cp, alias, typ = line.strip().split(";")
        cp = int(cp, 16)
        if cp not in chars:
            continue
        c = chars[cp]
        if typ == "abbreviation" and len(alias) < len(c.get("abbr") or alias + "x"):
            c["abbr"] = alias
        if not c["name"] and typ in ("control", "figment"):
            c["name"] = alias
        elif alias != c["name"] and alias not in c["aliases"]:
            c["aliases"].append(alias)

    # informative aliases from NamesList ("= …" lines)
    cur = None
    for line in open(fetch("NamesList.txt", offline), encoding="utf-8"):
        m = re.match(r"^([0-9A-F]{4,6})\t", line)
        if m:
            cur = int(m.group(1), 16)
            continue
        if line.startswith("@"):
            cur = None
            continue
        if cur in chars and line.startswith("\t= "):
            for a in line[3:].strip().split(", "):
                a = fold(a)
                if a and a != chars[cur]["name"] and a not in chars[cur]["aliases"]:
                    chars[cur]["aliases"].append(a)

    # emoji: CLDR short names for single code points (as aliases) and sequences (as their own rows)
    seqs = []
    for line in open(fetch("emoji-test.txt", offline), encoding="utf-8"):
        m = re.match(r"^([0-9A-F ]+?)\s*;\s*fully-qualified\s*#\s*\S+\s+E[\d.]+\s+(.+)$", line)
        if not m:
            continue
        cps = [int(x, 16) for x in m.group(1).split()]
        cldr = fold(m.group(2))
        base = [c for c in cps if c != 0xFE0F]
        if len(base) == 1 and base[0] in chars:
            c = chars[base[0]]
            if cldr != c["name"] and cldr not in c["aliases"]:
                c["aliases"].append(cldr)
            continue
        if any(0x1F3FB <= c <= 0x1F3FF for c in cps):
            continue  # skin tone variants: the default-coloured emoji covers the search
        seqs.append((cps, cldr))

    # HTML named character references: the preferred (lower-case, then shortest) name per character
    ents = {}
    for k, v in html.entities.html5.items():
        if not k.endswith(";") or len(v) != 1:
            continue
        k = k[:-1]
        cp = ord(v)
        best = ents.get(cp)
        rank = lambda n: (n != n.lower(), len(n), n)
        if best is None or rank(k) < rank(best):
            ents[cp] = k

    for cp, names in COMMON_NAMES.items():
        c = chars[cp]
        keys = [k for k in [c.get("abbr", "")] + names.split(";") if k]
        c["abbr"] = ";".join(dict.fromkeys(keys))

    # names derived by rule NR2 that UnicodeData lists one by one (CJK compatibility ideographs, Egyptian
    # hieroglyphs, Khitan, Nushu…): runs of them become ranges, so the file stays small
    rows = []
    for cp in sorted(chars):
        c = chars[cp]
        m = re.fullmatch(r"(.+-)([0-9A-F]{4,6})", c["name"])
        if m and int(m.group(2), 16) == cp and not c["aliases"] and not c.get("abbr"):
            last = ranges[-1] if ranges and ranges[-1][4:] == ("explicit",) else None
            if last and last[1] == cp - 1 and last[2] == m.group(1) and last[3] == c["gc"]:
                ranges[-1] = (last[0], cp, last[2], last[3], "explicit")
            else:
                ranges.append((cp, cp, m.group(1), c["gc"], "explicit"))
            continue
        if not c["name"]:
            continue
        rows.append((f"{cp:x}", c["name"], c["aliases"], c["gc"], ents.get(cp, ""), c.get("abbr", "")))
    for cps, cldr in seqs:
        rows.append((" ".join(f"{c:x}" for c in cps), cldr, [], "Emoji", "", ""))

    for r in rows:
        for field in (r[1], *r[2]):
            assert re.fullmatch(r"[\x20-\x3a\x3c-\x60\x7b-\x7e]+", field), (r[0], field)  # ASCII, no lower case or ";"
    ranges = sorted((r[0], r[1], r[2], r[3]) for r in ranges)
    return rows, ranges


def build_blocks(offline):
    out = []
    for line in open(fetch("Blocks.txt", offline), encoding="utf-8"):
        m = re.match(r"^([0-9A-F]+)\.\.([0-9A-F]+); (.+)$", line.strip())
        if m:
            out.append((int(m.group(1), 16), int(m.group(2), 16), m.group(3)))
    return out


# ---------- kaomoji ----------
KAO_CATEGORIES = [
    ("Table Flip", "flip table flipping"),
    ("Shrug", "shrug whatever idk dunno"),
    ("Lenny", "lenny"),
    ("Animals", "bear cat dog rabbit bunny pig bird fish monkey animal owl spider pet mouse seal whale "
                "chicken cow sheep penguin frog squid crab bat fox koala panda snake bug butterfly horse animals "
                "seacreatures sea octopus"),
    ("Love", "love heart kiss chu adore crush"),
    ("Joy", "happy joy smile excited fun laugh glad yay cheer yes celebrate joyful cute"),
    ("Sadness", "sad cry crying tears depressed upset sob unhappy"),
    ("Anger", "angry mad rage annoyed furious grr hate"),
    ("Surprise", "surprise surprised shock shocked omg wow scared afraid fear gasp"),
    ("Confusion", "confused doubt wonder what hmm puzzled unsure crazy"),
    ("Embarrassment", "embarrassed embarassed shy blush blushing nervous awkward sweat hide"),
    ("Worry & Pain", "worried hurt pain dead sorry surrender"),
    ("Thinking", "think stare wink"),
    ("Greeting", "wave hello hi bye greet greeting salute bow"),
    ("Hugs", "hug hugs cuddle"),
    ("Dance & Music", "dance dancing music sing song party"),
    ("Running", "run running flee chase"),
    ("Sleep", "sleep sleepy tired bed nap"),
    ("Writing", "write writing note"),
    ("Evil & Troll", "evil troll devil sinister smug"),
    ("Fighting", "fight gun shoot sword punch kill war weapon battle attack sniper"),
    ("Food & Drink", "food eat eating drink coffee tea beer cake"),
]
KAO_SKIP_WORDS = {"boobs", "butt", "blow", "penis", "dick"}  # a few crude entries are left out
EMOTICONS = [
    (":)", "smile happy"), (":-)", "smile happy nose"), (":D", "grin laugh happy"), (":-D", "grin laugh happy"),
    ("XD", "laugh lol"), (";)", "wink"), (";-)", "wink"), (":P", "tongue playful"), (":-P", "tongue playful"),
    (":(", "sad frown"), (":-(", "sad frown"), (":'(", "cry sad tears"), (":O", "surprise shock"),
    (":-O", "surprise shock"), (":|", "neutral meh"), (":/", "skeptical unsure"), (":S", "confused worried"),
    (":*", "kiss"), (":$", "embarrassed blush"), (">:(", "angry"), ("D:", "horror dismay"), (":3", "cute cat"),
    ("<3", "heart love"), ("</3", "broken heart"), ("B)", "cool sunglasses"), ("8-)", "cool glasses"),
    ("O:)", "angel innocent"), (">:)", "evil devil"), ("^_^", "happy smile"), ("^^", "happy smile"),
    ("-_-", "annoyed unamused"), ("o_O", "confused surprised"), ("O_o", "confused surprised"),
    (">_<", "frustrated"), ("T_T", "cry sad"), ("=)", "smile happy"), ("=D", "grin happy"), (":-*", "kiss"),
    ("\\o/", "yay cheer celebrate"), ("o/", "wave hello"), ("¯\\_(ツ)_/¯", "shrug whatever"),
]


def build_kaomoji(offline):
    lib = json.load(open(fetch("asciilib.json", offline), encoding="utf-8"))
    cats = [(name, set(words.split())) for name, words in KAO_CATEGORIES]
    out, seen = [], set()
    for text, words in EMOTICONS:
        out.append((text, "Emoticons", words))
        seen.add(text)
    for key in sorted(lib):
        e = lib[key]
        text = e["entry"].strip()
        words = []
        # the source's keywords, plus the words of the name and key when they are separated
        # (keys like "crazyshrugwhatever" are run together and would only add noise)
        extra = e["name"].lower().split() if " " in e["name"] else []
        extra += re.sub(r"_\d+$", "", key).split("_") if "_" in key else []
        for w in e.get("keywords", []) + extra:
            w = re.sub(r"[^a-z0-9]", "", w.lower())
            if w and not w.isdigit() and w not in words and len(w) > 1:
                words.append(w)
        if not text or text in seen or "\t" in text or "\n" in text or KAO_SKIP_WORDS & set(words):
            continue
        seen.add(text)
        cat = next((n for n, ws in cats if ws & set(words)), None)
        if cat is None:
            cat = {"animal": "Animals", "food": "Food & Drink", "emotion": "Joy"}.get(e.get("category"), "Other")
        out.append((text, cat, " ".join(words)))
    order = [n for n, _ in KAO_CATEGORIES] + ["Emoticons", "Other"]
    out.sort(key=lambda r: order.index(r[1]))  # stable: the source order within a category
    return out


# ---------- LaTeX ----------
# LaTeX 2e (fontmath, latexsym) commands; the rest are amssymb (from unicode.xml's AMS field) or unicode-math
BASE = set("""
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho
sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
pm mp times div cdot ast star circ bullet cap cup uplus sqcap sqcup vee wedge setminus wr diamond bigtriangleup
bigtriangledown triangleleft triangleright lhd rhd unlhd unrhd oplus ominus otimes oslash odot bigcirc dagger ddagger
amalg leq le geq ge prec succ preceq succeq ll gg subset supset subseteq supseteq sqsubset sqsupset sqsubseteq
sqsupseteq in ni owns vdash dashv equiv sim simeq asymp approx cong neq ne doteq propto models perp mid parallel
bowtie Join smile frown leftarrow gets rightarrow to Leftarrow Rightarrow leftrightarrow Leftrightarrow mapsto
hookleftarrow hookrightarrow leftharpoonup leftharpoondown rightharpoonup rightharpoondown rightleftharpoons
longleftarrow longrightarrow Longleftarrow Longrightarrow longleftrightarrow Longleftrightarrow longmapsto iff
uparrow downarrow updownarrow Uparrow Downarrow Updownarrow nearrow searrow swarrow nwarrow leadsto aleph hbar imath
jmath ell wp Re Im mho prime emptyset nabla surd top bot angle forall exists neg lnot flat natural sharp backslash
partial infty Box Diamond triangle clubsuit diamondsuit heartsuit spadesuit sum prod coprod int oint bigcap bigcup
bigsqcup bigvee bigwedge bigodot bigotimes bigoplus biguplus lfloor rfloor lceil rceil langle rangle vert Vert
ldots cdots vdots ddots colon lbrace rbrace lbrack rbrack dots
S P dag ddag copyright pounds
""".split())
LATEX_JUNK = re.compile(r"^\\(El|cyrchar|ding|Pisymbol|fontencoding|ElsevierGlyph|mathmit|mathsl|mathbit|mathsfbf|"
                        r"mathsfsl|mathsfbfsl|mathslbb|mathbcal|mathbfrak|mathbb\{[a-z]|tone|Mapsfrom|Mapsto|"
                        r"MapsTo|MapsFrom|textsuperscript|textsubscript|ensuremath|mathring|updownharpoon)")
MATH_ALPHA = re.compile(r"^\\(mathbb|mathfrak|mathcal|mathscr|mathbf|mathsf|mathtt|mathit)\{[A-Za-z0-9]\}$")


LATEX_RANK = {"LaTeX": 0, "amssymb": 1, "mathrsfs": 1, "text": 2, "other": 3, "unicode-math": 4}
TEXT_CMDS = {"\\S", "\\P", "\\dag", "\\ddag", "\\copyright", "\\pounds", "\\ss", "\\ae", "\\AE", "\\oe",
             "\\OE", "\\o", "\\O", "\\aa", "\\AA", "\\l", "\\L", "\\i", "\\j", "\\ldots", "\\dots"}


# Standard commands that unicode.xml lists under another name for the same character, and corrections
# (TeX's \\epsilon is the lunate ϵ, \\varepsilon the ε); these win over whatever unicode.xml says
LATEX_EXTRA = {
    "epsilon": 0x3F5, "varepsilon": 0x3B5, "to": 0x2192, "gets": 0x2190, "emptyset": 0x2205, "hbar": 0x210F,
    "vartheta": 0x3D1, "varkappa": 0x3F0, "digamma": 0x3DD, "Box": 0x25A1, "Diamond": 0x25C7, "lhd": 0x22B2,
    "rhd": 0x22B3, "unlhd": 0x22B4, "unrhd": 0x22B5, "leadsto": 0x21DD, "owns": 0x220B, "ne": 0x2260, "le": 0x2264,
    "ge": 0x2265, "iff": 0x27FA, "implies": 0x27F9, "impliedby": 0x27F8, "land": 0x2227, "lor": 0x2228,
    "neg": 0xAC, "nexists": 0x2204, "triangle": 0x25B3, "bigstar": 0x2605, "checkmark": 0x2713, "dagger": 0x2020,
    "ddagger": 0x2021, "star": 0x22C6, "ast": 0x2217, "vert": 0x7C, "Vert": 0x2016, "lbrack": 0x5B, "rbrack": 0x5D,
    "cdot": 0x22C5, "bullet": 0x2219, "circ": 0x2218, "div": 0xF7, "times": 0xD7, "pm": 0xB1, "mp": 0x2213,
    "infty": 0x221E, "partial": 0x2202, "nabla": 0x2207, "prime": 0x2032, "ell": 0x2113, "wp": 0x2118,
    "Re": 0x211C, "Im": 0x2111, "aleph": 0x2135, "complement": 0x2201, "mho": 0x2127, "angle": 0x2220,
}
LATEX_AMS_EXTRA = {"varkappa", "digamma", "checkmark", "bigstar", "nexists", "complement", "Box", "Diamond", "leadsto"}
LATEX_DROP = re.compile(r"^\\(u|v|k|nolinebreak|Alpha|Beta|Epsilon|Zeta|Eta|Iota|Kappa|Rho|Tau|Chi|Mu|Nu|Omicron|omicron)$")


def build_latex(offline):
    root = ET.parse(fetch("unicode.xml", offline)).getroot()
    chars = []
    for c in root.find("charlist"):
        m = re.fullmatch(r"U([0-9A-F]{5})", c.get("id", ""))
        if not m:
            continue
        cp = int(m.group(1), 16)
        d = c.find("description")
        name = fold(d.text) if d is not None and d.text else ""
        if name and not 0xE000 <= cp <= 0xF8FF:
            chars.append((c, cp, name))
    ams = {(e.text or "").strip() for c, _, _ in chars for e in c.findall("AMS")}
    found = {}  # command -> (cp, package, name)

    def add(cmd, cp, pkg, name):
        cmd = cmd.strip()
        if not (re.fullmatch(r"\\[A-Za-z]+", cmd) or MATH_ALPHA.match(cmd)) or LATEX_JUNK.match(cmd) \
                or LATEX_DROP.match(cmd) or cmd[1:] in LATEX_EXTRA:
            return
        if cmd[1:] in BASE:
            pkg = "LaTeX"
        elif MATH_ALPHA.match(cmd):
            pkg = {"\\mathbb": "amssymb", "\\mathfrak": "amssymb", "\\mathscr": "mathrsfs"}.get(cmd.split("{")[0], "LaTeX")
        elif cmd in ams:
            pkg = "amssymb"
        prev = found.get(cmd)
        if prev is None or LATEX_RANK[pkg] < LATEX_RANK[prev[1]]:
            found[cmd] = (cp, pkg, name)

    for c, cp, name in chars:
        for e in c.findall("AMS"):
            add(e.text or "", cp, "amssymb", name)
        e = c.find("latex")
        if e is not None and e.text:
            t = e.text.strip()
            if t.startswith("\\text") or t in TEXT_CMDS:
                add(t, cp, "text", name)
            elif c.get("mode") != "text":
                add(t, cp, "other", name)
        for e in c.findall("mathlatex"):
            if e.get("set") == "unicode-math":
                add(e.text or "", cp, "unicode-math", name)
    names = {cp: name for _, cp, name in chars}
    for bare, cp in LATEX_EXTRA.items():
        found["\\" + bare] = (cp, "amssymb" if bare in LATEX_AMS_EXTRA else "LaTeX", names[cp])
    return sorted(((cmd, f"{cp:x}", pkg, name) for cmd, (cp, pkg, name) in found.items()),
                  key=lambda r: (LATEX_RANK[r[2]], int(r[1], 16), r[0]))


# ---------- typography catalogue (the characters a careful writer reaches for, with when to use them) ----------
TYPOGRAPHY = [
    ("\u201c", "Left double quotation mark", "Opens a quotation in English: \u201cHello\u201d", "quote curly smart open"),
    ("\u201d", "Right double quotation mark", "Closes a quotation in English; opens and closes in Swedish", "quote curly smart close"),
    ("\u2018", "Left single quotation mark", "Opens a quote within a quote, or a British quotation", "quote curly smart open single"),
    ("\u2019", "Right single quotation mark (apostrophe)", "The apostrophe: it\u2019s, don\u2019t, \u201990s", "apostrophe quote curly smart close single"),
    ("\u201e", "Double low-9 quotation mark", "Opens a quotation in German, Polish, Czech: \u201eHallo\u201c", "quote german low"),
    ("\u201a", "Single low-9 quotation mark", "Opens a quote within a quote in German", "quote german low single"),
    ("\u00ab", "Left-pointing double angle quotation mark", "Guillemet: opens a quotation in French (\u00ab\u202fBonjour\u202f\u00bb)", "guillemet quote french angle"),
    ("\u00bb", "Right-pointing double angle quotation mark", "Guillemet: closes a quotation in French; opens one in German books", "guillemet quote french angle"),
    ("\u2039", "Single left-pointing angle quotation mark", "Opens a quote within a quote in French and Swiss German", "guillemet quote single angle"),
    ("\u203a", "Single right-pointing angle quotation mark", "Closes a quote within a quote in French and Swiss German", "guillemet quote single angle"),
    ("\u300c", "Left corner bracket", "Opens a quotation in Japanese and Chinese", "quote japanese chinese"),
    ("\u300d", "Right corner bracket", "Closes a quotation in Japanese and Chinese", "quote japanese chinese"),
    ("\u2032", "Prime", "Feet, minutes and arcminutes: 6\u2032, 45\u2032", "feet minutes prime"),
    ("\u2033", "Double prime", "Inches, seconds and arcseconds: 6\u2032 2\u2033", "inches seconds prime"),
    ("\u2013", "En dash", "Ranges and connections: 9\u201317, pages 4\u20138, London\u2013Paris", "dash range ndash"),
    ("\u2014", "Em dash", "A break in a sentence\u2014like this (no spaces in US style)", "dash break mdash"),
    ("\u2012", "Figure dash", "Between digits of the same width: 555\u20121234", "dash figure phone"),
    ("\u2015", "Horizontal bar", "Introduces a quotation or dialogue in some styles", "dash quotation bar"),
    ("\u2010", "Hyphen", "An unambiguous hyphen (the keyboard - is a hyphen-minus)", "hyphen"),
    ("\u2011", "Non-breaking hyphen", "A hyphen that never starts a new line: e\u2011mail", "hyphen nonbreaking nbhy"),
    ("\u00ad", "Soft hyphen", "An invisible hint where a long word may break", "hyphen soft shy invisible"),
    ("\u2212", "Minus sign", "Mathematics and negative numbers: \u22125 \u00b0C", "minus math"),
    ("\u2026", "Horizontal ellipsis", "Omission or trailing thought\u2026 (one character, not three dots)", "ellipsis dots hellip"),
    ("\u00a0", "No-break space", "Keeps two words on one line: 10\u00a0km, Mr\u00a0Smith", "space nbsp nonbreaking"),
    ("\u202f", "Narrow no-break space", "Before French punctuation and between thousands: 10\u202f000", "space narrow nonbreaking thin french"),
    ("\u2009", "Thin space", "A fifth of an em: around dashes or in numbers", "space thin"),
    ("\u200a", "Hair space", "The thinnest space, for fine kerning", "space hair"),
    ("\u2002", "En space", "Half an em wide", "space en"),
    ("\u2003", "Em space", "As wide as the font size", "space em"),
    ("\u2007", "Figure space", "As wide as a digit: aligns numbers in columns", "space figure digit"),
    ("\u200b", "Zero width space", "An invisible line-break opportunity (in URLs or long words)", "space zero width zwsp invisible"),
    ("\u2060", "Word joiner", "Invisibly prevents a line break", "joiner invisible wj"),
    ("\u200d", "Zero width joiner", "Joins emoji into sequences and shapes some scripts", "joiner zwj invisible emoji"),
    ("\u200c", "Zero width non-joiner", "Prevents a ligature or joining", "nonjoiner zwnj invisible"),
    ("\u2022", "Bullet", "List items and separators \u2022 like this", "bullet list dot"),
    ("\u00b7", "Middle dot", "Separators in lists and Catalan l\u00b7l", "dot middle interpunct separator"),
    ("\u2023", "Triangular bullet", "An alternative list bullet", "bullet triangle"),
    ("\u2043", "Hyphen bullet", "A dash-like list bullet", "bullet hyphen"),
    ("\u00a9", "Copyright sign", "\u00a9 2026 Name", "copyright"),
    ("\u00ae", "Registered sign", "A registered trademark\u00ae", "registered trademark"),
    ("\u2122", "Trade mark sign", "An unregistered trademark\u2122", "trademark tm"),
    ("\u2117", "Sound recording copyright", "\u2117 on audio recordings", "phonogram copyright"),
    ("\u00a7", "Section sign", "Legal sections: \u00a7\u00a04", "section legal"),
    ("\u00b6", "Pilcrow sign", "Paragraphs", "paragraph pilcrow"),
    ("\u2020", "Dagger", "A second footnote, or a date of death", "dagger footnote obelisk"),
    ("\u2021", "Double dagger", "A third footnote", "dagger double footnote"),
    ("\u2030", "Per mille sign", "Parts per thousand: 5\u2030", "permille per thousand"),
    ("\u00b0", "Degree sign", "Temperatures and angles: 21\u00b0C, 90\u00b0", "degree temperature"),
    ("\u00d7", "Multiplication sign", "Dimensions and multiplication: 1920\u00d71080, 3\u00a0\u00d7\u00a04", "times multiplication dimensions"),
    ("\u00f7", "Division sign", "Division", "division divide"),
    ("\u00b1", "Plus-minus sign", "Tolerances: \u00b10.5\u00a0mm", "plus minus tolerance"),
    ("\u2260", "Not equal to", "Inequality", "not equal"),
    ("\u2248", "Almost equal to", "Approximations: \u2248\u00a03\u00a0km", "approximately almost equal"),
    ("\u2264", "Less-than or equal to", "Comparisons", "less equal"),
    ("\u2265", "Greater-than or equal to", "Comparisons", "greater equal"),
    ("\u00bd", "Vulgar fraction one half", "\u00bd cup", "fraction half"),
    ("\u00bc", "Vulgar fraction one quarter", "\u00bc hour", "fraction quarter"),
    ("\u00be", "Vulgar fraction three quarters", "\u00be full", "fraction three quarters"),
    ("\u2044", "Fraction slash", "Builds any fraction: 5\u20448", "fraction slash"),
    ("\u2116", "Numero sign", "Numbers in some languages: \u2116\u00a05", "number numero"),
    ("\u00aa", "Feminine ordinal indicator", "Spanish and Portuguese ordinals: 1\u00aa", "ordinal feminine"),
    ("\u00ba", "Masculine ordinal indicator", "Spanish and Portuguese ordinals: 1\u00ba", "ordinal masculine"),
    ("\u00bf", "Inverted question mark", "Opens a Spanish question: \u00bfQu\u00e9?", "question inverted spanish"),
    ("\u00a1", "Inverted exclamation mark", "Opens a Spanish exclamation: \u00a1Hola!", "exclamation inverted spanish"),
    ("\u203d", "Interrobang", "A question and exclamation in one\u203d", "interrobang question exclamation"),
    ("\u2192", "Rightwards arrow", "Directions and flows: Settings \u2192 General", "arrow right"),
    ("\u2190", "Leftwards arrow", "Back, or the opposite direction", "arrow left"),
    ("\u2318", "Place of interest sign", "The Command key: \u2318C", "command key mac"),
    ("\u2325", "Option key", "The Option key: \u2325\u2318I", "option alt key mac"),
    ("\u21e7", "Upwards white arrow", "The Shift key: \u21e7\u2318Z", "shift key mac"),
    ("\u2303", "Up arrowhead", "The Control key: \u2303C", "control ctrl key mac"),
    ("\u21a9", "Leftwards arrow with hook", "The Return key: \u21a9", "return enter key mac"),
    ("\u232b", "Erase to the left", "The Delete key", "delete backspace key mac"),
    ("\u238b", "Broken circle with northwest arrow", "The Escape key", "escape esc key mac"),
    ("\u20ac", "Euro sign", "12\u00a0\u20ac or \u20ac12, depending on the language", "currency euro"),
    ("\u00a3", "Pound sign", "\u00a312", "currency pound sterling"),
    ("\u00a5", "Yen sign", "\u00a51,200", "currency yen yuan"),
    ("\u00a2", "Cent sign", "99\u00a2", "currency cent"),
]


def build_typography():
    ents = {}
    for k, v in html.entities.html5.items():
        if k.endswith(";") and len(v) == 1:
            k = k[:-1]
            if ord(v) not in ents or (k != k.lower(), len(k)) < (ents[ord(v)] != ents[ord(v)].lower(), len(ents[ord(v)])):
                ents[ord(v)] = k
    out, seen = [], set()
    for ch, name, usage, words in TYPOGRAPHY:
        if ch in seen:
            continue
        seen.add(ch)
        out.append("\t".join((ch, f"{ord(ch):x}", name, usage, words, ents.get(ord(ch), ""))))
    return out


def build_entities():
    out = {}
    for k, v in html.entities.html5.items():
        if k.endswith(";") and 1 <= len(v) <= 2:
            out[k[:-1]] = " ".join(f"{ord(c):x}" for c in v)
    return [f"{k}\t{v}" for k, v in sorted(out.items())]


def write(name, lines):
    path = os.path.join(OUT, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(l + "\n" for l in lines))
    os.replace(tmp, path)
    return path


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    offline = "--offline" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    rows, ranges = build_chars(offline)
    blocks = build_blocks(offline)
    kao = build_kaomoji(offline)
    latex = build_latex(offline)
    write("chars.txt", ["\t".join((h, n, ";".join(a), gc, e, ab)) for h, n, a, gc, e, ab in rows])
    write("ranges.txt", [f"{s:x}\t{e:x}\t{p}\t{gc}" for s, e, p, gc in ranges])
    write("blocks.txt", [f"{s:x}\t{e:x}\t{n}" for s, e, n in blocks])
    write("kaomoji.txt", ["\t".join(r) for r in kao])
    write("latex.txt", ["\t".join(r) for r in latex])
    write("typography.txt", build_typography())
    write("entities.txt", build_entities())
    in_ranges = sum(e - s + 1 for s, e, _, _ in ranges)
    singles = sum(1 for r in rows if " " not in r[0])
    meta = {
        "unicode": UNICODE_VERSION,
        "characters": singles + in_ranges,
        "named_rows": singles,
        "rule_named": in_ranges,
        "emoji_sequences": len(rows) - singles,
        "kaomoji": len(kao),
        "latex": len(latex),
        "sources": {k: {"url": v, "sha256": sha(os.path.join(CACHE, k))} for k, v in SOURCES.items()},
    }
    with open(os.path.join(OUT, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
        f.write("\n")
    print(json.dumps({k: v for k, v in meta.items() if k != "sources"}))


if __name__ == "__main__":
    main()
