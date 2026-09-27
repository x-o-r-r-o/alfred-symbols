#!/usr/bin/env python3
"""End-to-end tests: run the Script Filters and actions the way Alfred does and validate the output.

They never read the real clipboard or touch Alfred: SYM_TEST_MODE=1 makes the clipboard come from
SYM_TEST_CLIPBOARD (empty when unset) and turns "open in Alfred" into printed text.
"""
import json, os, plistlib, re, shutil, subprocess, sys, tempfile, time, unicodedata, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DATA = os.path.join(SRC, "data")
# Like Alfred's real folders: paths with spaces
BASE = tempfile.mkdtemp(prefix="symbols-test-")
CACHE = os.path.join(BASE, "Caches", "com.runningwithcrayons.Alfred", "Workflow Data", "io.github.x-o-r-r-o.symbols")
DATADIR = os.path.join(BASE, "Application Support", "Alfred", "Workflow Data", "io.github.x-o-r-r-o.symbols")


def run_env(**env):
    e = dict(os.environ, SYM_TEST_MODE="1", alfred_workflow_cache=CACHE, alfred_workflow_data=DATADIR)
    e.update({k: v for k, v in env.items()})
    return e


def raw(cmd, query="", **env):
    out = subprocess.run(["osascript", "-l", "JavaScript", "./symbols.js", cmd, query], cwd=SRC, env=run_env(**env),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def sf(cmd, query="", clipboard="", full=False, **env):
    data = json.loads(raw(cmd, query, SYM_TEST_CLIPBOARD=clipboard, **env))
    validate(data)
    return data if full else data["items"]


def validate(data):
    assert isinstance(data.get("items"), list)
    uids = set()
    for it in data["items"]:
        assert isinstance(it.get("title"), str) and it["title"], it
        if "icon" in it:
            p = it["icon"]["path"]
            assert os.path.exists(p if p.startswith("/") else os.path.join(SRC, p)), it["icon"]
        if it.get("valid", True) is not False:
            assert "arg" in it, it
        for m in (it.get("mods") or {}).values():
            assert "subtitle" in m and "arg" in m, it
        if "uid" in it:
            assert it["uid"] not in uids, it["uid"]
            uids.add(it["uid"])
        if "quicklookurl" in it:
            assert os.path.exists(it["quicklookurl"]), it["quicklookurl"]


def act(arg, rec=None, **env):
    e = run_env(**env)
    e.pop("sym_rec", None)
    if rec is not None:
        e["sym_rec"] = rec
    out = subprocess.run(["./act.sh", arg], cwd=SRC, env=e, capture_output=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return out.stdout.decode("utf-8")


def titles(items):
    return [i["title"] for i in items]


def args(items):
    return [i.get("arg") for i in items if i.get("valid", True) is not False]


def reset_data():
    shutil.rmtree(DATADIR, ignore_errors=True)


def read_chars():
    rows = []
    with open(os.path.join(DATA, "chars.txt"), encoding="utf-8") as f:
        for line in f:
            rows.append(line.rstrip("\n").split("\t"))
    return rows


class DataTests(unittest.TestCase):
    def test_chars_file_format(self):
        rows = read_chars()
        seen = set()
        last = -1
        for f in rows:
            self.assertEqual(len(f), 6, f)
            self.assertRegex(f[0], r"^[0-9a-f]{1,6}( [0-9a-f]{1,6})*$")
            self.assertNotIn(f[0], seen)
            seen.add(f[0])
            self.assertRegex(f[1], r"^[\x20-\x3a\x3c-\x60\x7b-\x7e]+$")  # upper-case ASCII, no ";"
            for a in filter(None, f[2].split(";")):
                self.assertRegex(a, r"^[\x20-\x3a\x3c-\x60\x7b-\x7e]+$")
            self.assertIn(f[3], set("Lu Ll Lt Lm Lo Mn Mc Me Nd Nl No Pc Pd Ps Pe Pi Pf Po Sm Sc Sk So Zs Zl Zp Cc Cf Emoji".split()))
            if " " not in f[0]:
                cp = int(f[0], 16)
                self.assertGreater(cp, last)  # sorted, single code points first
                last = cp
                self.assertFalse(0xD800 <= cp <= 0xDFFF or 0xE000 <= cp <= 0xF8FF)

    def test_names_match_python_unicodedata(self):
        # Names never change between Unicode versions, so every name Python knows must be identical
        ours = {int(f[0], 16): f[1] for f in read_chars() if " " not in f[0]}
        mismatches, checked = [], 0
        for cp, name in ours.items():
            py = unicodedata.name(chr(cp), None)
            if py is None:
                continue
            checked += 1
            if py != name:
                mismatches.append((hex(cp), py, name))
        self.assertGreater(checked, 30000)
        self.assertEqual(mismatches, [])

    def test_python_characters_are_all_present(self):
        # Every named character Python knows is either a row or inside a rule-named range
        ours = {int(f[0], 16) for f in read_chars() if " " not in f[0]}
        ranges = []
        with open(os.path.join(DATA, "ranges.txt")) as f:
            for line in f:
                s, e, prefix, gc = line.rstrip("\n").split("\t")
                ranges.append((int(s, 16), int(e, 16), prefix))
        missing = []
        for cp in range(0x110000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            name = unicodedata.name(chr(cp), None)
            if name is None or cp in ours:
                continue
            r = next((r for r in ranges if r[0] <= cp <= r[1]), None)
            if r is None or name != r[2] + "%04X" % cp:
                missing.append((hex(cp), name))
        self.assertEqual(missing[:20], [])

    def test_counts_match_meta(self):
        with open(os.path.join(DATA, "meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["unicode"], "18.0.0")
        rows = read_chars()
        singles = sum(1 for f in rows if " " not in f[0])
        self.assertEqual(meta["named_rows"], singles)
        self.assertEqual(meta["emoji_sequences"], len(rows) - singles)
        self.assertGreater(meta["characters"], 150000)
        self.assertEqual(meta["characters"], meta["named_rows"] + meta["rule_named"])

    def test_blocks_sorted_and_disjoint(self):
        prev = -1
        with open(os.path.join(DATA, "blocks.txt")) as f:
            for line in f:
                s, e, name = line.rstrip("\n").split("\t")
                s, e = int(s, 16), int(e, 16)
                self.assertGreater(s, prev)
                self.assertLessEqual(s, e)
                prev = e

    def test_latex_file(self):
        cmds = set()
        with open(os.path.join(DATA, "latex.txt"), encoding="utf-8") as f:
            for line in f:
                cmd, h, pkg, name = line.rstrip("\n").split("\t")
                self.assertNotIn(cmd, cmds)
                cmds.add(cmd)
                self.assertRegex(cmd, r"^\\([A-Za-z]+|math[a-z]+\{[A-Za-z0-9]\})$")
                self.assertLessEqual(int(h, 16), 0x10FFFF)
                self.assertIn(pkg, {"LaTeX", "amssymb", "mathrsfs", "text", "other", "unicode-math"})
        for c in [r"\alpha", r"\leq", r"\infty", r"\sum", r"\to", r"\mathbb{R}", r"\varepsilon", r"\in", r"\forall"]:
            self.assertIn(c, cmds)

    def test_kaomoji_file(self):
        seen = set()
        with open(os.path.join(DATA, "kaomoji.txt"), encoding="utf-8") as f:
            for line in f:
                text, cat, words = line.rstrip("\n").split("\t")
                self.assertNotIn(text, seen)
                seen.add(text)
                self.assertTrue(cat)
                self.assertRegex(words, r"^[a-z0-9 ]*$")
        self.assertGreater(len(seen), 1500)

    def test_entities_and_typography_files(self):
        with open(os.path.join(DATA, "entities.txt"), encoding="utf-8") as f:
            ents = dict(l.rstrip("\n").split("\t") for l in f)
        self.assertEqual(ents["rarr"], "2192")
        self.assertEqual(ents["amp"], "26")
        with open(os.path.join(DATA, "typography.txt"), encoding="utf-8") as f:
            typo = f.read().splitlines(True)
        for line in typo:
            f = line.rstrip("\n").split("\t")
            self.assertEqual(len(f), 6)
            self.assertEqual(ord(f[0]), int(f[1], 16))


class RoundTripTests(unittest.TestCase):
    def test_code_point_lookup_round_trip(self):
        # every notation of a code point gives back that exact character
        for cp in [0x41, 0xE9, 0x2192, 0x1F600, 0x10348, 0x1D400, 0x4E00, 0xAC00, 0x2F800, 0x13460]:
            for q in ["U+%04X" % cp, "u+%04x" % cp, "\\u{%X}" % cp, "0x%X" % cp, "&#x%X;" % cp, "&#%d;" % cp]:
                it = sf("uni", q)[0]
                self.assertEqual(it["arg"], chr(cp), (q, it))
                self.assertIn("U+%04X" % cp, it["subtitle"])

    def test_rule_named_and_algorithmic_names(self):
        self.assertIn("CJK UNIFIED IDEOGRAPH-4E00", sf("uni", "U+4E00")[0]["title"].upper())
        self.assertIn("Hangul Syllable GA".upper(), sf("uni", "U+AC00")[0]["title"].upper())
        self.assertIn("HANGUL SYLLABLE HIH", sf("uni", "U+D7A3")[0]["title"].upper())
        self.assertIn("SMALL SEAL CHARACTER-3D000", sf("uni", "U+3D000")[0]["title"].upper())
        self.assertIn("TANGUT IDEOGRAPH-17000", sf("uni", "U+17000")[0]["title"].upper())

    def test_inspect_round_trip(self):
        # a long mixed string: every row's arg is the code point it names, in order
        cps = [0x41, 0x20, 0xA0, 0x2192, 0x1F600, 0x1D400, 0x4E00, 0x301, 0x10348, 0x200D, 0xFEFF, 0x1F1EB, 0xE9, 0x1F44D]
        text = "".join(chr(c) for c in cps)
        items = sf("chars", text)
        rows = [i for i in items if i.get("uid", "").split("|")[-1].startswith("u:")]
        self.assertEqual(len(rows), len(cps))
        for c, row in zip(cps, rows):
            self.assertEqual(row["arg"], chr(c))
            self.assertIn("U+%04X" % c, row["subtitle"] + row.get("mods", {}).get("alt", {}).get("subtitle", "") + "U+%04X" % c)
        self.assertIn("14 code points", items[0]["title"])
        self.assertIn("3 invisible", items[0]["subtitle"])  # NBSP, ZWJ, BOM

    def test_sample_of_all_rows_round_trips(self):
        rows = [f for f in read_chars() if " " not in f[0]][1::997]  # (U+0000 can't be an argument)
        text = "".join(chr(int(f[0], 16)) for f in rows)
        items = sf("chars", text)
        got = [i for i in items if i.get("uid", "").split("|")[-1].startswith("u:")]
        self.assertEqual(len(got), len(rows))
        for f, row in zip(rows, got):
            self.assertEqual(row["uid"].split("|")[-1], "u:" + f[0])

    def test_surrogates_and_nul(self):
        it = sf("uni", "U+D800")[0]
        self.assertFalse(it["valid"])
        self.assertIn("SURROGATE", it["title"])
        self.assertIn("Can’t", it["subtitle"])
        it = sf("uni", "U+0000")[0]
        self.assertFalse(it["valid"])  # NUL can't pass through argv
        it = sf("uni", "U+110000")
        self.assertNotEqual(it[0].get("arg"), "")
        # astral characters stay whole in titles, args and escapes
        it = sf("chars", "😀")
        self.assertEqual(it[0]["arg"], "😀")
        fmts = {i["subtitle"].split(" · ")[0]: i["arg"] for i in it[1:] if i.get("valid", True)}
        self.assertEqual(fmts["UTF-16 escape (JavaScript, JSON, Java, C#)"], "\\uD83D\\uDE00")
        self.assertEqual(fmts["\\u{…} escape (JavaScript, Swift, Rust, PHP)"], "\\u{1F600}")
        self.assertEqual(fmts["UTF-8 bytes"], "F0 9F 98 80")
        self.assertEqual(fmts["UTF-16 code units"], "D83D DE00")
        self.assertEqual(fmts["URL encoded"], "%F0%9F%98%80")
        self.assertEqual(fmts["Python escape"], "\\N{GRINNING FACE}")

    def test_unassigned_and_private_use(self):
        it = sf("uni", "U+E000")[0]
        self.assertIn("PRIVATE USE", it["title"])
        it = sf("uni", "U+0378")[0]
        self.assertIn("UNASSIGNED", it["title"])
        self.assertFalse(it["valid"])
        it = sf("uni", "U+FFFF")[0]
        self.assertIn("NONCHARACTER", it["title"])

    def test_emoji_sequences(self):
        it = sf("chars", "👨‍👩‍👧")
        self.assertEqual(it[0]["arg"], "👨‍👩‍👧")
        self.assertIn("Family", it[0]["title"])
        self.assertIn("U+1F468 U+200D U+1F469", it[0]["subtitle"])
        it = sf("uni", "flag france")[0]
        self.assertEqual(it["arg"], "🇫🇷")


class SearchTests(unittest.TestCase):
    def setUp(self):
        reset_data()

    def first(self, q, **env):
        return sf("uni", q, **env)[0]

    def test_ranking(self):
        cases = {"a": "a", "x": "x", "pi": None, "right arrow": None, "em dash": "—", "nbsp": "\u00a0", "euro": "€",
                 "&rarr;": "→", "2192": "→", "check mark": "✓", "degree": "°", "copyright": "©", "infinity": "∞",
                 "bullet": "•", "omega": "ω", "e acute": "é", "latin small letter": "a", "pilcrow": "¶",
                 "grinning face": "😀", "red heart": "❤", "zwj": "\u200d", "section": "§"}
        for q, want in cases.items():
            items = sf("uni", q)
            if want is not None:
                self.assertEqual(items[0]["arg"], want, (q, titles(items[:5])))
        self.assertIn("π", args(sf("uni", "pi"))[:3])
        self.assertIn("→", args(sf("uni", "right arrow"))[:3])
        self.assertIn("A", args(sf("uni", "a"))[:4])

    def test_aliases_and_words(self):
        self.assertEqual(self.first("have a nice day")["arg"], "☺")  # an informal alias
        self.assertEqual(self.first("bom")["arg"], "\ufeff")  # an abbreviation
        self.assertIn("→", args(sf("uni", "rightwards arr")))  # word prefixes
        items = sf("uni", "arrow right")  # any order
        self.assertIn("→", args(items)[:5])

    def test_no_results_and_limits(self):
        it = sf("uni", "zzzzqqq")
        self.assertEqual(len(it), 1)
        self.assertFalse(it[0]["valid"])
        self.assertLessEqual(len(sf("uni", "l")), 55)
        self.assertLessEqual(len(sf("uni", "l", max_results="30")), 35)
        # padded, empty and junk config values fall back to the default
        for v in [" 30 ", "", "abc", "0", "99999"]:
            self.assertTrue(10 <= len(sf("uni", "letter", max_results=v)) <= 205)

    def test_entities(self):
        self.assertEqual(self.first("&hearts;")["arg"], "♥")
        self.assertEqual(self.first("&Alpha")["arg"], "Α")
        it = self.first("&nosuchentity;")
        self.assertFalse(it["valid"])
        self.assertIn("No HTML entity", it["title"])

    def test_empty_query_hints(self):
        it = sf("uni", "")
        self.assertIn("172,873", it[0]["title"])
        self.assertTrue(all(i["valid"] is False for i in it))
        self.assertEqual(it[1]["autocomplete"], "arrow")

    def test_pasted_characters_are_identified(self):
        it = sf("uni", "→")
        self.assertEqual(it[0]["arg"], "→")
        self.assertEqual(it[1]["arg"], "U+2192")
        it = sf("uni", "@")
        self.assertIn("Commercial At", it[0]["title"])

    def test_row_mods(self):
        it = self.first("U+2192")
        self.assertEqual(it["uid"], "u:2192")
        self.assertEqual(it["mods"]["cmd"]["arg"], "→")
        self.assertEqual(it["mods"]["alt"]["arg"], "&rarr;")
        self.assertEqual(it["mods"]["ctrl"]["arg"], "u:2192")
        self.assertEqual(it["mods"]["cmd+alt"]["arg"], "→")
        self.assertEqual(it["variables"]["sym_rec"], "u:2192")
        self.assertEqual(it["text"]["copy"], "→")
        self.assertTrue(it["icon"]["path"].startswith(CACHE))
        self.assertTrue(it["quicklookurl"].endswith(".html"))
        with open(it["quicklookurl"], encoding="utf-8") as f:
            page = f.read()
        self.assertIn("&amp;rarr;", page)
        for fmt, want in [("hex", "&#x2192;"), ("js", "\\u{2192}"), ("uplus", "U+2192"), ("python", "\\N{RIGHTWARDS ARROW}"),
                          ("css", "\\2192"), ("bogus", "&rarr;"), (" uplus ", "U+2192")]:
            self.assertEqual(self.first("U+2192", alt_format=fmt)["mods"]["alt"]["arg"], want, fmt)
        # no named entity: numeric
        self.assertEqual(self.first("U+1F600")["mods"]["alt"]["arg"], "&#x1F600;")

    def test_invisible_and_combining_titles(self):
        it = self.first("U+00A0")
        self.assertTrue(it["title"].startswith("[NBSP]"))
        it = self.first("U+0301")
        self.assertTrue(it["title"].startswith("◌́"))
        self.assertEqual(it["arg"], "\u0301")
        it = self.first("U+202E")  # a bidi override never reaches a title
        self.assertNotIn("\u202e", it["title"])
        self.assertEqual(it["arg"], "\u202e")

    def test_favourites_and_recents(self):
        out = raw("fav", "u:2192")
        self.assertIn("Added U+2192 to favourites", out)
        it = sf("uni", "")
        self.assertEqual(it[0]["arg"], "→")
        self.assertTrue(it[0]["subtitle"].startswith("★"))
        self.assertEqual(it[0]["mods"]["ctrl"]["subtitle"], "Remove from favourites")
        # a favourite ranks first among matches
        self.assertEqual(sf("uni", "arrow")[0]["arg"], "→")
        self.assertIn("Removed", raw("fav", "u:2192"))
        with open(os.path.join(DATADIR, "favourites.json")) as f:
            self.assertEqual(json.load(f), [])
        # recents come from act.sh
        self.assertEqual(act("é", "u:e9"), "é")
        self.assertEqual(act("😀", "u:1f600"), "😀")
        it = sf("uni", "")
        self.assertEqual(args(it)[:2], ["😀", "é"])
        self.assertTrue(it[0]["subtitle"].startswith("↺"))
        self.assertEqual(len(sf("uni", "", show_recent="0")), 4)  # back to the hints
        # rejected keys
        self.assertIn("Couldn’t", raw("fav", "rm -rf /"))
        self.assertIn("Couldn’t", raw("fav", "u:1\n2"))

    def test_recent_log_is_compacted(self):
        os.makedirs(DATADIR, exist_ok=True)
        with open(os.path.join(DATADIR, "recent.log"), "w") as f:
            for i in range(500):
                f.write("u:%x\n" % (0x41 + i % 20))
        it = sf("uni", "")
        self.assertEqual(it[0]["arg"], chr(0x41 + 499 % 20))
        with open(os.path.join(DATADIR, "recent.log")) as f:
            lines = f.read().split()
        self.assertLessEqual(len(lines), 150)

    def test_corrupt_data_files(self):
        os.makedirs(DATADIR, exist_ok=True)
        for content in ["{", "null", '{"a":1}', '["u:zz", 5, "u:110000"]']:
            with open(os.path.join(DATADIR, "favourites.json"), "w") as f:
                f.write(content)
            sf("uni", "")
            sf("uni", "arrow")

    def test_icon_style_and_versioned_cache(self):
        it = self.first("U+2605", icon_style="dark")
        self.assertIn("/glyphs-1-dark/", it["icon"]["path"])
        it = self.first("U+2605", icon_style=" junk ")
        self.assertIn("/glyphs-1-light/", it["icon"]["path"])
        with open(it["icon"]["path"], "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        old = os.path.join(CACHE, "glyphs-light")
        os.makedirs(old, exist_ok=True)
        sf("uni", "star", SYM_TEST_PRUNE="1")
        self.assertFalse(os.path.exists(old))

    def test_icons_render_progressively(self):
        # at most 10 new icons per run; the rest follow on a rerun
        fresh = os.path.join(BASE, "fresh cache")
        data = sf("uni", "latin capital letter", full=True, alfred_workflow_cache=fresh)
        drawn = [i for i in data["items"] if i["icon"]["path"].startswith(fresh)]
        self.assertEqual(len(drawn), 10)
        self.assertEqual(data["rerun"], 0.2)
        for _ in range(6):
            data = sf("uni", "latin capital letter", full=True, alfred_workflow_cache=fresh)
        self.assertNotIn("rerun", data)

    def test_injection_and_odd_input(self):
        for q in ['"; rm -rf ~', "$(touch /tmp/pwned)", "`id`", "a\nb", "\\", "'", "constructor", "__proto__",
                  "toString", "hasOwnProperty", "((((", "[a-z]+", "\t", "  ", "u+", "&", "&#;", "U+ZZZZ"]:
            for cmd in ["uni", "kao", "tex", "typo"]:
                sf(cmd, q)
        self.assertFalse(os.path.exists("/tmp/pwned"))

    def test_fresh_install_and_bad_paths(self):
        fresh = os.path.join(BASE, "new install", "cache dir")
        items = sf("uni", "arrow", alfred_workflow_cache=fresh, alfred_workflow_data=os.path.join(BASE, "new data"))
        self.assertTrue(items[0]["icon"]["path"].startswith(fresh))
        # relative or empty paths fall back to a private temporary folder, never the working directory
        for bad in ["", "relative/path", "/"]:
            items = sf("uni", "star", alfred_workflow_cache=bad, alfred_workflow_data=bad)
            p = items[0]["icon"]["path"]
            self.assertTrue(p.startswith("/") and "alfred-symbols" in p or p == "icons/char.png", p)

    def test_show_all_formats_jumps_to_chars(self):
        self.assertEqual(raw("show", "→").strip(), "ALFRED_SEARCH:chars →")
        self.assertEqual(raw("show", "→", keyword_chars=" what ").strip(), "ALFRED_SEARCH:what →")
        self.assertEqual(raw("show", "→", keyword_chars="").strip(), "ALFRED_SEARCH:chars →")


class InspectTests(unittest.TestCase):
    def test_clipboard_and_empty(self):
        it = sf("chars", "", clipboard="")
        self.assertEqual(it[0]["title"], "Clipboard is empty")
        it = sf("chars", "", clipboard="ab")
        self.assertTrue(it[0]["title"].startswith("Clipboard: 2 code points"))
        self.assertEqual(it[0]["arg"], "U+0061 U+0062")

    def test_spaces_are_kept(self):
        it = sf("chars", " ")
        self.assertEqual(it[0]["arg"], " ")
        self.assertTrue(it[0]["title"].startswith("[SP]"))

    def test_single_character_formats(self):
        it = sf("chars", "α")
        vals = args(it)
        for want in ["α", "U+03B1", "&alpha;", "&#x3B1;", "&#945;", "\\u{3B1}", "\\u03B1", "\\N{GREEK SMALL LETTER ALPHA}", "\\3B1",
                     "%CE%B1", "CE B1", "03B1", "945", "\\alpha"]:
            self.assertIn(want, vals)
        self.assertTrue(any(i["title"] == "Greek and Coptic" for i in it))

    def test_format_rows_have_no_character_mods(self):
        it = sf("chars", "α")
        row = next(i for i in it if i["arg"] == "U+03B1")
        self.assertFalse(row["mods"]["ctrl"]["valid"])
        self.assertFalse(row["mods"]["alt"]["valid"])
        self.assertTrue(row["mods"]["cmd"]["valid"])

    def test_graphemes_and_limits(self):
        it = sf("chars", "é👍🏽")  # precomposed é, thumbs up with a skin tone
        self.assertIn("3 code points · 2 characters · 5 UTF-16 units · 10 UTF-8 bytes", it[0]["title"])
        it = sf("chars", "x" * 300)
        self.assertIn("first 100", it[-1]["title"])
        self.assertEqual(len(it), 3 + 100 + 1)


class KaomojiTests(unittest.TestCase):
    def setUp(self):
        reset_data()

    def test_categories(self):
        it = sf("kao", "")
        self.assertIn("Table Flip", titles(it))
        cat = next(i for i in it if i["title"] == "Shrug")
        self.assertEqual(cat["autocomplete"], "shrug ")
        self.assertFalse(cat["valid"])

    def test_search(self):
        it = sf("kao", "table flip")
        self.assertTrue(all("Table Flip" in i["subtitle"] for i in it))
        self.assertGreater(len(it), 20)
        it = sf("kao", "shrug")
        self.assertIn("¯\\_(ツ)_/¯", args(it))
        it = sf("kao", "cat")
        self.assertTrue(all("cat" in i["subtitle"] for i in it[:10]))
        it = sf("kao", "smile")
        self.assertIn(":)", args(it))
        it = sf("kao", "zzzqqq")
        self.assertFalse(it[0]["valid"])
        it = sf("kao", "ツ")  # the kaomoji itself
        self.assertIn("¯\\_(ツ)_/¯", args(it))

    def test_favourite_and_recent(self):
        raw("fav", "k:(ツ)")  # not in the data: ignored on display
        raw("fav", "k:¯\\_(ツ)_/¯")
        act("(=^ェ^=)", "k:(=^ェ^=)")
        it = sf("kao", "")
        self.assertEqual(args(it)[:2], ["¯\\_(ツ)_/¯", "(=^ェ^=)"])
        self.assertTrue(it[0]["subtitle"].startswith("★"))
        self.assertEqual(sf("kao", "whatever")[0]["arg"], "¯\\_(ツ)_/¯")


FANCY = {
    "Bold": "𝐀𝐳𝟎𝟗", "Italic": "𝐴𝑧09", "Double-struck": "𝔸𝕫𝟘𝟡", "Monospace": "𝙰𝚣𝟶𝟿", "Fullwidth": "Ａｚ０９",
    "Sans-serif bold": "𝗔𝘇𝟬𝟵", "Circled": "Ⓐⓩ⓪⑨", "Small caps": "ᴀᴢ09", "Superscript": "ᴬᶻ⁰⁹", "Subscript": "A z₀₉".replace(" ", ""),
}


class FancyTests(unittest.TestCase):
    def styles(self, text, **kw):
        return {i["subtitle"].split(" · ")[0]: i["arg"] for i in sf("fancy", text, **kw)}

    def test_alphabets(self):
        s = self.styles("Az09")
        for name, want in FANCY.items():
            self.assertEqual(s[name], want, name)

    def test_holes_in_the_math_alphabets(self):
        s = self.styles("BEFHILMRegoCHIRZNPQh")
        self.assertEqual(s["Script"], "ℬℰℱℋℐℒℳℛℯℊℴ𝒞ℋℐℛ𝒵𝒩𝒫𝒬𝒽")
        self.assertEqual(s["Fraktur"], "𝔅𝔈𝔉ℌℑ𝔏𝔐ℜ𝔢𝔤𝔬ℭℌℑℜℨ𝔑𝔓𝔔𝔥")
        self.assertEqual(s["Double-struck"], "𝔹𝔼𝔽ℍ𝕀𝕃𝕄ℝ𝕖𝕘𝕠ℂℍ𝕀ℝℤℕℙℚ𝕙")
        self.assertEqual(s["Italic"][-1], "ℎ")
        # every code point is a letter of the right style (checked with Python's names)
        for name in ["Bold", "Italic", "Script", "Fraktur", "Double-struck", "Sans-serif", "Monospace"]:
            for ch in s[name]:
                n = unicodedata.name(ch)
                self.assertTrue(n.startswith(("MATHEMATICAL", "SCRIPT", "BLACK-LETTER", "DOUBLE-STRUCK", "PLANCK", "ITALIC SMALL")), (name, n))

    def test_marks_and_surrogates(self):
        s = self.styles("a😀 b")
        self.assertEqual(s["Strikethrough"], "a\u0336😀\u0336 b\u0336")
        self.assertEqual(s["Underlined"], "a\u0332😀\u0332 b\u0332")
        s = self.styles("e\u0301")  # an existing mark stays with its letter
        self.assertEqual(s["Strikethrough"], "e\u0301\u0336")
        self.assertEqual(s["Bold"], "𝐞\u0301")
        s = self.styles("é")  # precomposed: decomposed, styled, mark kept
        self.assertEqual(s["Bold"], "𝐞\u0301")
        for v in self.styles("😀 👨‍👩‍👧 𝐀 é x").values():
            v.encode("utf-8")  # no lone surrogates

    def test_upside_down_and_plain(self):
        s = self.styles("Hello!")
        self.assertEqual(s["Upside down"], "¡ollǝH")
        items = sf("fancy", "𝐇𝐞𝐥𝐥𝐨 ｗｏｒｌｄ s̶")
        self.assertEqual(items[0]["arg"], "Hello world s")
        self.assertIn("Plain text", items[0]["subtitle"])
        self.assertNotIn("Plain text", sf("fancy", "Hello")[0]["subtitle"])

    def test_clipboard_and_empty(self):
        it = sf("fancy", "", clipboard="")
        self.assertFalse(it[0]["valid"])
        it = sf("fancy", "", clipboard="hi")
        self.assertEqual(it[0]["arg"], "𝐡𝐢")
        self.assertIn("from the clipboard", it[0]["subtitle"])
        self.assertEqual(sf("fancy", "  hi  ")[0]["arg"], "  𝐡𝐢  ")  # spaces kept (no trimming)


class LatexTests(unittest.TestCase):
    def setUp(self):
        reset_data()

    def test_search(self):
        self.assertEqual(sf("tex", "\\alpha")[0]["arg"], "\\alpha")
        self.assertEqual(sf("tex", "alpha")[0]["arg"], "\\alpha")
        self.assertEqual(sf("tex", "Delta")[0]["arg"], "\\Delta")
        self.assertEqual(sf("tex", "delta")[0]["arg"], "\\delta")
        self.assertEqual(sf("tex", "leq")[0]["arg"], "\\leq")
        self.assertEqual(sf("tex", "∈")[0]["arg"], "\\in")
        self.assertEqual(sf("tex", "subset")[0]["arg"], "\\subset")
        self.assertIn("\\rightarrow", args(sf("tex", "rightwards arrow"))[:3])
        self.assertIn("\\mathbb{R}", args(sf("tex", "ℝ")))
        self.assertFalse(sf("tex", "zzqqzz")[0]["valid"])
        self.assertFalse(sf("tex", "ア")[0]["valid"])
        self.assertIn("Search", sf("tex", "")[0]["title"])

    def test_actions(self):
        it = sf("tex", "\\alpha")[0]
        self.assertEqual(it["mods"]["alt"]["arg"], "α")
        self.assertEqual(it["mods"]["cmd"]["arg"], "\\alpha")
        self.assertEqual(it["variables"]["sym_rec"], "x:\\alpha")
        it = sf("tex", "\\alpha", tex_primary="character")[0]
        self.assertEqual(it["arg"], "α")
        self.assertEqual(it["mods"]["alt"]["arg"], "\\alpha")
        self.assertIn("LaTeX", it["subtitle"])

    def test_favourites(self):
        raw("fav", "x:\\leq")
        act("\\alpha", "x:\\alpha")
        it = sf("tex", "")
        self.assertEqual(args(it)[:2], ["\\leq", "\\alpha"])


class TypographyTests(unittest.TestCase):
    def fix(self, text, **env):
        return sf("typofix", text, **env)[0]["arg"]

    def test_quotes_and_apostrophes(self):
        self.assertEqual(self.fix('"Hello," she said. "It\'s fine."'), "“Hello,” she said. “It’s fine.”")
        self.assertEqual(self.fix("'single' and don't"), "‘single’ and don’t")
        self.assertEqual(self.fix("the '90s, rock 'n' roll"), "the ’90s, rock ’n’ roll")
        self.assertEqual(self.fix('("quoted")'), "(“quoted”)")
        self.assertEqual(self.fix("He said \"'hi'\""), "He said “‘hi’”")
        self.assertEqual(self.fix("the kids' toys"), "the kids’ toys")

    def test_quote_styles(self):
        self.assertEqual(self.fix('"Hallo" \'x\'', quote_style="de"), "„Hallo“ ‚x‘")
        self.assertEqual(self.fix('"Bonjour"', quote_style="fr"), "«\u202fBonjour\u202f»")
        self.assertEqual(self.fix('"Hi"', quote_style="en-gb"), "‘Hi’")
        self.assertEqual(self.fix('"Hi"', quote_style=" bogus "), "“Hi”")

    def test_dashes_ellipses_symbols(self):
        self.assertEqual(self.fix("wait -- what --- no"), "wait—what—no")
        self.assertEqual(self.fix("wait -- what", dash_style="en"), "wait – what")
        self.assertEqual(self.fix("this - that"), "this—that")
        self.assertEqual(self.fix("well..."), "well…")
        self.assertEqual(self.fix("(c) 2026 Acme(tm) (r)"), "© 2026 Acme™ ®")
        self.assertEqual(self.fix("1920 x 1080 and 1/2 cup"), "1920 × 1080 and ½ cup")
        self.assertEqual(self.fix("on 1/2/2026"), "on 1/2/2026")
        self.assertEqual(self.fix("5'10\" tall"), "5′10″ tall")
        self.assertEqual(self.fix("two  spaces"), "two spaces")
        self.assertEqual(self.fix("2026-09-28 and x-ray"), "2026-09-28 and x-ray")
        self.assertEqual(self.fix("  indented\n  kept"), "  indented\n  kept")

    def test_code_urls_and_tags_untouched(self):
        t = "Run `echo \"hi\" -- 'x'` or see https://example.com/a--b?q='x'&y=1 and <a href=\"x\">\"link\"</a>"
        self.assertEqual(self.fix(t), "Run `echo \"hi\" -- 'x'` or see https://example.com/a--b?q='x'&y=1 and <a href=\"x\">“link”</a>")
        t = "```\nlet s = \"a\" -- b\n```\n\"after\""
        self.assertEqual(self.fix(t), "```\nlet s = \"a\" -- b\n```\n“after”")

    def test_rows(self):
        it = sf("typofix", "\"It's\" -- ok...")
        self.assertIn("2 quotes, 1 apostrophe, 1 dash, 1 ellipsis", it[0]["subtitle"])
        self.assertFalse(any("Straight" in i["subtitle"] for i in it))  # nothing to straighten
        it = sf("typofix", "“It’s” — ok…")
        straight = next(i for i in it if "Straight" in i["subtitle"])
        self.assertEqual(straight["arg"], "\"It's\" -- ok...")
        self.assertIn("Already", it[0]["subtitle"])
        wrap = next(i for i in sf("typofix", "hi") if "Wrap" in i["subtitle"])
        self.assertEqual(wrap["arg"], "“hi”")
        it = sf("typofix", "", clipboard="\"x\"")
        self.assertEqual(it[0]["arg"], "“x”")
        self.assertIn("from the clipboard", it[0]["subtitle"])
        self.assertFalse(sf("typofix", "", clipboard="")[0]["valid"])

    def test_catalogue(self):
        it = sf("typo", "")
        self.assertEqual(len(it), 78)
        self.assertEqual(sf("typo", "dash")[0]["arg"], "–")
        self.assertEqual(sf("typo", "em dash")[0]["arg"], "—")
        self.assertIn("\u00a0", args(sf("typo", "space")))
        self.assertIn("&ndash;", sf("typo", "dash")[0]["subtitle"])
        self.assertFalse(sf("typo", "qqqzzz")[0]["valid"])
        it = sf("typo", "german")
        self.assertEqual(it[0]["arg"], "„")


class ActionTests(unittest.TestCase):
    def setUp(self):
        reset_data()

    def test_act_passes_text_exactly(self):
        for s in ["→", "a\nb\n", "-n", "\\n", "$(id)", "  spaced  ", "😀", "%s%d", "-e"]:
            self.assertEqual(act(s, None), s)

    def test_act_rejects_bad_keys(self):
        act("x", "u:41\nrm")
        act("x", "evil")
        act("x", "u:" + "a" * 400)
        self.assertFalse(os.path.exists(os.path.join(DATADIR, "recent.log")))
        act("x", "u:41", alfred_workflow_data="relative")  # never writes outside the data folder
        self.assertFalse(os.path.exists(os.path.join(SRC, "relative")))


class BuildTests(unittest.TestCase):
    def test_build_and_package(self):
        out = subprocess.run([sys.executable, "tools/build.py", "--package"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with open(os.path.join(SRC, "info.plist"), "rb") as f:
            info = plistlib.load(f)
        self.assertEqual(info["bundleid"], "io.github.x-o-r-r-o.symbols")
        kws = [o["config"]["keyword"] for o in info["objects"] if "keyword" in o["config"]]
        self.assertEqual(len(kws), 7)
        defaults = [c["config"]["default"] for c in info["userconfigurationconfig"] if c["variable"].startswith("keyword_")]
        family = set("aitext anc ask audio bright case dev diff dls enc epoch font fredo gcal gdoc goo gtask gws hash hist icon img "
                     "json jwt layout lin linact logo m365 media meet mic models nadd new nnew notes obb obd obm obn obs obv onenote "
                     "outlook pomo race regex res shot ssh stock teams tldr tomd tra track trh uuid vid warp ytt".split())
        for d in defaults:
            self.assertGreaterEqual(len(d), 3)
            self.assertNotIn(d, family)
            self.assertFalse(d.startswith(("bu", "brew")))
        self.assertIn("## Usage", info["readme"])
        dist = os.path.join(ROOT, "dist", "alfred-symbols-%s.alfredworkflow" % info["version"])
        with zipfile.ZipFile(dist) as z:
            names = z.namelist()
        for n in names:
            self.assertTrue(os.path.exists(os.path.join(SRC, n)), n)
        self.assertIn("data/chars.txt", names)
        self.assertIn("act.sh", names)
        self.assertFalse(any(n.startswith(("tests", "tools")) or n.endswith(".py") for n in names))
        self.assertTrue(os.access(os.path.join(SRC, "act.sh"), os.X_OK))

    def test_no_lookbehind_or_modern_syntax(self):
        # macOS 13.0's JavaScriptCore: no regex lookbehind, no hashbang-free top-level await etc.
        with open(os.path.join(SRC, "symbols.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotRegex(js, r"\(\?<[=!]")
        self.assertNotIn(".at(", js)
        self.assertNotIn("replaceAll", js)


class AuditOneRegressionTests(unittest.TestCase):
    """Bugs found in the first audit."""

    def test_no_endless_rerun_when_icons_cannot_be_written(self):
        ro = os.path.join(BASE, "read only")
        os.makedirs(ro, exist_ok=True)
        os.chmod(ro, 0o555)
        try:
            data = sf("uni", "latin capital letter", full=True, alfred_workflow_cache=os.path.join(ro, "cache"))
            self.assertNotIn("rerun", data)
        finally:
            os.chmod(ro, 0o755)

    def test_rows_that_cannot_be_copied_have_no_actions(self):
        it = sf("uni", "U+0378")[0]
        self.assertFalse(it["mods"]["ctrl"]["valid"])
        self.assertFalse(it["mods"]["cmd+alt"]["valid"])
        self.assertFalse(it["mods"]["cmd"]["valid"])

    def test_bare_u_prefix_needs_four_digits(self):
        self.assertNotEqual(sf("uni", "ue")[0]["arg"], "\x0e")
        self.assertNotEqual(sf("uni", "uad")[0].get("arg"), "\xad")
        self.assertEqual(sf("uni", "u2192")[0]["arg"], "→")
        self.assertEqual(sf("uni", "U1F600")[0]["arg"], "😀")

    def test_unknown_sequence_and_punctuation(self):
        it = sf("uni", "U+41 U+42")[0]
        self.assertEqual(it["arg"], "AB")
        self.assertIn("Sequence of 2 code points", it["title"])
        it = sf("uni", "->")
        self.assertIn("2 code points", it[0]["title"])

    def test_fancy_input_is_capped(self):
        it = sf("fancy", "x" * 2001)
        self.assertEqual(len(it), 1)
        self.assertFalse(it[0]["valid"])
        self.assertEqual(len(sf("fancy", "x" * 2000)), 27)

    def test_large_results_go_through_the_cache(self):
        text = ('"quoted" -- text... ' * 4000)
        data = raw("typofix", text)
        self.assertLess(len(data), 60000)
        it = json.loads(data)["items"][0]
        self.assertTrue(it["arg"].startswith("symfile:" + CACHE + "/results/"))
        self.assertEqual(it["mods"]["cmd"]["arg"], it["arg"])
        self.assertIn("Too long", it["text"]["copy"])
        fixed = act(it["arg"])
        self.assertTrue(fixed.startswith("“quoted”—text… “quoted”"))
        self.assertNotIn('"', fixed)
        # anything else that looks like a result file passes through untouched
        for fake in ["symfile:/etc/passwd", "symfile:" + CACHE + "/results/../../x.txt", "symfile:" + CACHE + "/results/zz.txt"]:
            self.assertEqual(act(fake), fake)

    def test_typography_edge_cases(self):
        fix = lambda t, **e: sf("typofix", t, **e)[0]["arg"]
        self.assertEqual(fix("rock 'n' roll", quote_style="de"), "rock ’n’ roll")
        self.assertEqual(fix("don't", quote_style="de"), "don’t")
        self.assertEqual(fix("run npm i --save now"), "run npm i --save now")
        self.assertEqual(fix("wait--what"), "wait—what")
        self.assertEqual(fix("wait--what", dash_style="en"), "wait–what")
        self.assertEqual(fix("title\n---\nbody"), "title\n---\nbody")


class AuditTwoRegressionTests(unittest.TestCase):
    """Bugs found in the second audit."""

    def test_large_text_fix_is_linear(self):
        # the quote pass read the last character of a growing string: 3 s for 500,000 characters
        text = '"quoted" -- text... ' * 25000
        t = time.time()
        raw("typofix", text)
        self.assertLess(time.time() - t, 1.5)
        self.assertFalse(sf("typofix", "x" * 500001)[0]["valid"])

    def test_pruning_runs_at_most_daily(self):
        fresh = os.path.join(BASE, "prune cache")
        old = os.path.join(fresh, "results", "0123.txt")
        os.makedirs(os.path.dirname(old))
        with open(old, "w") as f:
            f.write("x")
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        sf("uni", "star", alfred_workflow_cache=fresh, SYM_TEST_PRUNE="1")
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(os.path.join(fresh, ".pruned")))


class AuditThreeRegressionTests(unittest.TestCase):
    """Bugs found in the third audit."""

    def test_alias_bonus_only_when_the_name_misses(self):
        # "HANGUL JUNGSEONG E" (alias "E") came before "LATIN SMALL LETTER E"
        self.assertEqual(sf("uni", "-e")[0]["arg"], "e")
        self.assertEqual(sf("uni", "have a nice day")[0]["arg"], "☺")

    def test_python_name_escape(self):
        self.assertEqual(sf("uni", "\\N{RIGHTWARDS ARROW}")[0]["arg"], "→")
        self.assertEqual(sf("uni", "\\N{nbsp}")[0]["arg"], "\u00a0")

    def test_options_like_queries(self):
        for q in ["-e", "-l", "--", "-", "--help"]:
            for cmd in ["uni", "chars", "kao", "fancy", "tex", "typo", "typofix"]:
                sf(cmd, q)

    def test_two_large_results_get_different_files(self):
        text = "a" * 60000
        items = sf("typofix", text + '"')
        files = [i["arg"] for i in items if i["arg"].startswith("symfile:")]
        self.assertEqual(len(files), len(set(files)))
        self.assertGreaterEqual(len(files), 2)


class PerformanceTests(unittest.TestCase):
    def test_keystroke_speed(self):
        queries = ["a", "ar", "arr", "arro", "arrow", "right arrow", "l", "latin small letter e", "U+1F600", "heart", "🙂", "zzzz"]
        for q in queries:
            for _ in range(8):  # warm the icon cache: new icons are drawn 10 per run, on reruns
                if "rerun" not in sf("uni", q, full=True):
                    break
        times = []
        for q in queries:
            t = time.time()
            raw("uni", q)
            times.append((time.time() - t) * 1000)
        for cmd, q in [("kao", "happy"), ("tex", "arrow"), ("fancy", "Hello world"), ("typofix", '"a" -- b'), ("typo", "dash")]:
            raw(cmd, q)
            t = time.time()
            raw(cmd, q)
            times.append((time.time() - t) * 1000)
        print("\n  per-keystroke ms (warm): max %.0f, median %.0f" % (max(times), sorted(times)[len(times) // 2]), file=sys.stderr)
        self.assertLess(sorted(times)[len(times) // 2], 150)
        self.assertLess(max(times), 300)

    def test_cold_run_is_bounded(self):
        # a fresh cache: at most 10 icons are drawn, so even the first keystroke stays quick
        fresh = os.path.join(BASE, "cold cache")
        t = time.time()
        raw("uni", "arrow", alfred_workflow_cache=fresh)
        ms = (time.time() - t) * 1000
        print("\n  cold keystroke with 10 new icons: %.0f ms" % ms, file=sys.stderr)
        self.assertLess(ms, 500)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
