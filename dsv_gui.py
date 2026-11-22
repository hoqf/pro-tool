# DSV GUI - Discord username availability checker (video-site style UI)
#
# Based on DSV by suenerve (https://github.com/suenerve/DSV)
# Original author credit is required wherever this code is used.
# Original license: NON-commercial use only.
#
# NOTE: Automating a user account is against Discord's Terms of Service and can get
# the account suspended. Use an alt account and a generous delay. Use at your own risk.

import base64
import collections
import colorsys
import ctypes
import datetime
import itertools
import json
import math
import os
import queue
import random
import re
import string
import subprocess
import sys
import threading
import time
import unicodedata
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont

import requests

APP_TITLE = "DSV  -  Username Scanner"
CREDIT = "Based on DSV by suenerve (github.com/suenerve/DSV) - non-commercial use"
ME_URL = "https://discord.com/api/v9/users/@me"
CHECK_URL = "https://discord.com/api/v9/users/@me/pomelo-attempt"
IS_WINDOWS = sys.platform.startswith("win")
# Only real Discord webhook URLs are ever contacted (the URL is user-supplied).
WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?"
                        r"/webhooks/\d+/[A-Za-z0-9_\-]+/?(?:\?.*)?$")

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(BASE_DIR, "available_usernames.txt")
SETTINGS_FILE = os.path.join(BASE_DIR, "dsv_settings.json")
DICT_DIR = os.path.join(BASE_DIR, "dictionaries")   # downloaded word lists live here
CHECKED_FILE = os.path.join(BASE_DIR, "dsv_checked.txt")  # names already answered (taken); skipped on later runs
MAX_ACCOUNTS = 50      # saved tokens in the account list
MAX_PRESETS = 30       # saved search presets
MAX_WORDS = 8          # most words a single name may be built from
MIN_INTERVAL = 0.2     # shortest delay between checks that can be set (seconds)
MAX_INTERVAL = 120.0   # longest the pacer will stretch the delay after repeated 429s
MAX_DELAY = 600.0      # longest delay that can be set
# Real spell-check dictionaries (Hunspell .dic) from github.com/wooorm/dictionaries.
DICT_URL = "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/{code}/index.dic"
# Only Latin-script languages: Discord usernames allow just a-z, 0-9, "_" and ".", so words in
# Cyrillic, Greek, Hebrew, Korean, etc. could never be valid names. Accents are folded (e -> e).
LANGUAGES = [
    ("Basque", "eu"), ("Breton", "br"), ("Catalan", "ca"), ("Croatian", "hr"), ("Czech", "cs"),
    ("Danish", "da"), ("Dutch", "nl"), ("English", "en"), ("English (Australia)", "en-AU"),
    ("English (Canada)", "en-CA"), ("English (South Africa)", "en-ZA"),
    ("English (United Kingdom)", "en-GB"), ("Esperanto", "eo"), ("Estonian", "et"),
    ("Faroese", "fo"), ("French", "fr"), ("Friulian", "fur"), ("Galician", "gl"),
    ("German", "de"), ("Hungarian", "hu"), ("Icelandic", "is"), ("Interlingua", "ia"),
    ("Interlingue", "ie"), ("Irish", "ga"), ("Italian", "it"), ("Kinyarwanda", "rw"),
    ("Klingon (Latin script)", "tlh-Latn"), ("Latin", "la"), ("Latvian", "lv"),
    ("Lithuanian", "lt"), ("Low German", "nds"), ("Luxembourgish", "lb"),
    ("Norwegian Bokm\u00e5l", "nb"), ("Norwegian Nynorsk", "nn"), ("Occitan", "oc"),
    ("Polish", "pl"), ("Portuguese", "pt"), ("Portuguese (Portugal)", "pt-PT"),
    ("Romanian", "ro"), ("Scottish Gaelic", "gd"), ("Serbian (Latin script)", "sr-Latn"),
    ("Slovak", "sk"), ("Slovenian", "sl"), ("Spanish", "es"), ("Spanish (Argentina)", "es-AR"),
    ("Spanish (Mexico)", "es-MX"), ("Swedish", "sv"), ("Turkish", "tr"), ("Turkmen", "tk"),
    ("Vietnamese", "vi"), ("Welsh", "cy"), ("Western Frisian", "fy"),
]
LANG_NAMES = {code: name for name, code in LANGUAGES}
KEEP_CAPS = {"de", "lb", "nds", "tlh-Latn"}   # capital letters are meaningful (German nouns etc.)

DEFAULT_HOOK = {
    "content": "", "embed": True, "timestamp": True, "color": "#ff0000",
    "title": "Username `{username}` is available",
    "desc": "Found at {time}. {count} available so far.",
    "footer": "DSV GUI - based on suenerve/DSV",
}

HOOK_TEMPLATES = (
    ("Default card", {}),
    ("Plain text", {"content": "Username `{username}` is available!", "embed": False}),
    ("Short card", {"title": "{username}", "desc": "", "footer": "", "timestamp": False}),
    ("Loud (@here)", {"content": "@here `{username}` is AVAILABLE", "title": "{username}",
                      "desc": "{length} characters - found at {time}", "color": "#ffcc00"}),
)

# ------------------------------------------------------------------ palette
# A dark, flat look modelled on the YouTube web app.
BG0 = "#0f0f0f"       # page / window
BG1 = "#181818"       # panels, log
BG2 = "#121212"       # inputs
BGLOG = "#181818"     # log / lists
BORDER = "#303030"    # hairlines, input outlines
CHIP = "#272727"      # pills, hover and selected backgrounds
CHIP_HOVER = "#3f3f3f"
ACCENT = "#ff0000"    # the red
ACCENT2 = "#3ea6ff"   # links, toggles, focus rings
TEXT = "#f1f1f1"
MUTED = "#aaaaaa"
OK = "#3fd16b"
WARN = "#f5a623"
ERR = "#ff6159"

# ------------------------------------------------------------------ theme
# A theme is a transform of the palette above:
#   hue   - accent hue in degrees (0 = the default red)
#   sat   - saturation multiplier        val  - brightness multiplier
#   lift  - how much the dark surfaces are lightened/tinted, 0..0.35
# OK / WARN / ERR keep their meaning colours on purpose.
THEME_VERSION = 2      # themes saved by the older blue UI (no "v") are ignored
BASE_HUE = 0.0
DEFAULT_THEME = {"hue": BASE_HUE, "sat": 1.0, "val": 1.0, "lift": 0.0, "mode": "dark"}
THEME = dict(DEFAULT_THEME)
_BASE = dict(BG0=BG0, BG1=BG1, BG2=BG2, BGLOG=BGLOG, BORDER=BORDER, CHIP=CHIP,
             CHIP_HOVER=CHIP_HOVER, ACCENT=ACCENT, ACCENT2=ACCENT2, TEXT=TEXT, MUTED=MUTED)
# Base palettes the hue/saturation/brightness sliders are applied on top of.
_BASES = {
    "dark": _BASE,
    "black": dict(_BASE, BG0="#000000", BG1="#0a0a0a", BG2="#050505", BGLOG="#0a0a0a", BORDER="#262626",
                  CHIP="#1a1a1a", CHIP_HOVER="#2e2e2e"),
    "light": dict(BG0="#ffffff", BG1="#f4f4f4", BG2="#ffffff", BGLOG="#f4f4f4", BORDER="#d3d3d3",
                  CHIP="#ececec", CHIP_HOVER="#dcdcdc", ACCENT="#ff0000", ACCENT2="#065fd4",
                  TEXT="#0f0f0f", MUTED="#606060"),
}
_SEMANTIC = {"dark": ("#3fd16b", "#f5a623", "#ff6159"), "black": ("#3fd16b", "#f5a623", "#ff6159"),
             "light": ("#16883f", "#a96800", "#d93025")}
MODE_NAMES = (("Dark", "dark"), ("Black", "black"), ("Light", "light"))

SCALE = 1.0
TEXT_ADJ = 0           # extra points added to every font (Appearance > Text size)
SMOOTH_SCROLL = True   # eased mouse-wheel scrolling (Settings > Scrolling)
SCROLL_SPEEDS = {"slow": 0.6, "normal": 1.0, "fast": 1.8}
SCROLL_SPEED = "normal"
UI_FONT = "TkDefaultFont"
MONO_FONT = "TkFixedFont"


def S(v):
    """Scale a pixel value for high-DPI screens."""
    return max(1, int(round(v * SCALE)))


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] + (rb[i] - ra[i]) * t) for i in range(3))


def T(c, th=None):
    """Map a colour from the default palette into the given (or current) theme."""
    th = th or THEME
    if th["hue"] == BASE_HUE and th["sat"] == 1.0 and th["val"] == 1.0 and th["lift"] == 0.0:
        return c
    r, g, b = (x / 255.0 for x in _rgb(c))
    h, sat, v = colorsys.rgb_to_hsv(r, g, b)
    v2 = min(1.0, v * th["val"])
    v2 = min(1.0, v2 + th["lift"] * (1.0 - v2) ** 2)   # only dark colours are lifted noticeably
    if sat < 0.05:   # greys stay grey, unless a dark surface is lifted: then it takes a faint tint
        if v < 0.5 and v2 - v > 0.004:
            h, sat = th["hue"] / 360.0, min(1.0, 0.3 * th["sat"])
    else:
        h = (h + (th["hue"] - BASE_HUE) / 360.0) % 1.0
        sat = min(1.0, sat * th["sat"])
    r, g, b = colorsys.hsv_to_rgb(h, sat, v2)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def apply_theme(theme):
    """Set the module-level palette from a theme dict (validated and clamped)."""
    try:
        t = {"hue": float(theme.get("hue", BASE_HUE)) % 360.0,
             "sat": max(0.0, min(2.0, float(theme.get("sat", 1.0)))),
             "val": max(0.4, min(1.6, float(theme.get("val", 1.0)))),
             "lift": max(0.0, min(0.35, float(theme.get("lift", 0.0)))),
             "mode": theme.get("mode") if theme.get("mode") in _BASES else "dark"}
    except (TypeError, ValueError, AttributeError):
        t = dict(DEFAULT_THEME)
    if abs(t["hue"] - BASE_HUE) < 0.5 or abs(t["hue"] - 360.0) < 0.5: t["hue"] = BASE_HUE
    if abs(t["sat"] - 1.0) < 0.01: t["sat"] = 1.0
    if abs(t["val"] - 1.0) < 0.01: t["val"] = 1.0
    if t["lift"] < 0.005: t["lift"] = 0.0
    THEME.clear()
    THEME.update(t)
    g = globals()
    for k, v in _BASES[t["mode"]].items():
        g[k] = T(v)
    g["OK"], g["WARN"], g["ERR"] = _SEMANTIC[t["mode"]]


def rrect(c, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def fmt_duration(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def render_template(tpl, ctx):
    """Fill {placeholders} in a webhook text; unknown braces are left alone. '\\n' = new line."""
    out = tpl.replace("\\n", "\n")
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def sniff_image(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None



# --------------------------------------------- Windows-only secret protection
if IS_WINDOWS:
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _to_blob(data):
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def dpapi_encrypt(data):
        blob_in, _keep = _to_blob(data)
        blob_out = _BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    def dpapi_decrypt(data):
        blob_in, _keep = _to_blob(data)
        blob_out = _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
else:
    def dpapi_encrypt(data):
        raise OSError("DPAPI is only available on Windows")

    dpapi_decrypt = dpapi_encrypt


def protect(text):
    """Encrypt for this Windows user (DPAPI). Elsewhere: only base64 (NOT secure)."""
    if IS_WINDOWS:
        return "dpapi:" + base64.b64encode(dpapi_encrypt(text.encode("utf-8"))).decode()
    return "plain:" + base64.b64encode(text.encode("utf-8")).decode()


def unprotect(blob):
    kind, _, payload = blob.partition(":")
    raw = base64.b64decode(payload)
    if kind == "dpapi":
        return dpapi_decrypt(raw).decode("utf-8")
    if kind == "plain":
        return raw.decode("utf-8")
    raise ValueError("unknown format")


# ------------------------------------------------------------ pacer
class Pacer:
    """Decides when the next check may be sent.

    Every check waits a random time between the user's minimum and maximum delay, so the
    requests don't arrive on a robotic fixed beat. If Discord answers 429 (rate limited) the
    whole range is stretched x1.5 (up to MAX_INTERVAL) and eases back to the user's range after
    a clean streak. Discord's rate-limit headers are honoured too, so it waits *before* a 429
    when the bucket is empty. Request time counts towards the delay (it is not added on top).
    """
    STREAK_TO_EASE = 25

    def __init__(self, dmin, dmax):
        self.dmin = max(MIN_INTERVAL, float(dmin))
        self.dmax = max(self.dmin, float(dmax))
        self.factor = 1.0
        self.streak = 0
        self.next_at = time.monotonic()

    @property
    def interval(self):
        """Average seconds between checks right now (for display)."""
        return (self.dmin + self.dmax) / 2.0 * self.factor

    def _gap(self):
        return random.uniform(self.dmin, self.dmax) * self.factor

    def wait_time(self):
        return max(0.0, self.next_at - time.monotonic())

    def ok(self, headers):
        self.streak += 1
        if self.streak >= self.STREAK_TO_EASE:
            self.streak = 0
            self.factor = max(1.0, self.factor * 0.9)
        now, gap = time.monotonic(), self._gap()
        self.next_at = max(self.next_at, now - gap) + gap
        # bucket empty? sleep until it resets instead of provoking a 429
        try:
            if int(headers.get("X-RateLimit-Remaining", "1")) == 0:
                reset = float(headers.get("X-RateLimit-Reset-After", "0"))
                self.next_at = max(self.next_at, now + reset + 0.1)
        except (TypeError, ValueError):
            pass

    def limited(self, retry_after):
        self.streak = 0
        self.factor = min(self.factor * 1.5, max(1.0, MAX_INTERVAL / ((self.dmin + self.dmax) / 2.0)))
        self.next_at = time.monotonic() + retry_after + random.uniform(0.2, 0.8)


# ------------------------------------------------------------ dictionaries
_FOLD = {"\u00df": "ss", "\u00e6": "ae", "\u0153": "oe", "\u00f8": "o", "\u0111": "d",
         "\u0142": "l", "\u0131": "i", "\u00fe": "th", "\u00f0": "d"}


def fold_text(text):
    """Lower-case, strip accents; None if it can't appear in a Discord username."""
    w = "".join(_FOLD.get(ch, ch) for ch in text.lower())
    w = "".join(ch for ch in unicodedata.normalize("NFKD", w) if not unicodedata.combining(ch))
    if not w or ".." in w:
        return None
    if not all(("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "_." for ch in w):
        return None   # apostrophes, hyphens, spaces, non-Latin letters...
    return w


def fold_word(word):
    w = fold_text(word)
    return w if w and 2 <= len(w) <= 32 else None


def parse_wordlist(text, skip_caps):
    """Parse a Hunspell .dic (or a plain one-word-per-line file) into unique usable words."""
    seen, out = set(), []
    for n, line in enumerate(text.splitlines()):
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith(("#", "\t")):
            continue
        if n == 0 and line.isdigit():      # Hunspell header: number of entries
            continue
        word = line.split()[0]
        cut = word.find("/")               # "word/FLAGS" - drop the affix flags
        while cut > 0 and word[cut - 1] == "\\":   # escaped slash
            cut = word.find("/", cut + 1)
        if cut > 0:
            word = word[:cut]
        if skip_caps and word[:1].isupper():
            continue                       # proper nouns
        if len(word) > 1 and word.isupper() and not any(c.islower() for c in word):
            continue                       # abbreviations such as USA
        w = fold_word(word)
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


# ------------------------------------------------------------ name generator
# A "plan" (dict) describes what to generate:
#   lo, hi      total name length (2..32)        chars   allowed characters (no dictionary)
#   words       dictionary word pool, or None    wmin/wmax  words per name (dictionary only)
#   must        text every name has to contain ("" = none). With a dictionary it counts as one
#               of the words; without one it is surrounded by random characters.
#   place       "any" | "start" | "end"          sequential / limit  as in the UI
def parse_name_list(text):
    """Names typed/pasted by the user -> (valid unique names, how many entries were not valid)."""
    seen, out, bad = set(), [], 0
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        w = fold_word(part)
        if not w:
            bad += 1
        elif w not in seen:
            seen.add(w)
            out.append(w)
    return out, bad


def _group(words):
    by_len = {}
    for w in words:
        by_len.setdefault(len(w), []).append(w)
    return by_len


def _combos(by_len, n, total):
    """Every concatenation of n pool words with exactly `total` characters (shortest-first friendly)."""
    if n == 0:
        if total == 0:
            yield ""
        return
    if not by_len:
        return
    if n == 1:
        yield from by_len.get(total, ())
        return
    lens = sorted(by_len)
    minl, maxl = lens[0], lens[-1]
    for l in lens:
        rest = total - l
        if rest < (n - 1) * minl:
            break
        if rest > (n - 1) * maxl:
            continue
        for w in by_len[l]:
            for tail in _combos(by_len, n - 1, rest):
                yield w + tail


def _ways(h, n, total, memo):
    """How many ordered n-word sequences have `total` characters, given a length histogram."""
    if n == 0:
        return 1 if total == 0 else 0
    key = (n, total)
    if key not in memo:
        if n == 1:
            memo[key] = h.get(total, 0)
        else:
            memo[key] = sum(c * _ways(h, n - 1, total - l, memo) for l, c in h.items() if l < total)
    return memo[key]


def count_names(p):
    """Number of names the plan can produce (an upper bound where duplicates are possible)."""
    lo, hi, must, place = p["lo"], p["hi"], p["must"], p["place"]
    lf = len(must)
    total = 0
    if p["words"] is not None:
        h = {l: len(v) for l, v in _group(p["words"]).items()}
        memo = {}
        for k in range(p["wmin"], p["wmax"] + 1):
            for L in range(lo, hi + 1):
                if not lf:
                    total += _ways(h, k, L, memo)
                elif L >= lf:
                    m = k - 1
                    pos = m + 1 if (place == "any" and m > 0) else 1
                    total += pos * _ways(h, m, L - lf, memo)
    elif lf:
        c = len(p["chars"])
        for L in range(max(lo, lf), hi + 1):
            rest = L - lf
            total += (rest + 1 if place == "any" else 1) * c ** rest
    else:
        total = sum(len(p["chars"]) ** n for n in range(lo, hi + 1))
    return min(total, p["limit"]) if p["limit"] else total


def _dict_names(p):
    by_len = _group(p["words"])
    must, place, lf = p["must"], p["place"], len(p["must"])
    for L in range(p["lo"], p["hi"] + 1):
        for k in range(p["wmin"], p["wmax"] + 1):
            if not lf:
                yield from _combos(by_len, k, L)
                continue
            m, rest = k - 1, L - lf
            if rest < 0:
                continue
            if m == 0:
                if rest == 0:
                    yield must
            elif place == "start":
                for t in _combos(by_len, m, rest):
                    yield must + t
            elif place == "end":
                for t in _combos(by_len, m, rest):
                    yield t + must
            else:
                for pos in range(m + 1):
                    for lt in range(rest + 1):
                        for left in _combos(by_len, pos, lt):
                            for right in _combos(by_len, m - pos, rest - lt):
                                yield left + must + right


def _char_names(p):
    chars, must, place, lf = p["chars"], p["must"], p["place"], len(p["must"])
    for L in range(max(p["lo"], lf), p["hi"] + 1):
        rest = L - lf
        splits = (0,) if place == "start" else (rest,) if place == "end" else range(rest + 1)
        for fill in itertools.product(chars, repeat=rest):
            fill = "".join(fill)
            for sp in splits:
                name = fill[:sp] + must + fill[sp:]
                if ".." in name:
                    continue
                if place == "any" and name.find(must) != sp:
                    continue        # same name already produced with the word at its first position
                yield name


def _sequential_names(p):
    if p["words"] is not None:
        yield from _dict_names(p)
    elif p["must"]:
        yield from _char_names(p)
    else:
        chars = p["chars"]
        for length in range(p["lo"], p["hi"] + 1):
            for combo in itertools.product(chars, repeat=length):
                name = "".join(combo)
                if ".." in name:  # Discord disallows consecutive periods
                    continue
                yield name


def _random_candidate(p):
    lo, hi, must, place, lf = p["lo"], p["hi"], p["must"], p["place"], len(p["must"])
    if p["words"] is not None:
        k = random.randint(p["wmin"], p["wmax"])
        m = k - 1 if lf else k
        parts = random.choices(p["words"], k=m) if m else []
        if lf:
            at = 0 if place == "start" else m if place == "end" else random.randint(0, m)
            parts.insert(at, must)
        name = "".join(parts)
    elif lf:
        lmin = max(lo, lf)
        if lmin > hi:
            return None
        rest = random.randint(lmin, hi) - lf
        fill = "".join(random.choices(p["chars"], k=rest)) if rest else ""
        sp = 0 if place == "start" else rest if place == "end" else random.randint(0, rest)
        name = fill[:sp] + must + fill[sp:]
        if place == "any" and name.find(must) != sp:
            return None
    else:
        name = "".join(random.choices(p["chars"], k=random.randint(lo, hi)))
    if ".." in name or not (lo <= len(name) <= hi):
        return None
    return name


def _random_names(p):
    seen, misses = set(), 0
    while misses < 200000:        # stop once (nearly) everything has been produced
        name = _random_candidate(p)
        if name is None or name in seen:
            misses += 1
            continue
        misses = 0
        seen.add(name)
        yield name


def sample_names(p, n=6, scan=20000):
    """A few example names for the current settings (never scans more than `scan` candidates)."""
    ex, out = p.get("exclude") or (), []
    try:
        for i, name in enumerate(name_stream(dict(p, limit=0, exclude=()))):
            if not any(x in name for x in ex):
                out.append(name)
            if len(out) >= n or i >= scan:
                break
    except Exception:
        pass
    return out


def name_stream(p):
    """Yield candidate usernames for a plan (see above), honouring p['limit']."""
    gen = _sequential_names(p) if p["sequential"] else _random_names(p)
    pre, suf = p.get("prefix", ""), p.get("suffix", "")
    if pre or suf:   # text added to the start / end of every generated name
        gen = (pre + n + suf for n in gen if ".." not in pre + n + suf)
    ex = p.get("exclude") or ()
    if ex:   # names containing any excluded text are skipped
        gen = (n for n in gen if not any(x in n for x in ex))
    limit = p["limit"]
    for i, name in enumerate(gen):
        if limit and i >= limit:
            return
        yield name



# ================================================================ widgets
def font(size=10, weight="normal"):
    return (UI_FONT, max(7, size + TEXT_ADJ), weight)


def mono(size=10):
    return (MONO_FONT, max(7, size + TEXT_ADJ))


def draw_icon(c, name, cx, cy, color, tag="ic", k=1.0):
    """Tiny line icons drawn with canvas primitives (no icon font needed)."""
    u = S(9) * k
    w = max(2, S(2))
    ln = dict(fill=color, width=w, capstyle="round", tags=tag)
    ol = dict(outline=color, fill="", width=w, tags=tag)
    if name == "menu":
        for dy in (-0.7, 0, 0.7):
            c.create_line(cx - u, cy + dy * u, cx + u, cy + dy * u, **ln)
    elif name == "home":
        c.create_polygon(cx - u, cy, cx, cy - u, cx + u, cy, cx + 0.7 * u, cy, cx + 0.7 * u, cy + u,
                         cx - 0.7 * u, cy + u, cx - 0.7 * u, cy, joinstyle="round", **ol)
    elif name == "search":
        r = 0.62 * u
        c.create_oval(cx - 0.2 * u - r, cy - 0.2 * u - r, cx - 0.2 * u + r, cy - 0.2 * u + r, **ol)
        c.create_line(cx + 0.25 * u, cy + 0.25 * u, cx + 0.95 * u, cy + 0.95 * u, **ln)
    elif name == "book":
        c.create_rectangle(cx - 0.8 * u, cy - u, cx + 0.8 * u, cy + u, **ol)
        c.create_line(cx - 0.35 * u, cy - u, cx - 0.35 * u, cy + u, **ln)
        c.create_line(cx, cy - 0.35 * u, cx + 0.5 * u, cy - 0.35 * u, **ln)
        c.create_line(cx, cy + 0.1 * u, cx + 0.5 * u, cy + 0.1 * u, **ln)
    elif name == "webhook":
        c.create_rectangle(cx - u, cy - 0.7 * u, cx + u, cy + 0.7 * u, **ol)
        c.create_line(cx - u, cy - 0.7 * u, cx, cy + 0.1 * u, cx + u, cy - 0.7 * u, **ln)
    elif name == "user":
        c.create_oval(cx - 0.42 * u, cy - u, cx + 0.42 * u, cy - 0.16 * u, **ol)
        c.create_arc(cx - 0.9 * u, cy + 0.1 * u, cx + 0.9 * u, cy + 1.9 * u, start=0, extent=180,
                     style="arc", outline=color, width=w, tags=tag)
    elif name == "palette":
        c.create_oval(cx - u, cy - u, cx + u, cy + u, **ol)
        for dx, dy in ((-0.4, -0.35), (0.35, -0.4), (0.45, 0.25)):
            r = 0.14 * u
            c.create_oval(cx + dx * u - r, cy + dy * u - r, cx + dx * u + r, cy + dy * u + r,
                          fill=color, outline=color, tags=tag)
    elif name == "gear":
        c.create_oval(cx - 0.5 * u, cy - 0.5 * u, cx + 0.5 * u, cy + 0.5 * u, **ol)
        for i in range(8):
            a = i * math.pi / 4
            c.create_line(cx + 0.78 * u * math.cos(a), cy + 0.78 * u * math.sin(a),
                          cx + 1.05 * u * math.cos(a), cy + 1.05 * u * math.sin(a), **ln)
    elif name == "play":
        c.create_polygon(cx - 0.55 * u, cy - 0.8 * u, cx - 0.55 * u, cy + 0.8 * u, cx + 0.85 * u, cy,
                         fill=color, outline=color, tags=tag)
    elif name == "list":
        for dy in (-0.7, 0, 0.7):
            c.create_oval(cx - u, cy + dy * u - 1.5, cx - 0.65 * u, cy + dy * u + 1.5, fill=color, outline=color, tags=tag)
            c.create_line(cx - 0.3 * u, cy + dy * u, cx + u, cy + dy * u, **ln)
    elif name == "pause":
        for dx in (-0.55, 0.2):
            c.create_rectangle(cx + dx * u, cy - 0.75 * u, cx + (dx + 0.35) * u, cy + 0.75 * u,
                               fill=color, outline=color, tags=tag)
    elif name == "stop":
        c.create_rectangle(cx - 0.65 * u, cy - 0.65 * u, cx + 0.65 * u, cy + 0.65 * u,
                           fill=color, outline=color, tags=tag)


def _text_w(text, size, weight):
    return tkfont.Font(family=UI_FONT, size=max(7, size + TEXT_ADJ), weight=weight).measure(text)


class Pill(tk.Canvas):
    """Rounded button. kind: primary (white), secondary (grey), danger (red), ghost, chip, chip_on."""

    @staticmethod
    def _colors(kind, enabled):
        if not enabled:
            return CHIP, lerp_color(MUTED, CHIP, 0.55), CHIP
        return {
            "primary": (TEXT, BG0, lerp_color(TEXT, "#000000", 0.14)),
            "secondary": (CHIP, TEXT, CHIP_HOVER),
            "danger": (T("#cc0000"), "#ffffff", T("#ff1a1a")),
            "ghost": (BG0, TEXT, CHIP),
            "chip": (CHIP, TEXT, CHIP_HOVER),
            "chip_on": (TEXT, BG0, TEXT),
        }[kind]

    def __init__(self, parent, text, command=None, kind="secondary", icon=None, width=None, height=36, size=10):
        self.text, self.command, self.kind, self.icon, self.size = text, command, kind, icon, size
        self.enabled, self.hover, self.down = True, False, False
        self.h = S(height)
        self.fixed_w = S(width) if width else None
        super().__init__(parent, height=self.h, width=self._measure(), bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _measure(self):
        if self.fixed_w:
            return self.fixed_w
        w = _text_w(self.text, self.size, "bold") + S(32)
        return w + (S(22) if self.icon else 0)

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self._draw()

    def _release(self, e):
        was_down = self.down
        self._set(down=False)
        if was_down and self.enabled and self.command and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.h:
            self.command()

    def set_text(self, text):
        self.text = text
        self.configure(width=self._measure())
        self._draw()

    def set_label(self, text, icon):
        self.text, self.icon = text, icon
        self.configure(width=self._measure())
        self._draw()

    def set_kind(self, kind):
        self.kind = kind
        self._draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _draw(self):
        self.delete("all")
        w = int(self.cget("width"))
        base, fg, hov = self._colors(self.kind, self.enabled)
        fill = lerp_color(hov, "#000000", 0.12) if (self.down and self.enabled) else \
            hov if (self.hover and self.enabled) else base
        rrect(self, 1, 1, w - 1, self.h - 1, self.h / 2, fill=fill, outline=fill)
        if self.icon:
            tw = _text_w(self.text, self.size, "bold")
            x0 = (w - (tw + S(22))) / 2
            draw_icon(self, self.icon, x0 + S(8), self.h / 2, fg, k=0.8)
            self.create_text(x0 + S(22), self.h / 2, text=self.text, fill=fg, anchor="w",
                             font=font(self.size, "bold"))
        else:
            self.create_text(w / 2, self.h / 2, text=self.text, fill=fg, font=font(self.size, "bold"))


class IconButton(tk.Canvas):
    """Round, transparent icon button (hamburger, palette...)."""

    def __init__(self, parent, icon, command, size=40):
        self.icon, self.command, self.d = icon, command, S(size)
        super().__init__(parent, width=self.d, height=self.d, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self._draw(True))
        self.bind("<Leave>", lambda e: self._draw(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command() if 0 <= e.x <= self.d and 0 <= e.y <= self.d else None)
        self._draw(False)

    def _draw(self, hover):
        self.delete("all")
        if hover:
            self.create_oval(1, 1, self.d - 1, self.d - 1, fill=CHIP, outline=CHIP)
        draw_icon(self, self.icon, self.d / 2, self.d / 2, TEXT)


class Tip:
    """Small hover tooltip."""

    def __init__(self, widget, text):
        self.w, self.text, self.tw, self.job = widget, text, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e=None):
        self._leave()
        self.job = self.w.after(550, self._show)

    def _show(self):
        self.job = None
        try:
            x = self.w.winfo_rootx() + S(8)
            y = self.w.winfo_rooty() + self.w.winfo_height() + S(6)
            self.tw = tk.Toplevel(self.w)
            self.tw.wm_overrideredirect(True)
            self.tw.wm_geometry(f"+{x}+{y}")
            box = tk.Frame(self.tw, bg=BORDER)
            box.pack()
            tk.Label(box, text=self.text, bg=CHIP, fg=TEXT, font=font(9), padx=S(8), pady=S(4)).pack(padx=1, pady=1)
        except tk.TclError:
            self.tw = None

    def _leave(self, _e=None):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.tw is not None:
            try:
                self.tw.destroy()
            except tk.TclError:
                pass
            self.tw = None


def tip(widget, text):
    Tip(widget, text)
    return widget


class Avatar(tk.Canvas):
    def __init__(self, parent, size=34):
        self.d = S(size)
        self.letter = ""
        super().__init__(parent, width=self.d, height=self.d, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._draw()

    def set_letter(self, letter):
        self.letter = (letter or "")[:1].upper()
        self._draw()

    def _draw(self):
        self.delete("all")
        col = T("#8e24aa") if self.letter else CHIP_HOVER
        self.create_oval(1, 1, self.d - 1, self.d - 1, fill=col, outline=col)
        if self.letter:
            self.create_text(self.d / 2, self.d / 2, text=self.letter, fill="#ffffff", font=font(12, "bold"))
        else:
            draw_icon(self, "user", self.d / 2, self.d / 2 - S(2), TEXT, k=0.8)


class Logo(tk.Canvas):
    def __init__(self, parent):
        self.w, self.h = S(150), S(30)
        super().__init__(parent, width=self.w, height=self.h, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        rrect(self, 1, S(5), S(32), S(25), S(6), fill=ACCENT, outline=ACCENT)
        self.create_polygon(S(13), S(10), S(13), S(20), S(21), S(15), fill="#ffffff", outline="#ffffff")
        self.create_text(S(40), self.h / 2, text="DSV", anchor="w", fill=TEXT, font=font(15, "bold"))
        self.create_text(S(86), self.h / 2 + S(3), text="Scanner", anchor="w", fill=MUTED, font=font(9))


class Toggle(tk.Frame):
    """Switch in the same style as the video site's settings toggles."""

    def __init__(self, parent, text, variable, command=None):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.var, self.command, self.bgc = variable, command, bg
        self.cv = tk.Canvas(self, width=S(40), height=S(22), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(side="left")
        lbl = tk.Label(self, text=text, bg=bg, fg=TEXT, font=font(10), cursor="hand2")
        lbl.pack(side="left", padx=(S(10), 0))
        for w in (self.cv, lbl):
            w.bind("<Button-1>", self.toggle)
        self._tid = variable.trace_add("write", lambda *a: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, e):
        if e.widget is self:
            try:
                self.var.trace_remove("write", self._tid)
            except Exception:
                pass

    def toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        c, on = self.cv, bool(self.var.get())
        c.delete("all")
        track = lerp_color(ACCENT2, self.bgc, 0.45) if on else T("#717171")
        rrect(c, S(3), S(4), S(37), S(18), S(7), fill=track, outline=track)
        x = S(28) if on else S(11)
        knob = ACCENT2 if on else TEXT
        c.create_oval(x - S(9), S(11) - S(9), x + S(9), S(11) + S(9), fill=knob, outline=knob)


class Field(tk.Frame):
    """Text input with a thin outline that turns blue on focus."""

    def __init__(self, parent, textvariable, width=20, show=""):
        super().__init__(parent, bg=BORDER)
        inner = tk.Frame(self, bg=BG2)
        inner.pack(fill="x", padx=1, pady=1)
        self.entry = tk.Entry(inner, textvariable=textvariable, width=width, bg=BG2, fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0,
                              font=font(10), show=show, selectbackground=T("#265a8f"), selectforeground="#ffffff")
        self.entry.pack(fill="x", padx=S(10), pady=S(7))
        self.entry.bind("<FocusIn>", lambda e: self.configure(bg=ACCENT2))
        self.entry.bind("<FocusOut>", lambda e: self.configure(bg=BORDER))


class Chips(tk.Frame):
    """Row of rounded choice chips bound to a StringVar (selected chip is white)."""

    def __init__(self, parent, options, variable, command=None, size=10, height=32):
        super().__init__(parent, bg=parent.cget("bg"))
        self.var, self.command = variable, command
        self.chips = {}
        for label, value in options:
            p = Pill(self, label, lambda v=value: self._pick(v), kind="chip", size=size, height=height)
            p.pack(side="left", padx=(0, S(8)))
            self.chips[value] = p
        self._tid = variable.trace_add("write", lambda *a: self._sync())
        self.bind("<Destroy>", self._on_destroy)
        self._sync()

    def _on_destroy(self, e):
        if e.widget is self:
            try:
                self.var.trace_remove("write", self._tid)
            except Exception:
                pass

    def _pick(self, value):
        self.var.set(value)
        if self.command:
            self.command()

    def _sync(self):
        for value, p in self.chips.items():
            p.set_kind("chip_on" if self.var.get() == value else "chip")


class SideItem(tk.Canvas):
    """Sidebar entry: icon + label, or (collapsed) icon over a tiny label."""

    def __init__(self, parent, icon, text, command, expanded, active):
        self.icon, self.text, self.command, self.expanded, self.active = icon, text, command, expanded, active
        self.hover = False
        h = S(42) if expanded else S(68)
        super().__init__(parent, height=h, bg=BG0, highlightthickness=0, bd=0, cursor="hand2")
        if not expanded:
            self.configure(width=S(66))
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command() if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= int(self.cget("height")) else None)

    def _hov(self, v):
        self.hover = v
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if self.active or self.hover:
            rrect(self, S(2), S(1), w - S(2), h - S(1), S(10), fill=CHIP if self.active else lerp_color(BG0, CHIP, 0.7),
                  outline="")
        weight = "bold" if self.active else "normal"
        if self.expanded:
            draw_icon(self, self.icon, S(28), h / 2, TEXT)
            self.create_text(S(58), h / 2, text=self.text, anchor="w", fill=TEXT, font=font(10, weight))
        else:
            draw_icon(self, self.icon, w / 2, h * 0.36, TEXT)
            self.create_text(w / 2, h * 0.74, text=self.text, fill=TEXT, font=font(8, weight))


class ProgressBar(tk.Canvas):
    """Thin red progress line with a round scrubber, like a video seek bar."""

    def __init__(self, parent):
        super().__init__(parent, height=S(16), bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.target = self.shown = 0.0
        self._running = False
        self.bind("<Configure>", lambda e: self._paint())
        self._paint()

    def set(self, fraction):
        self.target = max(0.0, min(1.0, fraction))
        self._kick()

    def reset(self):
        self.target = self.shown = 0.0
        self._paint()

    def _kick(self):
        if not self._running:
            self._running = True
            self._tick()

    def _tick(self):
        if not self.winfo_exists():
            self._running = False
            return
        self.shown += (self.target - self.shown) * 0.2
        if abs(self.target - self.shown) < 0.0008:
            self.shown = self.target
            self._running = False
        self._paint()
        if self._running:
            self.after(33, self._tick)

    def _paint(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if w < 10:
            return
        y, th = h / 2, S(4)
        pad = S(7)
        self.create_rectangle(pad, y - th / 2, w - pad, y + th / 2, fill=CHIP_HOVER, outline="")
        x = pad + (w - 2 * pad) * self.shown
        if self.shown > 0:
            self.create_rectangle(pad, y - th / 2, x, y + th / 2, fill=ACCENT, outline="")
            r = S(6)
            self.create_oval(x - r, y - r, x + r, y + r, fill=ACCENT, outline=ACCENT)


class StatCard(tk.Frame):
    def __init__(self, parent, caption, color):
        super().__init__(parent, bg=BG1)
        tk.Frame(self, bg=color, height=S(3)).pack(fill="x")
        tk.Label(self, text=caption, bg=BG1, fg=MUTED, font=font(9)).pack(anchor="w", padx=S(14), pady=(S(10), 0))
        self.value = tk.Label(self, text="0", bg=BG1, fg=color, font=font(22, "bold"))
        self.value.pack(anchor="w", padx=S(14), pady=(0, S(10)))

    def set(self, n):
        text = f"{n:,}"
        if self.value.cget("text") != text:
            self.value.configure(text=text)


class SearchPill(tk.Canvas):
    """The big rounded bar in the top bar. Here it holds the 'must contain' text."""

    def __init__(self, parent, var, placeholder, height=40):
        self.h = S(height)
        super().__init__(parent, height=self.h, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.var = var
        self.entry = tk.Entry(self, textvariable=var, bg=BG2, fg=TEXT, insertbackground=TEXT, relief="flat",
                              bd=0, highlightthickness=0, font=font(11), selectbackground=T("#265a8f"),
                              selectforeground="#ffffff")
        self.hint = tk.Label(self, text=placeholder, bg=BG2, fg=MUTED, font=font(11), cursor="xterm", anchor="w")
        self.hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.focused = False
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        self._tid = var.trace_add("write", lambda *a: self._hint())
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, e):
        if e.widget is self:
            try:
                self.var.trace_remove("write", self._tid)
            except Exception:
                pass

    def _focus(self, on):
        self.focused = on
        self._layout()

    def _hint(self):
        if self.var.get() or self.focused:
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, x=0, y=0, relwidth=1, relheight=1)

    def _layout(self):
        w, h = self.winfo_width(), self.h
        if w < 40:
            return
        self.delete("all")
        edge = ACCENT2 if self.focused else BORDER
        btn = S(64)
        rrect(self, 1, 1, w - 1, h - 1, h / 2, fill=BG2, outline=edge, width=1)
        # right-hand search button segment
        self.create_arc(w - btn - h / 2, 1, w - 1, h - 1, start=-90, extent=180, fill=CHIP, outline=edge)
        self.create_rectangle(w - btn, 2, w - h / 2 - 1, h - 2, fill=CHIP, outline=CHIP)
        self.create_line(w - btn, 1, w - btn, h - 1, fill=edge)
        draw_icon(self, "search", w - btn / 2 - S(3), h / 2, TEXT, k=0.9)
        self.create_window(S(20), h / 2, window=self.entry, anchor="w", width=max(20, w - btn - S(34)), height=h - S(14))
        self._hint()


def wheel_px(e):
    """How many pixels one wheel event should scroll (positive = down), for any platform."""
    num = getattr(e, "num", 0)
    if num == 4:
        notches = 1.0
    elif num == 5:
        notches = -1.0
    else:
        d = getattr(e, "delta", 0)
        notches = (d / 4.0) if sys.platform == "darwin" else (d / 120.0)
    return -notches * S(64) * SCROLL_SPEEDS.get(SCROLL_SPEED, 1.0)


class SmoothText:
    """Gives a Text widget eased, pixel-by-pixel mouse-wheel scrolling."""

    def __init__(self, text):
        self.t, self.pending, self.job = text, 0.0, None
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            text.bind(ev, self._wheel)

    def _wheel(self, e):
        try:
            if self.t.yview() == (0.0, 1.0):
                return None                    # nothing to scroll here: let the page scroll instead
        except tk.TclError:
            return None
        self.pending += wheel_px(e)
        if not SMOOTH_SCROLL:
            n, self.pending = int(self.pending), 0.0
            if n:
                self.t.yview_scroll(n, "pixels")
        elif self.job is None:
            self._tick()
        return "break"

    def _tick(self):
        self.job = None
        if not self.t.winfo_exists() or abs(self.pending) < 1:
            self.pending = 0.0
            return
        n = int(self.pending * 0.25)
        if n == 0:
            n = 1 if self.pending > 0 else -1
        before = self.t.yview()
        self.t.yview_scroll(n, "pixels")
        if self.t.yview() == before:           # hit the top or bottom
            self.pending = 0.0
            return
        self.pending -= n
        if abs(self.pending) >= 1:
            self.job = self.t.after(16, self._tick)
        else:
            self.pending = 0.0


class ScrollFrame(tk.Frame):
    """A page that scrolls vertically when it is taller than the window.
    The mouse wheel / Page Up / Page Down glide to their target instead of jumping."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG0)
        self.cv = tk.Canvas(self, bg=BG0, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", style="Yt.Vertical.TScrollbar", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.cv, bg=BG0)
        self._win = self.cv.create_window(0, 0, window=self.body, anchor="nw")
        self._sb_on = False
        self.target, self._cur, self._anim, self._job = 0.0, 0.0, False, None
        self.body.bind("<Configure>", lambda e: self._sync())
        self.cv.bind("<Configure>", lambda e: self._sync())
        self.sb.bind("<ButtonPress-1>", lambda e: self._stop())    # dragging the bar takes over

    def _sync(self):
        try:
            w, h = self.cv.winfo_width(), self.cv.winfo_height()
            self.cv.itemconfigure(self._win, width=max(1, w - S(14)))
            box = self.cv.bbox("all")
            need = bool(box) and (box[3] - box[1]) > h + 2
            self.cv.configure(scrollregion=(0, 0, w, box[3]) if box else (0, 0, w, h))
            if need and not self._sb_on:
                self.sb.pack(side="right", fill="y")
                self._sb_on = True
            elif not need and self._sb_on:
                self.sb.pack_forget()
                self._sb_on = False
                self._stop()
                self.cv.yview_moveto(0)
        except tk.TclError:
            pass

    # ---- smooth scrolling
    def _extent(self):
        box = self.cv.bbox("all")
        return (float(box[3]) if box else 0.0), float(self.cv.winfo_height())

    def _apply(self, offset, total):
        self.cv.yview_moveto(offset / total if total else 0.0)

    def _stop(self):
        self._anim = False
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def wheel(self, px):
        """Scroll by `px` pixels (positive = down), easing towards the new position."""
        if not self._sb_on or not px:
            return
        total, view = self._extent()
        if total <= view:
            return
        base = self.target if self._anim else self.cv.yview()[0] * total
        self.target = max(0.0, min(total - view, base + px))
        if not SMOOTH_SCROLL:
            self._stop()
            self._apply(self.target, total)
        elif not self._anim:
            self._cur, self._anim = base, True
            self._tick()

    def page(self, direction):
        """Page Up (-1) / Page Down (+1)."""
        self.wheel(direction * self._extent()[1] * 0.9)

    def _tick(self):
        self._job = None
        if not self._anim or not self.winfo_exists():
            self._anim = False
            return
        total, view = self._extent()
        self.target = max(0.0, min(max(0.0, total - view), self.target))
        diff = self.target - self._cur
        if abs(diff) < 0.6:
            self._cur, self._anim = self.target, False
            self._apply(self._cur, total)
            return
        self._cur += diff * 0.2
        self._apply(self._cur, total)
        self._job = self.after(16, self._tick)


def panel(parent, title=None, subtitle=None, pad=16):
    """Flat grey card with an optional heading. Returns (outer frame, inner body frame)."""
    f = tk.Frame(parent, bg=BG1)
    if title:
        tk.Label(f, text=title, bg=BG1, fg=TEXT, font=font(12, "bold")).pack(anchor="w", padx=S(pad), pady=(S(pad), 0))
    if subtitle:
        tk.Label(f, text=subtitle, bg=BG1, fg=MUTED, font=font(9), justify="left",
                 wraplength=S(430), anchor="w").pack(anchor="w", padx=S(pad), pady=(S(2), 0))
    body = tk.Frame(f, bg=BG1)
    body.pack(fill="both", expand=True, padx=S(pad), pady=(S(10) if title else S(pad), S(pad)))
    return f, body


def small(parent, text, **kw):
    kw.setdefault("bg", parent.cget("bg"))
    kw.setdefault("fg", MUTED)
    kw.setdefault("font", font(9))
    return tk.Label(parent, text=text, **kw)


def note(parent, text, wrap=430):
    return small(parent, text, justify="left", wraplength=S(wrap), anchor="w")


def page_head(parent, title, sub):
    tk.Label(parent, text=title, bg=BG0, fg=TEXT, font=font(20, "bold")).pack(anchor="w")
    tk.Label(parent, text=sub, bg=BG0, fg=MUTED, font=font(10)).pack(anchor="w", pady=(S(2), S(14)))


class GradientSlider(tk.Canvas):
    """Horizontal slider drawn over a colour gradient. value is 0..1."""

    def __init__(self, parent, value, stops_fn, on_drag, on_release, width=360, height=26):
        self.w, self.h = S(width), S(height)
        super().__init__(parent, width=self.w, height=self.h, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.value, self.stops_fn = max(0.0, min(1.0, value)), stops_fn
        self.on_drag, self.on_release = on_drag, on_release
        self.bind("<ButtonPress-1>", self._move)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._up)
        self._draw()

    def _move(self, e):
        pad = S(8)
        self.value = max(0.0, min(1.0, (e.x - pad) / max(1, self.w - 2 * pad)))
        self._draw()
        self.on_drag(self.value)

    def _up(self, e):
        self._move(e)
        self.on_release(self.value)

    def _draw(self):
        self.delete("all")
        pad, n = S(8), 48
        ty1, ty2 = S(9), self.h - S(9)
        stops = self.stops_fn()
        span = self.w - 2 * pad
        for i in range(n):
            t = i / (n - 1)
            k = t * (len(stops) - 1)
            j = min(len(stops) - 2, int(k))
            col = lerp_color(stops[j], stops[j + 1], k - j)
            self.create_rectangle(pad + span * i / n, ty1, pad + span * (i + 1) / n + 1, ty2, fill=col, outline="")
        x = pad + span * self.value
        r = S(8)
        self.create_oval(x - r, self.h / 2 - r, x + r, self.h / 2 + r, fill="#ffffff", outline=CHIP_HOVER, width=2)


def _hsv_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))



def hook_ctx(name, stats):
    """Values available as {placeholders} in the webhook message."""
    return {"username": name, "length": len(name), "time": time.strftime("%H:%M:%S"),
            "date": time.strftime("%Y-%m-%d"), "count": stats.get("available", 0) + 1,
            "checked": stats.get("checked", 0) + 1}


def build_webhook_payload(cfg, ctx):
    """Turn the message settings into the JSON Discord expects (respecting Discord's size limits)."""
    payload = {}
    content = render_template(cfg["content"], ctx).strip()
    if content:
        payload["content"] = content[:2000]
    if cfg["embed"]:
        e = {"color": cfg["color"] & 0xFFFFFF}
        title = render_template(cfg["title"], ctx).strip()
        desc = render_template(cfg["desc"], ctx).strip()
        footer = render_template(cfg["footer"], ctx).strip()
        if title:
            e["title"] = title[:256]
        if desc:
            e["description"] = desc[:4096]
        if footer:
            e["footer"] = {"text": footer[:2048]}
        if cfg["timestamp"]:
            e["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if set(e) - {"color", "timestamp"}:
            payload["embeds"] = [e]
    if not payload:   # Discord rejects empty messages
        payload["content"] = f"Username `{ctx['username']}` is available"
    return payload



# ==================================================================== app
class App(tk.Tk):
    # (key, label, icon); None = divider line
    TABS = [("home", "Home", "home"), ("search", "Search", "search"), ("words", "Dictionary", "book"),
            ("check", "Check names", "list"), None,
            ("webhook", "Webhook", "webhook"), ("account", "Account", "user"), None,
            ("look", "Appearance", "palette"), ("settings", "Settings", "gear")]
    LOG_FILTERS = {"all": None, "ok": {"ok"}, "taken": {"taken"}, "err": {"err", "warn"}}

    def __init__(self):
        super().__init__()
        global SCALE, UI_FONT, MONO_FONT
        saved = self._load_settings()
        th = saved.get("theme")
        apply_theme(th if isinstance(th, dict) and th.get("v") == THEME_VERSION else DEFAULT_THEME)
        try:
            SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            SCALE = 1.0
        fams = set(tkfont.families())
        UI_FONT = next((f for f in ("Roboto", "Segoe UI", "Inter", "Helvetica Neue", "Helvetica", "DejaVu Sans")
                        if f in fams), "TkDefaultFont")
        MONO_FONT = next((f for f in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono")
                          if f in fams), "TkFixedFont")

        self.title(APP_TITLE)
        self.configure(bg=BG0)
        w = min(S(1240), self.winfo_screenwidth() - 60)
        h = min(S(820), self.winfo_screenheight() - 90)
        self.geometry(f"{w}x{h}+{max(0, (self.winfo_screenwidth() - w) // 2)}+{max(0, (self.winfo_screenheight() - h) // 3)}")
        self.minsize(min(S(1060), w), min(S(700), h))
        self._restore_geometry(saved.get("geometry"), w, h)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.found = []
        self.stats = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
        self.total, self.start_time = 0, 0.0
        self._toast_job = None
        self._log_hist = collections.deque(maxlen=3000)
        self._est_labels = []
        self._windows, self._builders, self._avatar_path = {}, {}, ""
        self._status = ("Idle", "idle")
        self._draft = dict(THEME)
        self._hook_info = ("", "muted")
        self.tab, self.side_expanded = "home", True
        self.pause_event = threading.Event()   # set = scan paused
        self._pause_at, self._pace = 0.0, 0.0
        self.presets = {}

        # ---- variables (loaded from last session)
        self.token_var = tk.StringVar()
        self.show_var = tk.BooleanVar(value=False)
        self.remember_var = tk.BooleanVar(value=False)
        self.min_var = tk.StringVar(value="2")
        self.max_var = tk.StringVar(value="3")
        self.dmin_var = tk.StringVar(value="2")
        self.dmax_var = tk.StringVar(value="4")
        self.break_every_var = tk.StringVar(value="0")
        self.break_secs_var = tk.StringVar(value="30")
        self.timelimit_var = tk.StringVar(value="0")
        self.prefix_var = tk.StringVar()
        self.suffix_var = tk.StringVar()
        self.chars_var = tk.StringVar()
        self.logts_var = tk.BooleanVar(value=True)
        self.logtaken_var = tk.BooleanVar(value=True)
        self.ontop_var = tk.BooleanVar(value=False)
        self.autocopy_var = tk.BooleanVar(value=False)
        self.smooth_var = tk.BooleanVar(value=True)
        self.scrollspeed_var = tk.StringVar(value="normal")
        self.results_path = ""
        self.error_names = []
        self.accounts = []                      # saved tokens: {"label", "token", "name", "bad"}
        self.acct_name_var = tk.StringVar()     # label typed for "Add current token" / "Rename"
        self._tok = ""                          # token the running scan uses for its next check
        self.limit_var = tk.StringVar(value="0")
        self.letters_var = tk.BooleanVar(value=True)
        self.digits_var = tk.BooleanVar(value=True)
        self.underscore_var = tk.BooleanVar(value=False)
        self.period_var = tk.BooleanVar(value=False)
        self.mode_var = tk.StringVar(value="shortest")
        self.hook_on = tk.BooleanVar(value=False)
        self.hook_var = tk.StringVar()
        self.hook_name_var = tk.StringVar()
        self.hook_content_var = tk.StringVar(value=DEFAULT_HOOK["content"])
        self.hook_embed_var = tk.BooleanVar(value=DEFAULT_HOOK["embed"])
        self.hook_title_var = tk.StringVar(value=DEFAULT_HOOK["title"])
        self.hook_desc_var = tk.StringVar(value=DEFAULT_HOOK["desc"])
        self.hook_footer_var = tk.StringVar(value=DEFAULT_HOOK["footer"])
        self.hook_color_var = tk.StringVar(value=DEFAULT_HOOK["color"])
        self.hook_ts_var = tk.BooleanVar(value=DEFAULT_HOOK["timestamp"])
        self.wmin_var = tk.StringVar(value="1")
        self.wmax_var = tk.StringVar(value="1")
        self.must_var = tk.StringVar()
        self.place_var = tk.StringVar(value="any")
        self.exclude_var = tk.StringVar()
        self.stopafter_var = tk.StringVar(value="0")
        self.skipknown_var = tk.BooleanVar(value=True)
        self.sound_var = tk.BooleanVar(value=True)
        self.hook_done_var = tk.BooleanVar(value=False)
        self.preset_name_var = tk.StringVar()
        self.log_filter = tk.StringVar(value="all")
        self.dict_spec = ""            # "" = off, a language code, or "file" (custom list)
        self.dict_file = ""
        self.dict_caps_var = tk.BooleanVar(value=True)
        self._dict_cache, self._dict_downloading, self._pool_cache = {}, set(), (None, [])
        self._apply_settings(saved)

        self._style()
        self._build_ui()
        for v in (self.min_var, self.max_var, self.dmin_var, self.dmax_var, self.limit_var, self.dict_caps_var,
                  self.prefix_var, self.suffix_var, self.chars_var, self.timelimit_var, self.break_every_var,
                  self.wmin_var, self.wmax_var, self.must_var, self.place_var, self.letters_var,
                  self.digits_var, self.underscore_var, self.period_var, self.exclude_var):
            v.trace_add("write", lambda *_: self._update_estimate())
        self.log_filter.trace_add("write", lambda *_: self._render_log())
        self.logts_var.trace_add("write", lambda *_: self._render_log())
        self.ontop_var.trace_add("write", lambda *_: self._apply_topmost())
        self.smooth_var.trace_add("write", lambda *_: self._apply_scroll_prefs())
        self.scrollspeed_var.trace_add("write", lambda *_: self._apply_scroll_prefs())
        self._apply_scroll_prefs()
        self._apply_topmost()
        self._update_estimate()

        self._bind_shortcuts()
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        self._style_titlebar()

    # ------------------------------------------------- window / shortcuts
    def _restore_geometry(self, geo, w, h):
        """Reopen the window where it was last closed (if that still fits on this screen)."""
        mm = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo) if isinstance(geo, str) else None
        if not mm:
            return
        try:
            gw, gh = int(mm[1]), int(mm[2])
            gx, gy = int(mm[3].replace("+-", "-")), int(mm[4].replace("+-", "-"))
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            gw = max(min(S(1060), w), min(gw, sw))
            gh = max(min(S(700), h), min(gh, sh))
            gx = max(0, min(gx, sw - min(gw, 300)))
            gy = max(0, min(gy, sh - 120))
            self.geometry(f"{gw}x{gh}+{gx}+{gy}")
        except (ValueError, tk.TclError):
            pass

    def _bind_shortcuts(self):
        keys = [e[0] for e in self.TABS if e]
        self.bind("<Control-Return>", lambda e: self.start())
        self.bind("<Escape>", lambda e: self.stop() if self._running() else None)
        self.bind("<Control-p>", lambda e: self.toggle_pause())
        self.bind("<Control-b>", lambda e: self._toggle_sidebar())
        self.bind("<Control-l>", lambda e: self.search_pill.entry.focus_set())
        self.bind("<Control-j>", lambda e: self.cycle_account(1))
        self.bind("<Control-Shift-J>", lambda e: self.cycle_account(-1))
        for i, key in enumerate(keys[:9]):
            self.bind(f"<Control-Key-{i + 1}>", lambda e, k=key: self._show_tab(k))
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(ev, self._on_wheel)
        self.bind("<Prior>", lambda e: self._on_page_key(-1))
        self.bind("<Next>", lambda e: self._on_page_key(1))

    def _on_wheel(self, e):
        page = self.pages.get(self.tab)
        if not isinstance(page, ScrollFrame):
            return
        try:
            under = self.winfo_containing(e.x_root, e.y_root)
            if under is None or not str(under).startswith(str(page)):
                return
            if under.winfo_class() in ("Listbox", "Text") and under.yview() != (0.0, 1.0):
                return                       # a list / text box that can scroll itself keeps the wheel
        except (tk.TclError, KeyError):
            return
        page.wheel(wheel_px(e))

    def _on_page_key(self, direction):
        page = self.pages.get(self.tab)
        try:
            if isinstance(page, ScrollFrame) and self.focus_get().winfo_class() not in ("Text", "Listbox"):
                page.page(direction)
        except (tk.TclError, KeyError, AttributeError):
            pass

    def _apply_scroll_prefs(self):
        global SMOOTH_SCROLL, SCROLL_SPEED
        SMOOTH_SCROLL = bool(self.smooth_var.get())
        SCROLL_SPEED = self.scrollspeed_var.get() if self.scrollspeed_var.get() in SCROLL_SPEEDS else "normal"

    def _running(self):
        return bool(self.worker and self.worker.is_alive())

    # ------------------------------------------------------ look and feel
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.layout("Yt.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
            "sticky": "ns"})])
        st.configure("Yt.Vertical.TScrollbar", background=CHIP_HOVER, troughcolor=BG1,
                     bordercolor=BG1, lightcolor=CHIP_HOVER, darkcolor=CHIP_HOVER,
                     arrowsize=S(10), gripcount=0)
        st.map("Yt.Vertical.TScrollbar", background=[("active", MUTED)])

    def _style_titlebar(self, win=None):
        if not IS_WINDOWS:
            return
        def bgr(h):
            r, g, b = _rgb(h)
            return (b << 16) | (g << 8) | r
        try:
            win = win or self
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            dwm = ctypes.windll.dwmapi
            dark = ctypes.c_int(1 if sum(_rgb(BG0)) < 380 else 0)
            for attr in (20, 19):  # dark / light title bar (new / old attribute id)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark))
            cap = ctypes.c_int(bgr(BG0))   # Windows 11 caption colour
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
            txt = ctypes.c_int(bgr(TEXT))
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))
        except Exception:
            pass

    def _build_ui(self):
        root = tk.Frame(self, bg=BG0)
        root.pack(fill="both", expand=True)
        self._root_frame = root
        self._est_labels = []
        self._build_topbar(root)
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
        body = tk.Frame(root, bg=BG0)
        body.pack(fill="both", expand=True)
        self.side = tk.Frame(body, bg=BG0)
        self.side.pack(side="left", fill="y", pady=(S(8), 0))
        self.side.pack_propagate(False)
        self._build_sidebar()
        self.content = tk.Frame(body, bg=BG0)
        self.content.pack(side="left", fill="both", expand=True, padx=(S(12), S(28)), pady=(S(20), S(16)))
        self.pages = {}
        for key, builder in (("home", self._page_home), ("search", self._page_search),
                             ("words", self._page_words), ("check", self._page_check),
                             ("webhook", self._page_webhook),
                             ("account", self._page_account), ("look", self._page_look),
                             ("settings", self._page_settings)):
            if key == "home":
                page = tk.Frame(self.content, bg=BG0)
                builder(page)
            else:                                   # every other page scrolls if the window is small
                page = ScrollFrame(self.content)
                builder(page.body)
            self.pages[key] = page
        self.pages[self.tab].pack(fill="both", expand=True)
        self.toast_lbl = tk.Label(root, text="", bg=T("#212121"), fg=TEXT, font=font(10),
                                  padx=S(14), pady=S(10))

    def _build_topbar(self, root):
        bar = tk.Frame(root, bg=BG0, height=S(58))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        left = tk.Frame(bar, bg=BG0)
        left.pack(side="left", padx=(S(14), S(10)))
        tip(IconButton(left, "menu", self._toggle_sidebar), "Collapse / expand the menu  (Ctrl+B)").pack(side="left", pady=S(9))
        Logo(left).pack(side="left", padx=(S(8), 0), pady=S(14))
        right = tk.Frame(bar, bg=BG0)
        right.pack(side="right", padx=(S(8), S(18)))
        self.avatar = Avatar(right)
        self.avatar.configure(cursor="hand2")
        self.avatar.bind("<Button-1>", lambda e: self.cycle_account(1))
        tip(self.avatar, "Click to switch to the next saved account  (Ctrl+J)")
        self.avatar.pack(side="right", pady=S(12), padx=(S(10), 0))
        tip(IconButton(right, "palette", lambda: self._show_tab("look")), "Appearance").pack(side="right", pady=S(9))
        self.stop_btn = Pill(right, "Stop", self.stop, kind="danger", icon="stop", height=36)
        self.stop_btn.pack(side="right", pady=S(11), padx=(0, S(8)))
        tip(self.stop_btn, "Stop the scan  (Esc)")
        self.pause_btn = Pill(right, "Pause", self.toggle_pause, icon="pause", height=36)
        self.pause_btn.pack(side="right", pady=S(11), padx=(0, S(8)))
        tip(self.pause_btn, "Pause / resume  (Ctrl+P)")
        self.start_btn = Pill(right, "Start", self.start, kind="primary", icon="play", height=36)
        self.start_btn.pack(side="right", pady=S(11), padx=(0, S(8)))
        tip(self.start_btn, "Start the scan  (Ctrl+Enter)")
        self.stop_btn.set_enabled(False)
        self.pause_btn.set_enabled(False)
        self.status_chip = tk.Label(right, text="", bg=BG0, fg=MUTED, font=font(9, "bold"))
        self.status_chip.pack(side="right", padx=(0, S(14)))
        self.set_status(*self._status)
        mid = tk.Frame(bar, bg=BG0)
        mid.pack(side="left", fill="both", expand=True, padx=(S(12), S(12)))
        self.search_pill = SearchPill(mid, self.must_var, "Username must contain...")
        self.search_pill.pack(fill="x", pady=S(9))

    def _build_sidebar(self):
        for ch in self.side.winfo_children():
            ch.destroy()
        exp = self.side_expanded
        self.side.configure(width=S(236) if exp else S(78))
        for entry in self.TABS:
            if entry is None:
                tk.Frame(self.side, bg=BORDER, height=1).pack(fill="x", padx=S(12), pady=S(8))
                continue
            key, label, icon = entry
            SideItem(self.side, icon, label, lambda k=key: self._show_tab(k), exp,
                     key == self.tab).pack(fill="x", padx=S(8) if exp else S(4), pady=1)
        if exp:
            tk.Label(self.side, text=CREDIT, bg=BG0, fg=lerp_color(MUTED, BG0, 0.35), font=font(8),
                     justify="left", wraplength=S(200), anchor="w").pack(side="bottom", anchor="w",
                                                                         padx=S(16), pady=S(14))

    def _toggle_sidebar(self):
        self.side_expanded = not self.side_expanded
        self._build_sidebar()

    def _show_tab(self, key):
        if key not in self.pages:
            return
        self.pages[self.tab].pack_forget()
        self.tab = key
        self.pages[key].pack(fill="both", expand=True)
        self._build_sidebar()

    def set_status(self, text, mode):
        self._status = (text, mode)
        col = {"idle": MUTED, "run": ACCENT, "warn": WARN, "err": ERR}.get(mode, MUTED)
        self.status_chip.configure(text="\u25cf " + text, fg=col)

    def toast(self, text, color=None):
        self.toast_lbl.configure(text=text, fg=color or TEXT)
        self.toast_lbl.place(x=S(24), rely=1.0, y=-S(24), anchor="sw")
        self.toast_lbl.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(2600, self._toast_hide)

    def _toast_hide(self):
        self._toast_job = None
        if self.toast_lbl.winfo_exists():
            self.toast_lbl.place_forget()

    def _make_estimate(self, parent, examples=False):
        lbl = tk.Label(parent, text="", bg=parent.cget("bg"), fg=MUTED, font=font(9), justify="left",
                       anchor="w", wraplength=S(700))
        self._est_labels.append((lbl, examples))
        return lbl

    def _fields_row(self, parent, items):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        for i, (label, var) in enumerate(items):
            row.columnconfigure(i, weight=1, uniform="f")
            col = tk.Frame(row, bg=row.cget("bg"))
            col.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else S(10), 0))
            small(col, label).pack(anchor="w", pady=(0, S(4)))
            Field(col, var, width=6).pack(fill="x")
        return row

    @staticmethod
    def _two_cols(parent):
        grid = tk.Frame(parent, bg=BG0)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="g")
        grid.columnconfigure(1, weight=1, uniform="g")
        return grid

    # ------------------------------------------------------------ pages
    def _page_home(self, f):
        top = tk.Frame(f, bg=BG0)
        top.pack(fill="x", pady=(0, S(14)))
        Chips(top, [("All", "all"), ("Available", "ok"), ("Taken", "taken"), ("Errors", "err")],
              self.log_filter).pack(side="left")
        Pill(top, "Open results", self.open_results, height=34).pack(side="right")
        Pill(top, "Save log...", self._save_log, height=34).pack(side="right", padx=(0, S(8)))
        Pill(top, "Clear log", self.clear_log, height=34).pack(side="right", padx=(0, S(8)))
        Pill(top, "Retry errors", self.retry_errors, height=34).pack(side="right", padx=(0, S(8)))

        tiles = tk.Frame(f, bg=BG0)
        tiles.pack(fill="x", pady=(0, S(14)))
        for i in range(4):
            tiles.columnconfigure(i, weight=1, uniform="t")
        self.tiles = {}
        for i, (key, cap, col) in enumerate([("checked", "Checked", TEXT), ("available", "Available", OK),
                                             ("taken", "Taken", MUTED), ("errors", "Errors", ERR)]):
            card = StatCard(tiles, cap, col)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else S(10), 0))
            self.tiles[key] = card

        prog = tk.Frame(f, bg=BG0)
        prog.pack(fill="x", pady=(0, S(10)))
        self._make_estimate(prog).pack(fill="x")
        self.progress_label = tk.Label(prog, text="Ready", bg=BG0, fg=MUTED, font=font(9), anchor="w")
        self.progress_label.pack(fill="x", pady=(S(2), S(2)))
        self.progress = ProgressBar(prog)
        self.progress.pack(fill="x")

        area = tk.Frame(f, bg=BG0)
        area.pack(fill="both", expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        lp, lb = panel(area, "Live log", pad=12)
        lp.grid(row=0, column=0, sticky="nsew")
        wrap = tk.Frame(lb, bg=BG1)
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, bg=BG1, fg=TEXT, wrap="none", state="disabled", bd=0, highlightthickness=0,
                           font=mono(10), padx=4, pady=4, insertwidth=0, cursor="arrow",
                           selectbackground=T("#265a8f"))
        sb = ttk.Scrollbar(wrap, orient="vertical", style="Yt.Vertical.TScrollbar", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        SmoothText(self.log)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        dim = lerp_color(MUTED, BG1, 0.45)
        self.log.tag_configure("ts", foreground=dim)
        self.log.tag_configure("info", foreground=MUTED)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("taken", foreground=dim)
        self.log.tag_configure("err", foreground=ERR)

        fp, fb = panel(area, "Available names", "Double-click a name to copy it.", pad=12)
        fp.grid(row=0, column=1, sticky="ns", padx=(S(12), 0))
        self.found_list = tk.Listbox(fb, width=20, bg=BG1, fg=OK, bd=0, highlightthickness=0,
                                     font=mono(11), activestyle="none", selectmode="extended",
                                     selectbackground=T("#265a8f"), selectforeground="#ffffff")
        self.found_list.pack(fill="both", expand=True, pady=(0, S(8)))
        self.found_list.bind("<Double-Button-1>", self._copy_selected)
        row = tk.Frame(fb, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Copy all", self._copy_all, height=32).pack(side="left")
        Pill(row, "Export...", self._export_found, height=32).pack(side="left", padx=(S(6), 0))
        Pill(fb, "Clear list", self._clear_found, height=32).pack(anchor="w", pady=(S(6), 0))
        self._render_log()

    def _page_search(self, f):
        page_head(f, "Search", "Choose how usernames are generated and how fast they are checked.")
        grid = self._two_cols(f)

        def cell(title, sub, r, c):
            p, b = panel(grid, title, sub)
            p.grid(row=r, column=c, sticky="nsew", padx=(0, S(8)) if c == 0 else (S(8), 0), pady=(0, S(12)))
            return b

        b = cell("Name length", "Discord usernames are between 2 and 32 characters long. This counts any start / end text you add.", 0, 0)
        self._fields_row(b, [("Min length", self.min_var), ("Max length", self.max_var)])
        b = cell("Speed", "Every check waits a random time between the two delays, so requests don't arrive on a "
                          "fixed beat. If Discord rate limits you, the delay stretches by itself and eases back.", 0, 1)
        self._fields_row(b, [("Min delay (seconds)", self.dmin_var), ("Max delay (seconds)", self.dmax_var)])
        b = cell("Characters", "Used for letter combinations. With a dictionary, words must only use these characters.", 1, 0)
        t = tk.Frame(b, bg=BG1)
        t.pack(fill="x")
        t.columnconfigure(0, weight=1, uniform="t")
        t.columnconfigure(1, weight=1, uniform="t")
        Toggle(t, "Letters a-z", self.letters_var).grid(row=0, column=0, sticky="w", pady=4)
        Toggle(t, "Digits 0-9", self.digits_var).grid(row=0, column=1, sticky="w", pady=4)
        Toggle(t, "Underscore _", self.underscore_var).grid(row=1, column=0, sticky="w", pady=4)
        Toggle(t, "Period .", self.period_var).grid(row=1, column=1, sticky="w", pady=4)
        small(b, "Or only use these characters (overrides the toggles)", bg=BG1).pack(anchor="w", pady=(S(10), S(4)))
        Field(b, self.chars_var, width=30).pack(fill="x")
        b = cell("Order", "Shortest first checks every name, short ones before long ones. Random jumps around.", 1, 1)
        Chips(b, [("Shortest first", "shortest"), ("Random", "random")], self.mode_var).pack(anchor="w")
        b = cell("Add to every name", "Text added to the start and/or end of each generated name, e.g. 'xx' + name. "
                                      "It counts towards the 32 character limit.", 2, 0)
        self._fields_row(b, [("Start with", self.prefix_var), ("End with", self.suffix_var)])
        b = cell("Skip names containing", "Separate with commas or spaces. Names with any of these are never checked.", 2, 1)
        Field(b, self.exclude_var, width=30).pack(fill="x")
        b = cell("Stop automatically", "Ends the scan at the first limit reached. 0 means no limit.", 3, 0)
        self._fields_row(b, [("Names found", self.stopafter_var), ("Minutes", self.timelimit_var),
                             ("Names checked", self.limit_var)])
        b = cell("Take breaks", "Pause for a while every so many checks. Long scans look less robotic and "
                                "rate limits are less likely. 0 = no breaks.", 3, 1)
        self._fields_row(b, [("Break every N checks", self.break_every_var), ("Break length (s)", self.break_secs_var)])
        self._make_estimate(f, True).pack(anchor="w", pady=(0, S(12)))
        self._presets_panel(f)

    def _page_words(self, f):
        page_head(f, "Dictionary", "Only check real words, in the language you pick.")
        grid = self._two_cols(f)
        p, b = panel(grid, "Language", "Names will only be made of words from this dictionary. Accents are removed (cafe, not caf\u00e9).")
        p.grid(row=0, column=0, sticky="nsew", padx=(0, S(8)), pady=(0, S(12)))
        self.dict_btn = Pill(b, self._dict_button_text(), self.open_dict_dialog, height=36)
        self.dict_btn.pack(anchor="w")
        Toggle(b, "Skip capitalized words (proper nouns)", self.dict_caps_var).pack(anchor="w", pady=(S(14), 0))
        p, b = panel(grid, "Words per name",
                     f"Join several words into one name, e.g. 2 words gives redapple. A name can never be longer than 32 characters (your Max length). Up to {MAX_WORDS} words.")
        p.grid(row=0, column=1, sticky="nsew", padx=(S(8), 0), pady=(0, S(12)))
        self._fields_row(b, [("From", self.wmin_var), ("To", self.wmax_var)])
        note(b, "Only used while a dictionary is selected.").pack(anchor="w", pady=(S(8), 0))
        p, b = panel(f, "Must contain",
                     "Every name has to include this text. With a dictionary it counts as one of the words, "
                     "so 'apple' with 2 words gives names like appleboat or redapple. Without one, the "
                     "other characters are filled in for you. You can also type it in the bar at the top.")
        p.pack(fill="x", pady=(0, S(12)))
        Field(b, self.must_var, width=30).pack(fill="x", pady=(0, S(10)))
        Chips(b, [("Anywhere", "any"), ("At the start", "start"), ("At the end", "end")], self.place_var).pack(anchor="w")
        self._make_estimate(f, True).pack(anchor="w")

    def _page_check(self, f):
        page_head(f, "Check names", "Paste your own list of names and check just those.")
        p, b = panel(f, "Your names", "One per line, or separated by spaces / commas. Names that can't exist on Discord "
                                      "(wrong characters, too short or too long) are ignored.")
        p.pack(fill="x", pady=(0, S(12)))
        box = tk.Frame(b, bg=BORDER)
        box.pack(fill="x")
        self.check_text = tk.Text(box, height=10, bg=BG2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                                  highlightthickness=0, font=mono(11), padx=S(10), pady=S(8), wrap="word",
                                  selectbackground=T("#265a8f"), selectforeground="#ffffff")
        self.check_text.pack(fill="x", padx=1, pady=1)
        SmoothText(self.check_text)
        self.check_text.bind("<KeyRelease>", lambda e: self._update_check_count())
        self.check_count = small(b, "", bg=BG1)
        self.check_count.pack(anchor="w", pady=(S(8), S(10)))
        row = tk.Frame(b, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Check these names", self._check_listed, kind="primary", icon="play", height=36).pack(side="left")
        Pill(row, "Load from file...", self._load_name_file, height=36).pack(side="left", padx=(S(8), 0))
        Pill(row, "Clear", lambda: (self.check_text.delete("1.0", "end"), self._update_check_count()),
             height=36).pack(side="left", padx=(S(8), 0))
        note(f, "Uses your Account token and your Speed / Breaks settings. Results appear on the Home tab "
                "and are saved like any other scan.", 700).pack(anchor="w")
        self._update_check_count()

    def _page_webhook(self, f):
        page_head(f, "Webhook", "Get a Discord message whenever a name is available.")
        grid = self._two_cols(f)
        p, b = panel(grid, "Notifications", "Paste a webhook URL (Server settings > Integrations > Webhooks).")
        p.grid(row=0, column=0, sticky="nsew", padx=(0, S(8)))
        Toggle(b, "Notify me when a name is available", self.hook_on).pack(anchor="w", pady=(0, S(8)))
        Toggle(b, "Also send a summary when the scan ends", self.hook_done_var).pack(anchor="w", pady=(0, S(10)))
        Field(b, self.hook_var, width=30).pack(fill="x", pady=(0, S(12)))
        row = tk.Frame(b, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Send test", self.test_webhook, height=34).pack(side="left")
        Pill(row, "Edit message...", lambda: self._open_window("msg", "Webhook message", self._build_msg_dialog),
             height=34).pack(side="left", padx=(S(8), 0))
        p, b = panel(grid, "Name & picture", "Changes the webhook itself, so it posts with this name and profile picture.")
        p.grid(row=0, column=1, sticky="nsew", padx=(S(8), 0))
        small(b, "Webhook name").pack(anchor="w", pady=(0, S(4)))
        Field(b, self.hook_name_var, width=30).pack(fill="x", pady=(0, S(10)))
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x", pady=(0, S(10)))
        self.avatar_label = small(row, os.path.basename(self._avatar_path) or "No picture chosen")
        Pill(row, "Choose picture...", self._pick_avatar, height=34).pack(side="left")
        self.avatar_label.pack(side="left", padx=(S(10), 0))
        row = tk.Frame(b, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Apply", self.apply_hook_identity, kind="primary", height=34).pack(side="left")
        Pill(row, "Load current", self.load_hook_identity, height=34).pack(side="left", padx=(S(8), 0))
        Pill(row, "Remove picture", lambda: self.apply_hook_identity(remove_avatar=True),
             height=34).pack(side="left", padx=(S(8), 0))
        self.hook_info_lbl = tk.Label(b, text=self._hook_info[0], bg=BG1, fg=self._hook_color(self._hook_info[1]),
                                      font=font(9), justify="left", wraplength=S(380), anchor="w")
        self.hook_info_lbl.pack(anchor="w", pady=(S(10), 0))

    def _hook_color(self, kind):
        return {"ok": OK, "err": ERR}.get(kind, MUTED)

    def _page_account(self, f):
        page_head(f, "Account", "The Discord account used to check names.")
        p, b = panel(f, "Token", "Stored only on this PC. Tick 'Remember' to keep it between sessions (encrypted with Windows).")
        p.pack(fill="x")
        trow = tk.Frame(b, bg=BG1)
        trow.pack(fill="x", pady=(0, S(12)))
        Pill(trow, "Paste", self._paste_token, height=34).pack(side="right", padx=(S(8), 0))
        self.token_entry = Field(trow, self.token_var, width=44, show="\u2022")
        self.token_entry.pack(side="left", fill="x", expand=True)
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x", pady=(0, S(14)))
        Toggle(row, "Show token", self.show_var, self._toggle_token).pack(side="left")
        if IS_WINDOWS:
            Toggle(row, "Remember token (encrypted)", self.remember_var).pack(side="left", padx=(S(24), 0))
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x")
        Pill(row, "Test token", self.test_token, kind="primary", height=36).pack(side="left")
        self.acct_label = small(row, "Not connected", bg=BG1)
        self.acct_label.pack(side="left", padx=(S(14), 0))
        note(b, "Heads up: automating a user account goes against Discord's Terms of Service and can get it "
                "suspended. Use an account you can afford to lose and keep the delay generous.", 600
             ).pack(anchor="w", pady=(S(18), 0))
        p, b = panel(f, "Saved accounts",
                     "Keep several tokens and switch between them yourself. Pick one and press 'Use selected', "
                     "or press 'Next account' (Ctrl+J, or click your avatar at the top). The switch is always "
                     "manual and takes effect from the next check, even during a scan. Rate limits are per account.")
        p.pack(fill="x", pady=(S(12), 0))
        self._acct_list = tk.Listbox(b, height=7, width=72, bg=BG2, fg=TEXT, bd=0, highlightthickness=0,
                                     font=mono(10), activestyle="none", exportselection=False,
                                     selectbackground=T("#265a8f"), selectforeground="#ffffff")
        self._acct_list.pack(fill="x", pady=(0, S(10)))
        self._acct_list.bind("<Double-Button-1>", lambda e: self.use_selected_account())
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x", pady=(0, S(8)))
        Pill(row, "Add current token", self.add_account, kind="primary", height=34).pack(side="right", padx=(S(8), 0))
        small(row, "Label", bg=BG1).pack(side="left", padx=(0, S(8)))
        Field(row, self.acct_name_var, width=22).pack(side="left", fill="x", expand=True)
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x")
        for text, cmd in (("Use selected", self.use_selected_account), ("Previous", lambda: self.cycle_account(-1)),
                          ("Next account", lambda: self.cycle_account(1)), ("Rename", self.rename_account),
                          ("Remove", self.remove_account), ("Import from file...", self.import_accounts)):
            Pill(row, text, cmd, height=32, size=9).pack(side="left", padx=(0, S(6)))
        note(b, "Saved tokens are kept between sessions only if 'Remember token' is on (Windows, encrypted). "
                "Otherwise they last until you close the app. Press 'Test token' after switching to see who "
                "the account is; accounts that Discord rejects are marked and skipped when cycling.", 700
             ).pack(anchor="w", pady=(S(10), 0))
        self._fill_accounts()

    def _page_look(self, f):
        page_head(f, "Appearance", "Change the colour scheme. The whole app updates when you let go of a slider.")
        p, b = panel(f, "Colour scheme")
        p.pack(fill="x")
        self._draft = dict(THEME)
        d = self._draft
        self._preview = tk.Canvas(b, width=S(360), height=S(48), bg=BG1, highlightthickness=0, bd=0)
        self._preview.pack(anchor="w", pady=(0, S(14)))
        small(b, "Mode", bg=BG1).pack(anchor="w", pady=(0, S(6)))
        modes = tk.Frame(b, bg=BG1)
        modes.pack(anchor="w", pady=(0, S(14)))
        for label, mode in MODE_NAMES:
            Pill(modes, label, lambda mm=mode: self.after_idle(lambda: self.set_theme(dict(THEME, mode=mm))),
                 kind="chip_on" if THEME["mode"] == mode else "chip", height=30, size=9).pack(side="left", padx=(0, S(6)))
        small(b, "Text size", bg=BG1).pack(anchor="w", pady=(0, S(6)))
        sizes = tk.Frame(b, bg=BG1)
        sizes.pack(anchor="w", pady=(0, S(14)))
        for label, adj in (("Small", -1), ("Normal", 0), ("Large", 1), ("Extra large", 2)):
            Pill(sizes, label, lambda a=adj: self.after_idle(lambda: self.set_text_size(a)),
                 kind="chip_on" if TEXT_ADJ == adj else "chip", height=30, size=9).pack(side="left", padx=(0, S(6)))
        small(b, "Quick accent colours", bg=BG1).pack(anchor="w", pady=(0, S(6)))
        quick = tk.Frame(b, bg=BG1)
        quick.pack(anchor="w", pady=(0, S(14)))
        for label, hue in (("Red", 0), ("Orange", 25), ("Yellow", 48), ("Green", 130), ("Teal", 175),
                           ("Blue", 210), ("Purple", 270), ("Pink", 320)):
            Pill(quick, label, lambda hh=hue: self.after_idle(lambda: self.set_theme(dict(THEME, hue=float(hh)))),
                 kind="chip", height=30, size=9).pack(side="left", padx=(0, S(6)))
        h = lambda: d["hue"] / 360.0
        specs = [
            ("Accent hue", d["hue"] / 360.0, lambda: [_hsv_hex(i / 6.0, 0.85, 1.0) for i in range(7)],
             lambda v: {"hue": v * 360.0}, lambda: f"{d['hue']:.0f}\u00b0"),
            ("Saturation", d["sat"] / 2.0, lambda: [_hsv_hex(h(), 0.0, 0.9), _hsv_hex(h(), 1.0, 0.9)],
             lambda v: {"sat": v * 2.0}, lambda: f"{d['sat'] * 100:.0f}%"),
            ("Brightness", (d["val"] - 0.4) / 1.2, lambda: ["#000000", _hsv_hex(h(), min(1.0, d["sat"]), 1.0)],
             lambda v: {"val": 0.4 + v * 1.2}, lambda: f"{d['val'] * 100:.0f}%"),
            ("Background tint (dark modes)", d["lift"] / 0.35, lambda: ["#0f0f0f", _hsv_hex(h(), min(1.0, 0.3 * d["sat"]), 0.35)],
             lambda v: {"lift": v * 0.35}, lambda: f"{d['lift'] / 0.35 * 100:.0f}%"),
        ]
        self._sliders, self._readouts = [], []
        for title, value, stops, to_theme, readout in specs:
            row = tk.Frame(b, bg=BG1)
            row.pack(fill="x", pady=(0, S(2)))
            small(row, title, bg=BG1).pack(side="left")
            lbl = tk.Label(row, text=readout(), bg=BG1, fg=TEXT, font=font(9, "bold"))
            lbl.pack(side="right")
            self._readouts.append((lbl, readout))

            def on_drag(v, to_theme=to_theme):
                d.update(to_theme(v))
                self._refresh_look()

            def on_release(v, to_theme=to_theme):
                d.update(to_theme(v))
                self.after_idle(lambda: self.set_theme(dict(d)))

            sl = GradientSlider(b, value, stops, on_drag, on_release, width=360)
            sl.pack(anchor="w", pady=(0, S(10)))
            self._sliders.append(sl)
        Pill(b, "Reset to default", lambda: self.after_idle(lambda: self.set_theme(dict(DEFAULT_THEME))), height=34).pack(anchor="w", pady=(S(4), 0))
        self._refresh_look()

    def _refresh_look(self):
        for sl in self._sliders:
            sl._draw()
        for lbl, fn in self._readouts:
            lbl.configure(text=fn())
        c, th = self._preview, dict(self._draft)
        c.delete("all")
        names = ("BG0", "BG1", "CHIP", "CHIP_HOVER", "ACCENT", "ACCENT2", "TEXT", "MUTED")
        w, hgt = S(360), S(48)
        for i, k in enumerate(names):
            c.create_rectangle(w * i / len(names), 0, w * (i + 1) / len(names), hgt,
                               fill=T(_BASES[th["mode"]][k], th), outline=T(_BASES[th["mode"]]["BORDER"], th))

    def _page_settings(self, f):
        page_head(f, "Settings", "Saved results, saved settings and credits.")
        grid = self._two_cols(f)
        p, b = panel(grid, "Results", "Available names are appended to this file as they are found.")
        p.grid(row=0, column=0, sticky="nsew", padx=(0, S(8)), pady=(0, S(12)))
        self.results_lbl = note(b, "", 380)
        self.results_lbl.pack(anchor="w", pady=(0, S(10)))
        self._refresh_results_lbl()
        row = tk.Frame(b, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Open", self.open_results, height=34).pack(side="left")
        Pill(row, "Change...", self._change_results, height=34).pack(side="left", padx=(S(6), 0))
        Pill(row, "Default", self._default_results, height=34).pack(side="left", padx=(S(6), 0))
        p, b = panel(grid, "Saved settings", "Everything you set is remembered between sessions. The token is only saved if you ask.")
        p.grid(row=0, column=1, sticky="nsew", padx=(S(8), 0), pady=(0, S(12)))
        Pill(b, "Reset saved settings", self.reset_settings, kind="danger", height=34).pack(anchor="w")
        p, b = panel(grid, "Checked history", "Taken names are remembered, so later scans don't ask Discord about them again.")
        p.grid(row=1, column=0, sticky="nsew", padx=(0, S(8)), pady=(0, S(12)))
        self.history_lbl = small(b, "", bg=BG1)
        self.history_lbl.pack(anchor="w", pady=(0, S(8)))
        Toggle(b, "Skip names I've already checked", self.skipknown_var).pack(anchor="w", pady=(0, S(10)))
        Pill(b, "Forget history", self._forget_history, height=34).pack(anchor="w")
        self._refresh_history()
        p, b = panel(grid, "Sound & files")
        p.grid(row=1, column=1, sticky="nsew", padx=(S(8), 0), pady=(0, S(12)))
        Toggle(b, "Play a sound when a name is available", self.sound_var).pack(anchor="w", pady=(0, S(12)))
        Pill(b, "Open app folder", self.open_folder, height=34).pack(anchor="w")
        p, b = panel(grid, "Window & behaviour")
        p.grid(row=2, column=0, sticky="nsew", padx=(0, S(8)), pady=(0, S(12)))
        Toggle(b, "Keep this window on top", self.ontop_var).pack(anchor="w", pady=(0, S(12)))
        Toggle(b, "Copy each available name to the clipboard", self.autocopy_var).pack(anchor="w")
        p, b = panel(grid, "Log")
        p.grid(row=2, column=1, sticky="nsew", padx=(S(8), 0), pady=(0, S(12)))
        Toggle(b, "Show times in the log", self.logts_var).pack(anchor="w", pady=(0, S(12)))
        Toggle(b, "Log taken names (turn off for a quieter log)", self.logtaken_var).pack(anchor="w", pady=(0, S(12)))
        Pill(b, "Save log...", self._save_log, height=34).pack(anchor="w")
        p, b = panel(grid, "Scrolling", "Smooth scrolling glides to where you scroll instead of jumping. "
                           "Applies to the pages, the live log and the name box on Check names.")
        p.grid(row=3, column=1, sticky="nsew", padx=(S(8), 0), pady=(0, S(12)))
        Toggle(b, "Smooth scrolling", self.smooth_var).pack(anchor="w", pady=(0, S(12)))
        small(b, "Scroll speed", bg=BG1).pack(anchor="w", pady=(0, S(6)))
        Chips(b, [("Slow", "slow"), ("Normal", "normal"), ("Fast", "fast")], self.scrollspeed_var,
              size=9, height=30).pack(anchor="w")
        p, b = panel(grid, "Backup", "Save your settings and presets to a file, or load them on another PC. "
                                     "Your token and webhook URL are never included.")
        p.grid(row=3, column=0, sticky="nsew", padx=(0, S(8)), pady=(0, S(12)))
        row = tk.Frame(b, bg=BG1)
        row.pack(anchor="w")
        Pill(row, "Export settings...", self.export_settings, height=34).pack(side="left")
        Pill(row, "Import settings...", self.import_settings, height=34).pack(side="left", padx=(S(6), 0))
        p, b = panel(f, "Keyboard shortcuts")
        p.pack(fill="x", pady=(0, S(12)))
        for keys, what in (("Ctrl + Enter", "Start"), ("Esc", "Stop"), ("Ctrl + P", "Pause / resume"),
                           ("Ctrl + L", "Jump to the 'must contain' bar"),
                           ("Ctrl + J", "Switch to the next saved account (Ctrl+Shift+J: previous)"),
                           ("Page Up / Down", "Scroll the current page by a screen"), ("Ctrl + B", "Collapse / expand the sidebar"),
                           ("Ctrl + 1 ... 8", "Go to Home, Search, Dictionary, Check names, Webhook, Account, Appearance, Settings")):
            line = tk.Frame(b, bg=BG1)
            line.pack(fill="x", pady=1)
            tk.Label(line, text=keys, bg=BG1, fg=TEXT, font=mono(9), width=16, anchor="w").pack(side="left")
            small(line, what, bg=BG1).pack(side="left")
        p, b = panel(f, "About")
        p.pack(fill="x")
        note(b, CREDIT + "\nOriginal license: non-commercial use only. Original author credit is required "
                         "wherever this code is used.", 640).pack(anchor="w")


    # --------------------------------------------------------- pop-up windows
    def _win_alive(self, key):
        w = self._windows.get(key)
        return bool(w) and w.winfo_exists()

    def _open_window(self, key, title, builder):
        if self._win_alive(key):
            self._windows[key].lift()
            return
        win = tk.Toplevel(self)
        win.title(title)
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=BG0)
        self._windows[key] = win
        self._builders[key] = builder
        builder(win)
        win.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
        y = self.winfo_rooty() + S(30)
        win.geometry(f"+{x}+{y}")
        self._style_titlebar(win)

    def _rebuild_windows(self):
        """After a colour change: redraw any pop-up that is still open in the new colours."""
        for key, builder in self._builders.items():
            if self._win_alive(key):
                self._windows[key].configure(bg=BG0)
                builder(self._windows[key])

    @staticmethod
    def _clear(win):
        for ch in win.winfo_children():
            ch.destroy()
        win.configure(bg=BG0)

    # ----------------------------------------------------- colour scheme
    def _snapshot(self):
        fg = self.acct_label.cget("fg")
        return dict(
            found=list(self.found), start=self.start_btn.enabled, stop=self.stop_btn.enabled,
            prog=(self.progress.target, self.progress.shown), letter=self.avatar.letter,
            plabel=(self.progress_label.cget("text"), self.progress_label.cget("fg") == TEXT),
            acct=(self.acct_label.cget("text"), "ok" if fg == OK else "err" if fg == ERR else "muted"),
        )

    def set_theme(self, theme):
        """Apply a theme to the whole app. Rebuilds the widgets and restores their state."""
        snap = self._snapshot()
        apply_theme(theme)
        if self._toast_job:
            self.after_cancel(self._toast_job)
            self._toast_job = None
        self._root_frame.destroy()
        self.configure(bg=BG0)
        self._style()
        self._build_ui()
        for name in snap["found"]:
            self.found_list.insert("end", name)
        self.start_btn.set_enabled(snap["start"])
        self.stop_btn.set_enabled(snap["stop"])
        self.progress.target, self.progress.shown = snap["prog"]
        self.progress._paint()
        self._refresh_stats()
        text, active = snap["plabel"]
        self.progress_label.configure(text=text, fg=TEXT if active else MUTED)
        text, kind = snap["acct"]
        self.acct_label.configure(text=text, fg={"ok": OK, "err": ERR, "muted": MUTED}[kind])
        self.avatar.set_letter(snap["letter"])
        self._sync_pause_btn()
        self._update_estimate()
        self._toggle_token()
        self._style_titlebar()
        self._rebuild_windows()
        self._patch_settings({"theme": dict(THEME, v=THEME_VERSION), "text_adj": TEXT_ADJ})

    def set_text_size(self, adj):
        global TEXT_ADJ
        TEXT_ADJ = adj
        self.set_theme(dict(THEME))

    # ------------------------------------------------------------ dictionary
    def _dict_button_text(self):
        if not self.dict_spec:
            return "Off  (all combinations)  - choose a language..."
        if self.dict_spec == "file":
            return "Custom list: " + os.path.basename(self.dict_file)[:28]
        return LANG_NAMES.get(self.dict_spec, self.dict_spec) + "  - change..."

    @staticmethod
    def _dict_path(code):
        return os.path.join(DICT_DIR, code + ".dic")

    def _dict_words(self):
        """Return (usable words, None) for the active dictionary, or (None, error message)."""
        spec = self.dict_spec
        path = self.dict_file if spec == "file" else self._dict_path(spec)
        skip = self.dict_caps_var.get() and spec not in KEEP_CAPS
        key = (spec, path, skip)
        if key not in self._dict_cache:
            if not os.path.isfile(path):
                what = "The chosen word list file is missing." if spec == "file" else \
                    f"The {LANG_NAMES.get(spec, spec)} dictionary hasn't been downloaded yet."
                return None, what + " Open the Dictionary tab and pick a language."
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self._dict_cache = {key: parse_wordlist(f.read(), skip)}   # keep only one list in memory
            except OSError as e:
                return None, f"Couldn't read the dictionary: {e}"
        return self._dict_cache[key], None

    def _set_dict(self, spec, file=""):
        self.dict_spec, self.dict_file = spec, file
        if self.dict_btn.winfo_exists():
            self.dict_btn.set_text(self._dict_button_text())
        self._update_estimate()
        self._patch_settings({"dict_lang": spec, "dict_file": file})
        if self._win_alive("dict"):
            self._fill_dict_list()

    def open_dict_dialog(self):
        self._open_window("dict", "Dictionary language", self._build_dict_dialog)

    def _build_dict_dialog(self, win):
        self._clear(win)
        body = tk.Frame(win, bg=BG0)
        body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        tk.Label(body, text="Dictionary language", bg=BG0, fg=TEXT, font=font(14, "bold")).pack(anchor="w")
        note(body, "Only check real words from one language's dictionary instead of every letter combination. "
                   "Word lists are downloaded once and kept in the 'dictionaries' folder. Only Latin-script "
                   "languages are listed, because Discord usernames allow just a-z, 0-9, _ and . "
                   "(accents are removed).", 470).pack(anchor="w", pady=(S(4), 0))
        self._dict_filter = tk.StringVar()
        Field(body, self._dict_filter, width=30).pack(fill="x", pady=(S(12), S(8)))
        self._dict_filter.trace_add("write", lambda *_: self._fill_dict_list())

        wrap = tk.Frame(body, bg=BG1)
        wrap.pack(fill="x")
        self._dict_list = tk.Listbox(wrap, height=14, width=48, bg=BG1, fg=TEXT, bd=0, highlightthickness=0,
                                     font=mono(10), activestyle="none", exportselection=False,
                                     selectbackground=T("#265a8f"), selectforeground="#ffffff")
        sb = ttk.Scrollbar(wrap, orient="vertical", style="Yt.Vertical.TScrollbar", command=self._dict_list.yview)
        self._dict_list.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._dict_list.pack(side="left", fill="both", expand=True, padx=S(8), pady=S(8))
        self._dict_list.bind("<Double-Button-1>", lambda e: self._dict_use_selected())

        self._dict_status = tk.Label(body, text="", bg=BG0, fg=MUTED, font=font(9), justify="left",
                                     wraplength=S(470), anchor="w")
        self._dict_status.pack(anchor="w", pady=(S(8), S(8)), fill="x")
        Toggle(body, "Skip capitalized words (proper nouns)", self.dict_caps_var).pack(anchor="w", pady=(0, S(14)))
        btns = tk.Frame(body, bg=BG0)
        btns.pack(fill="x")
        Pill(btns, "Use selected", self._dict_use_selected, kind="primary", height=34).pack(side="left")
        Pill(btns, "Off", self._dict_choose_off, height=34).pack(side="left", padx=(S(8), 0))
        Pill(btns, "Custom file...", self._dict_custom, height=34).pack(side="left", padx=(S(8), 0))
        Pill(btns, "Close", win.destroy, height=34).pack(side="right")
        self._fill_dict_list()
        self._dict_say(self._dict_button_text() if self.dict_spec else "Dictionary is off.")

    def _dict_say(self, text, color=None):
        if self._win_alive("dict"):
            self._dict_status.configure(text=text, fg=color or MUTED)

    def _fill_dict_list(self):
        q = self._dict_filter.get().strip().lower()
        self._dict_rows = [(n, c) for n, c in LANGUAGES if q in n.lower() or q == c.lower()]
        lb = self._dict_list
        lb.delete(0, "end")
        for i, (name, code) in enumerate(self._dict_rows):
            mark = "\u25b6 " if code == self.dict_spec else "  "
            state = "downloaded" if os.path.isfile(self._dict_path(code)) else ""
            if code in self._dict_downloading:
                state = "downloading..."
            lb.insert("end", f"{mark}{name:<32}{state}")
            if code == self.dict_spec:
                lb.selection_set(i)
                lb.see(i)

    def _dict_choose_off(self):
        self._set_dict("")
        self._dict_say("Dictionary is off: every combination will be checked.")

    def _dict_custom(self):
        path = filedialog.askopenfilename(
            parent=self._windows.get("dict"), title="Choose a word list",
            filetypes=[("Word lists", "*.txt *.dic *.lst *.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                n = len(parse_wordlist(f.read(), False))
        except OSError as e:
            self._dict_say(f"Couldn't read that file: {e}", ERR)
            return
        if not n:
            self._dict_say("No usable words found. Use one word per line (letters a-z, digits, _ and .).", ERR)
            return
        self._set_dict("file", path)
        self._dict_say(f"Custom list loaded: {n:,} usable words.", OK)

    def _dict_use_selected(self):
        sel = self._dict_list.curselection()
        if not sel:
            self._dict_say("Pick a language from the list first.", WARN)
            return
        name, code = self._dict_rows[sel[0]]
        if os.path.isfile(self._dict_path(code)):
            self._set_dict(code)
            words, err = self._dict_words()
            self._dict_say(err or f"{name}: {len(words):,} usable words.", ERR if err else OK)
            return
        if code in self._dict_downloading:
            return
        self._dict_downloading.add(code)
        self._dict_say(f"Downloading {name}...", ACCENT2)
        self._fill_dict_list()

        def job():
            err = None
            try:
                r = requests.get(DICT_URL.format(code=code), timeout=60)
                if r.status_code != 200:
                    err = f"server answered HTTP {r.status_code}"
                else:
                    os.makedirs(DICT_DIR, exist_ok=True)
                    tmp = self._dict_path(code) + ".part"
                    with open(tmp, "wb") as f:
                        f.write(r.content)
                    os.replace(tmp, self._dict_path(code))
            except Exception as e:
                err = str(e)
            self.q.put(("dict_done", code, err))

        threading.Thread(target=job, daemon=True).start()

    def _dict_finished(self, code, err):
        self._dict_downloading.discard(code)
        name = LANG_NAMES.get(code, code)
        if err:
            self._dict_say(f"Couldn't download {name}: {err}", ERR)
        else:
            self._set_dict(code)
            words, e2 = self._dict_words()
            self._dict_say(e2 or f"{name} ready: {len(words):,} usable words.", ERR if e2 else OK)
        if self._win_alive("dict"):
            self._fill_dict_list()

    # -------------------------------------------------------------- presets
    PRESET_KEYS = ("min", "max", "dmin", "dmax", "break_every", "break_secs", "time_limit", "prefix",
                   "suffix", "custom_chars", "limit", "letters", "digits", "underscore", "period", "mode",
                   "wmin", "wmax", "must", "place", "exclude", "stop_after", "dict_lang", "dict_file",
                   "dict_skip_caps")

    def _preset_data(self):
        v = {"min": self.min_var, "max": self.max_var, "dmin": self.dmin_var, "dmax": self.dmax_var,
             "break_every": self.break_every_var, "break_secs": self.break_secs_var,
             "time_limit": self.timelimit_var, "prefix": self.prefix_var, "suffix": self.suffix_var,
             "custom_chars": self.chars_var, "limit": self.limit_var,
             "letters": self.letters_var, "digits": self.digits_var, "underscore": self.underscore_var,
             "period": self.period_var, "mode": self.mode_var, "wmin": self.wmin_var, "wmax": self.wmax_var,
             "must": self.must_var, "place": self.place_var, "exclude": self.exclude_var,
             "stop_after": self.stopafter_var, "dict_skip_caps": self.dict_caps_var}
        data = {k: var.get() for k, var in v.items()}
        data["dict_lang"], data["dict_file"] = self.dict_spec, self.dict_file
        return data

    def _presets_panel(self, f):
        p, b = panel(f, "Presets", "Save your favourite search setups and bring them back with one click.")
        p.pack(fill="x", pady=(0, S(12)))
        row = tk.Frame(b, bg=BG1)
        row.pack(fill="x")
        self._preset_list = tk.Listbox(row, height=4, width=34, bg=BG2, fg=TEXT, bd=0, highlightthickness=0,
                                       font=font(10), activestyle="none", exportselection=False,
                                       selectbackground=T("#265a8f"), selectforeground="#ffffff")
        self._preset_list.pack(side="left")
        self._preset_list.bind("<Double-Button-1>", lambda e: self._load_preset())
        btns = tk.Frame(row, bg=BG1)
        btns.pack(side="left", padx=(S(14), 0), anchor="n")
        Pill(btns, "Load", self._load_preset, kind="primary", height=32).pack(anchor="w")
        Pill(btns, "Save current as...", lambda: self._open_window("preset", "Save preset", self._build_preset_dialog),
             height=32).pack(anchor="w", pady=(S(6), 0))
        Pill(btns, "Delete", self._delete_preset, height=32).pack(anchor="w", pady=(S(6), 0))
        self._fill_presets()

    def _fill_presets(self):
        lb = getattr(self, "_preset_list", None)
        if lb is None or not lb.winfo_exists():
            return
        lb.delete(0, "end")
        for name in sorted(self.presets, key=str.lower):
            lb.insert("end", name)

    def _selected_preset(self):
        lb = getattr(self, "_preset_list", None)
        sel = lb.curselection() if lb is not None and lb.winfo_exists() else ()
        if not sel:
            self.toast("Pick a preset from the list first")
            return None
        return lb.get(sel[0])

    def _load_preset(self):
        name = self._selected_preset()
        if name is None or name not in self.presets:
            return
        self._apply_settings({k: v for k, v in self.presets[name].items() if k in self.PRESET_KEYS})
        self._set_dict(self.dict_spec, self.dict_file)
        self._update_estimate()
        self.toast(f"Loaded preset '{name}'")

    def _delete_preset(self):
        name = self._selected_preset()
        if name is None:
            return
        self.presets.pop(name, None)
        self._patch_settings({"presets": self.presets})
        self._fill_presets()
        self.toast(f"Deleted preset '{name}'")

    def _save_preset(self, name):
        name = name.strip()[:40]
        if not name:
            self.toast("Give the preset a name")
            return False
        if name not in self.presets and len(self.presets) >= MAX_PRESETS:
            messagebox.showwarning("Presets", f"You can keep up to {MAX_PRESETS} presets. Delete one first.")
            return False
        self.presets[name] = self._preset_data()
        self._patch_settings({"presets": self.presets})
        self._fill_presets()
        self.toast(f"Saved preset '{name}'")
        return True

    def _build_preset_dialog(self, win):
        self._clear(win)
        body = tk.Frame(win, bg=BG0)
        body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        tk.Label(body, text="Save preset", bg=BG0, fg=TEXT, font=font(14, "bold")).pack(anchor="w")
        note(body, "Saves the current Search and Dictionary settings (not your token or webhook). "
                   "Using an existing name replaces that preset.", 380).pack(anchor="w", pady=(S(4), S(12)))
        fld = Field(body, self.preset_name_var, width=34)
        fld.pack(fill="x")

        def done():
            if self._save_preset(self.preset_name_var.get()):
                self.preset_name_var.set("")
                win.destroy()

        fld.entry.bind("<Return>", lambda e: done())
        btns = tk.Frame(body, bg=BG0)
        btns.pack(fill="x", pady=(S(16), 0))
        Pill(btns, "Save", done, kind="primary", height=34).pack(side="left")
        Pill(btns, "Cancel", win.destroy, height=34).pack(side="right")
        fld.entry.focus_set()

    # ------------------------------------------------------ history / pause
    @staticmethod
    def _history_count():
        try:
            with open(CHECKED_FILE, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def _refresh_history(self):
        lbl = getattr(self, "history_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=f"{self._history_count():,} taken names remembered")

    def _forget_history(self):
        if not messagebox.askyesno("Forget history", "Forget every name that was found taken?\n"
                                                     "They will be checked again on the next scan."):
            return
        try:
            os.remove(CHECKED_FILE)
        except OSError:
            pass
        self._refresh_history()
        self.toast("History cleared")

    def toggle_pause(self):
        if not self._running():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.start_time += time.time() - self._pause_at   # paused time doesn't count towards speed/ETA
            self.set_status("Scanning", "run")
        else:
            self._pause_at = time.time()
            self.pause_event.set()
            self.set_status("Paused", "warn")
        self._sync_pause_btn()
        self._set_title()

    def _sync_pause_btn(self, running=None):
        running = self._running() if running is None else running
        paused = running and self.pause_event.is_set()
        self.pause_btn.set_label("Resume" if paused else "Pause", "play" if paused else "pause")
        self.pause_btn.set_enabled(running)

    def _notify_found(self):
        if not self.sound_var.get():
            return
        if not IS_WINDOWS:
            self.bell()
            return

        def beep():
            try:
                import winsound
                winsound.Beep(988, 110)
                winsound.Beep(1319, 190)
            except Exception:
                pass

        threading.Thread(target=beep, daemon=True).start()

    # ----------------------------------------------- webhook message window
    def _hook_cfg(self):
        try:
            color = int(self.hook_color_var.get().lstrip("#")[:6], 16)
        except ValueError:
            color = 0xFF0000
        return dict(content=self.hook_content_var.get(), embed=self.hook_embed_var.get(),
                    title=self.hook_title_var.get(), desc=self.hook_desc_var.get(),
                    footer=self.hook_footer_var.get(), timestamp=self.hook_ts_var.get(), color=color)

    def _build_msg_dialog(self, win):
        self._clear(win)
        body = tk.Frame(win, bg=BG0)
        body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        tk.Label(body, text="Webhook message", bg=BG0, fg=TEXT, font=font(14, "bold")).pack(anchor="w")
        note(body, "What the webhook says when a username is available. You can use {username}, {length}, "
                   "{time}, {date}, {count} (available so far) and {checked}. Type \\n for a new line.",
             520).pack(anchor="w", pady=(S(4), 0))

        def row(label, var):
            small(body, label).pack(anchor="w", pady=(S(10), S(3)))
            fld = Field(body, var, width=64)
            fld.pack(fill="x")
            fld.entry.bind("<KeyRelease>", lambda e: self._refresh_msg())

        small(body, "Start from a template").pack(anchor="w", pady=(S(12), S(4)))
        trow = tk.Frame(body, bg=BG0)
        trow.pack(anchor="w")
        for label, tpl in HOOK_TEMPLATES:
            Pill(trow, label, lambda t=tpl: self._apply_hook_template(t), kind="chip", height=30,
                 size=9).pack(side="left", padx=(0, S(6)))
        row("Message text (shown above the card, optional)", self.hook_content_var)
        Toggle(body, "Send as an embed (coloured card)", self.hook_embed_var,
               self._refresh_msg).pack(anchor="w", pady=(S(14), 0))
        row("Embed title", self.hook_title_var)
        row("Embed description", self.hook_desc_var)
        row("Embed footer", self.hook_footer_var)
        Toggle(body, "Show a timestamp", self.hook_ts_var, self._refresh_msg).pack(anchor="w", pady=(S(12), 0))

        def rgb():
            try:
                v = int(self.hook_color_var.get().lstrip("#")[:6], 16)
            except ValueError:
                v = 0xFF0000
            return [(v >> 16) & 255, (v >> 8) & 255, v & 255]

        def set_chan(i, val):
            c = rgb()
            c[i] = int(round(val * 255))
            self.hook_color_var.set("#%02x%02x%02x" % tuple(c))
            self._refresh_msg()

        small(body, "Card colour").pack(anchor="w", pady=(S(12), S(3)))
        self._msg_sliders = []
        for i, name in enumerate(("Red", "Green", "Blue")):
            def stops(i=i):
                a, b = rgb(), rgb()
                a[i], b[i] = 0, 255
                return ["#%02x%02x%02x" % tuple(a), "#%02x%02x%02x" % tuple(b)]
            line = tk.Frame(body, bg=BG0)
            line.pack(anchor="w")
            small(line, name).pack(side="left", padx=(0, S(10)))
            sl = GradientSlider(line, rgb()[i] / 255.0, stops, lambda v, i=i: set_chan(i, v),
                                lambda v, i=i: set_chan(i, v), width=300, height=22)
            sl.pack(side="left")
            self._msg_sliders.append(sl)

        small(body, "Preview").pack(anchor="w", pady=(S(12), S(4)))
        self._msg_prev = tk.Frame(body, bg=T("#313338"))
        self._msg_prev.pack(fill="x")
        btns = tk.Frame(body, bg=BG0)
        btns.pack(fill="x", pady=(S(16), 0))
        Pill(btns, "Send test", self.test_webhook, kind="primary", height=34).pack(side="left")
        Pill(btns, "Reset to default", self._msg_reset, height=34).pack(side="left", padx=(S(8), 0))
        Pill(btns, "Close", win.destroy, height=34).pack(side="right")
        self._refresh_msg()

    def _apply_hook_template(self, d):
        d = dict(DEFAULT_HOOK, **d)
        self.hook_content_var.set(d["content"])
        self.hook_embed_var.set(d["embed"])
        self.hook_title_var.set(d["title"])
        self.hook_desc_var.set(d["desc"])
        self.hook_footer_var.set(d["footer"])
        self.hook_color_var.set(d["color"])
        self.hook_ts_var.set(d["timestamp"])
        self.after_idle(lambda: self._build_msg_dialog(self._windows["msg"]) if self._win_alive("msg") else None)

    def _msg_reset(self):
        d = DEFAULT_HOOK
        self.hook_content_var.set(d["content"])
        self.hook_embed_var.set(d["embed"])
        self.hook_title_var.set(d["title"])
        self.hook_desc_var.set(d["desc"])
        self.hook_footer_var.set(d["footer"])
        self.hook_color_var.set(d["color"])
        self.hook_ts_var.set(d["timestamp"])
        self.after_idle(lambda: self._build_msg_dialog(self._windows["msg"]) if self._win_alive("msg") else None)

    def _refresh_msg(self):
        if not self._win_alive("msg"):
            return
        for sl in self._msg_sliders:
            sl._draw()
        box = self._msg_prev
        for ch in box.winfo_children():
            ch.destroy()
        dbg = T("#313338")
        payload = build_webhook_payload(self._hook_cfg(), hook_ctx("example", self.stats))
        tk.Label(box, text=(self.hook_name_var.get().strip() or "Webhook"), bg=dbg, fg=TEXT,
                 font=font(10, "bold")).pack(anchor="w", padx=S(12), pady=(S(10), 0))
        if payload.get("content"):
            tk.Label(box, text=payload["content"], bg=dbg, fg=TEXT, font=font(10), justify="left",
                     wraplength=S(480), anchor="w").pack(anchor="w", padx=S(12), pady=(S(2), 0))
        for e in payload.get("embeds", []):
            card = tk.Frame(box, bg=T("#2b2d31"))
            card.pack(fill="x", padx=S(12), pady=(S(6), 0))
            tk.Frame(card, bg="#%06x" % (e.get("color", 0) & 0xFFFFFF), width=S(4)).pack(side="left", fill="y")
            inner = tk.Frame(card, bg=T("#2b2d31"))
            inner.pack(side="left", fill="x", padx=S(10), pady=S(8))
            if e.get("title"):
                tk.Label(inner, text=e["title"], bg=T("#2b2d31"), fg=TEXT, font=font(10, "bold"),
                         justify="left", wraplength=S(440), anchor="w").pack(anchor="w")
            if e.get("description"):
                tk.Label(inner, text=e["description"], bg=T("#2b2d31"), fg=TEXT, font=font(10),
                         justify="left", wraplength=S(440), anchor="w").pack(anchor="w", pady=(S(2), 0))
            foot = (e.get("footer") or {}).get("text", "")
            if e.get("timestamp"):
                foot = (foot + "  \u2022  " if foot else "") + time.strftime("%H:%M")
            if foot:
                tk.Label(inner, text=foot, bg=T("#2b2d31"), fg=MUTED, font=font(8), anchor="w").pack(anchor="w", pady=(S(6), 0))
        tk.Frame(box, bg=dbg, height=S(10)).pack()


    # ------------------------------------------------------------ settings
    @staticmethod
    def _load_settings():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _apply_settings(self, d):
        global TEXT_ADJ
        if "delay" in d and "dmin" not in d:      # older versions had one fixed delay
            d = dict(d, dmin=d["delay"], dmax=d["delay"])
        if d.get("text_adj") in (-1, 0, 1, 2, 3):
            TEXT_ADJ = d["text_adj"]
        if isinstance(d.get("results_path"), str):
            self.results_path = d["results_path"]
        for key, var in (("min", self.min_var), ("max", self.max_var), ("dmin", self.dmin_var),
                         ("dmax", self.dmax_var), ("break_every", self.break_every_var),
                         ("break_secs", self.break_secs_var), ("time_limit", self.timelimit_var),
                         ("prefix", self.prefix_var), ("suffix", self.suffix_var),
                         ("custom_chars", self.chars_var),
                         ("limit", self.limit_var), ("wmin", self.wmin_var), ("wmax", self.wmax_var),
                         ("must", self.must_var), ("hook_name", self.hook_name_var),
                         ("hook_content", self.hook_content_var), ("hook_title", self.hook_title_var),
                         ("hook_desc", self.hook_desc_var), ("hook_footer", self.hook_footer_var),
                         ("hook_color", self.hook_color_var), ("exclude", self.exclude_var),
                         ("stop_after", self.stopafter_var)):
            if key in d:
                var.set(str(d[key]))
        for key, var in (("letters", self.letters_var), ("digits", self.digits_var),
                         ("underscore", self.underscore_var), ("period", self.period_var),
                         ("hook_on", self.hook_on), ("hook_embed", self.hook_embed_var),
                         ("hook_ts", self.hook_ts_var), ("dict_skip_caps", self.dict_caps_var),
                         ("skip_known", self.skipknown_var), ("sound", self.sound_var),
                         ("log_ts", self.logts_var), ("log_taken", self.logtaken_var),
                         ("on_top", self.ontop_var), ("auto_copy", self.autocopy_var),
                         ("smooth_scroll", self.smooth_var),
                         ("hook_done", self.hook_done_var)):
            if key in d:
                var.set(bool(d[key]))
        if d.get("scroll_speed") in SCROLL_SPEEDS:
            self.scrollspeed_var.set(d["scroll_speed"])
        if d.get("mode") in ("shortest", "random"):
            self.mode_var.set(d["mode"])
        if d.get("place") in ("any", "start", "end"):
            self.place_var.set(d["place"])
        if "sidebar" in d:
            self.side_expanded = bool(d["sidebar"])
        if d.get("tab") in [e[0] for e in self.TABS if e]:
            self.tab = d["tab"]
        if isinstance(d.get("presets"), dict):
            self.presets = {str(k)[:40]: v for k, v in list(d["presets"].items())[:MAX_PRESETS] if isinstance(v, dict)}
        spec = d.get("dict_lang", "")
        if spec == "":
            self.dict_spec, self.dict_file = "", ""
        elif spec in LANG_NAMES:
            self.dict_spec = spec
        elif spec == "file" and isinstance(d.get("dict_file"), str) and os.path.isfile(d["dict_file"]):
            self.dict_spec, self.dict_file = "file", d["dict_file"]
        if d.get("hook_enc"):
            try:
                self.hook_var.set(unprotect(d["hook_enc"]))
            except Exception:
                pass
        if IS_WINDOWS and d.get("remember_token") and d.get("token_enc"):
            try:
                self.token_var.set(unprotect(d["token_enc"]))
                self.remember_var.set(True)
            except Exception:
                pass  # saved on another PC/user, or damaged: ignore
        if IS_WINDOWS and d.get("remember_token") and isinstance(d.get("accounts"), list):
            self.accounts = []
            for a in d["accounts"][:MAX_ACCOUNTS]:
                try:
                    tok = unprotect(a["enc"])
                except Exception:
                    continue
                if tok:
                    self.accounts.append({"label": str(a.get("label") or f"Account {len(self.accounts) + 1}")[:30],
                                          "token": tok, "name": str(a.get("name") or "")[:40],
                                          "bad": bool(a.get("bad"))})

    def _save_settings(self):
        data = {
            "min": self.min_var.get(), "max": self.max_var.get(),
            "dmin": self.dmin_var.get(), "dmax": self.dmax_var.get(), "limit": self.limit_var.get(),
            "break_every": self.break_every_var.get(), "break_secs": self.break_secs_var.get(),
            "time_limit": self.timelimit_var.get(), "prefix": self.prefix_var.get(),
            "suffix": self.suffix_var.get(), "custom_chars": self.chars_var.get(),
            "log_ts": self.logts_var.get(), "log_taken": self.logtaken_var.get(),
            "on_top": self.ontop_var.get(), "auto_copy": self.autocopy_var.get(),
            "smooth_scroll": self.smooth_var.get(), "scroll_speed": self.scrollspeed_var.get(),
            "results_path": self.results_path, "text_adj": TEXT_ADJ,
            "letters": self.letters_var.get(), "digits": self.digits_var.get(),
            "underscore": self.underscore_var.get(), "period": self.period_var.get(),
            "mode": self.mode_var.get(), "hook_on": self.hook_on.get(),
            "remember_token": bool(IS_WINDOWS and self.remember_var.get()),
            "theme": dict(THEME, v=THEME_VERSION), "sidebar": self.side_expanded,
            "dict_lang": self.dict_spec, "dict_file": self.dict_file,
            "dict_skip_caps": self.dict_caps_var.get(),
            "wmin": self.wmin_var.get(), "wmax": self.wmax_var.get(),
            "must": self.must_var.get(), "place": self.place_var.get(),
            "hook_name": self.hook_name_var.get(), "hook_content": self.hook_content_var.get(),
            "hook_embed": self.hook_embed_var.get(), "hook_title": self.hook_title_var.get(),
            "hook_desc": self.hook_desc_var.get(), "hook_footer": self.hook_footer_var.get(),
            "hook_color": self.hook_color_var.get(), "hook_ts": self.hook_ts_var.get(),
            "exclude": self.exclude_var.get(), "stop_after": self.stopafter_var.get(),
            "skip_known": self.skipknown_var.get(), "sound": self.sound_var.get(),
            "hook_done": self.hook_done_var.get(), "presets": self.presets, "tab": self.tab,
        }
        try:
            data["geometry"] = self.geometry()
        except tk.TclError:
            pass
        try:
            hook = self.hook_var.get().strip()
            if hook:
                data["hook_enc"] = protect(hook)
            token = self.token_var.get().strip()
            if data["remember_token"] and token:
                data["token_enc"] = protect(token)
            if data["remember_token"] and self.accounts:
                data["accounts"] = [{"label": a["label"], "enc": protect(a["token"]), "name": a["name"],
                                     "bad": a["bad"]} for a in self.accounts]
        except Exception:
            pass
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def _patch_settings(self, changes):
        """Update a few keys in the saved settings without touching the rest."""
        try:
            d = self._load_settings()
            d.update(changes)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
        except OSError:
            pass

    def reset_settings(self):
        if not messagebox.askyesno("Reset saved settings",
                                   "Delete the saved settings and reset all fields?"):
            return
        try:
            os.remove(SETTINGS_FILE)
        except OSError:
            pass
        for var, val in ((self.token_var, ""), (self.hook_var, ""), (self.min_var, "2"), (self.max_var, "3"),
                         (self.dmin_var, "2"), (self.dmax_var, "4"), (self.break_every_var, "0"),
                         (self.break_secs_var, "30"), (self.timelimit_var, "0"), (self.prefix_var, ""),
                         (self.suffix_var, ""), (self.chars_var, ""), (self.logts_var, True),
                         (self.logtaken_var, True), (self.ontop_var, False), (self.autocopy_var, False),
                         (self.smooth_var, True), (self.scrollspeed_var, "normal"),
                         (self.limit_var, "0"), (self.mode_var, "shortest"),
                         (self.wmin_var, "1"), (self.wmax_var, "1"), (self.must_var, ""),
                         (self.place_var, "any"), (self.hook_name_var, ""),
                         (self.hook_content_var, DEFAULT_HOOK["content"]), (self.hook_title_var, DEFAULT_HOOK["title"]),
                         (self.hook_desc_var, DEFAULT_HOOK["desc"]), (self.hook_footer_var, DEFAULT_HOOK["footer"]),
                         (self.hook_color_var, DEFAULT_HOOK["color"]),
                         (self.remember_var, False), (self.hook_on, False), (self.letters_var, True),
                         (self.digits_var, True), (self.underscore_var, False), (self.period_var, False),
                         (self.dict_caps_var, True), (self.hook_embed_var, True), (self.hook_ts_var, True),
                         (self.exclude_var, ""), (self.stopafter_var, "0"), (self.skipknown_var, True),
                         (self.sound_var, True), (self.hook_done_var, False)):
            var.set(val)
        self.presets = {}
        self.results_path = ""
        self.accounts = []
        self._tok = ""
        self._fill_accounts()
        self._fill_presets()
        self._refresh_results_lbl()
        self._avatar_path = ""
        self._hook_info = ("", "muted")
        self.side_expanded = True
        self._set_dict("")
        self.after_idle(lambda: self.set_theme(dict(DEFAULT_THEME)))
        self.toast("Saved settings cleared")

    # ------------------------------------------------------------- helpers
    def _toggle_token(self):
        self.token_entry.entry.configure(show="" if self.show_var.get() else "\u2022")

    def _charset(self):
        custom = "".join(dict.fromkeys(ch for ch in self.chars_var.get().lower()
                                       if ch in string.ascii_lowercase + string.digits + "_."))
        if custom:                      # "only use these characters" overrides the toggles
            return custom
        chars = ""
        if self.letters_var.get():
            chars += string.ascii_lowercase
        if self.digits_var.get():
            chars += string.digits
        if self.underscore_var.get():
            chars += "_"
        if self.period_var.get():
            chars += "."
        return chars

    def _pool(self, hi, chars, must, exclude=()):
        """Dictionary words that fit the length/character settings (cached between keystrokes)."""
        pool, err = self._dict_words()
        if err:
            return None, err
        key = (id(pool), hi, chars, must, tuple(exclude))
        if self._pool_cache[0] != key:
            allowed = set(chars)
            self._pool_cache = (key, [w for w in pool if len(w) <= hi and w != must and allowed.issuperset(w)
                                      and not any(x in w for x in exclude)])
        return self._pool_cache[1], None

    def _read_pacing(self, fail):
        """Speed / break / stop settings shared by normal scans and name-list checks."""
        try:
            dmin, dmax = float(self.dmin_var.get()), float(self.dmax_var.get())
            break_every = int(self.break_every_var.get() or 0)
            break_secs = float(self.break_secs_var.get() or 0)
            time_limit = float(self.timelimit_var.get() or 0)
            stop_after = int(self.stopafter_var.get() or 0)
        except ValueError:
            return fail("Invalid input", "Delays, breaks and stop limits must be numbers.")
        if not (MIN_INTERVAL <= dmin <= dmax <= MAX_DELAY):
            return fail("Invalid delay", f"Delay must satisfy {MIN_INTERVAL:g} <= min <= max <= {MAX_DELAY:g} seconds.")
        if min(break_every, break_secs, time_limit, stop_after) < 0 or break_secs > 3600:
            return fail("Invalid input", "Break, time limit and 'stop after' can't be negative "
                                         "(a break can last at most an hour).")
        return {"dmin": dmin, "dmax": dmax, "delay": (dmin + dmax) / 2.0, "break_every": break_every,
                "break_secs": break_secs, "time_limit": time_limit, "stop_after": stop_after}

    def _read_settings(self, quiet=False):
        """Validate the UI and return a generation plan (see name_stream), or None."""
        def fail(title, msg):
            if not quiet:
                messagebox.showerror(title, msg)
            return None

        pace = self._read_pacing(fail)
        if pace is None:
            return None
        try:
            lo, hi = int(self.min_var.get()), int(self.max_var.get())
            limit = int(self.limit_var.get() or 0)
        except ValueError:
            return fail("Invalid input", "Lengths and max names must be numbers.")
        if lo < 2 or hi > 32 or lo > hi:
            return fail("Invalid length", "Lengths must satisfy 2 <= min <= max <= 32 (Discord's limit).")
        if limit < 0:
            return fail("Invalid input", "Max names can't be negative.")
        chars = self._charset()
        if not chars:
            return fail("No characters", "Turn on at least one character toggle (Search tab).")
        pre = suf = ""
        for label, raw in (("Start with", self.prefix_var.get()), ("End with", self.suffix_var.get())):
            raw = raw.strip()
            val = fold_text(raw) if raw else ""
            if raw and not val:
                return fail(label, "That text can only use letters a-z (accents are removed), digits, _ and . "
                                   "- and no two periods in a row.")
            if label == "Start with":
                pre = val or ""
            else:
                suf = val or ""
        affix = len(pre) + len(suf)
        tot_lo, tot_hi = lo, hi
        lo, hi = max(1, lo - affix), hi - affix      # length of the part that gets generated
        if hi < 1:
            return fail("Start / end text", f"'{pre}' + '{suf}' already use {affix} characters, which leaves no "
                                            f"room under your max length ({tot_hi}).")
        must = ""
        raw = self.must_var.get().strip()
        if raw:
            must = fold_text(raw)
            if not must:
                return fail("Must contain", "That text can only use letters a-z (accents are removed), "
                                            "digits, _ and . - and no two periods in a row.")
            if len(must) > hi:
                return fail("Must contain", f"'{must}' has {len(must)} characters, which is longer than the "
                                            f"{hi} left under your max length ({tot_hi}).")
        exclude = []
        for part in re.split(r"[,\s]+", self.exclude_var.get().strip()):
            x = fold_text(part) if part else None
            if x and x not in exclude:
                exclude.append(x)
        if any(x in must or x in pre or x in suf for x in exclude):
            return fail("Skip names containing", "Your 'must contain' / start / end text includes something "
                                                 "you asked to skip.")
        words, wmin, wmax = None, 1, 1
        if self.dict_spec:
            try:
                wmin, wmax = int(self.wmin_var.get()), int(self.wmax_var.get())
            except ValueError:
                return fail("Words per name", "Words per name must be whole numbers.")
            if not (1 <= wmin <= wmax <= MAX_WORDS):
                return fail("Words per name", f"Words per name must satisfy 1 <= from <= to <= {MAX_WORDS}.")
            words, err = self._pool(hi, chars, must, exclude)
            if err:
                return fail("Dictionary", err)
        plan = {"lo": lo, "hi": hi, "limit": limit, "chars": chars, "words": words,
                "prefix": pre, "suffix": suf, "tot_lo": tot_lo, "tot_hi": tot_hi, **pace,
                "wmin": wmin, "wmax": wmax, "must": must, "place": self.place_var.get(),
                "sequential": self.mode_var.get() == "shortest", "exclude": exclude,
                "skip_known": self.skipknown_var.get(), "hook_done": self.hook_done_var.get(),
                "results_file": self._results_file()}
        if count_names(plan) == 0:
            return fail("Nothing to search", "These settings can't produce any names. Check the lengths, "
                                             "words per name, dictionary and 'must contain' text.")
        return plan

    def _update_estimate(self):
        s = self._read_settings(quiet=True)
        examples = warn = ""
        if not s:
            text = "Fix the settings to see an estimate."
        else:
            total = count_names(s)
            if s["words"] is not None:
                kind = f"{LANG_NAMES.get(self.dict_spec, 'custom list')} names"
                if s["wmax"] > 1:
                    kind += f" ({s['wmin']}-{s['wmax']} words)" if s["wmin"] != s["wmax"] else f" ({s['wmin']} words)"
            else:
                kind = "names"
            if s["must"]:
                kind += f" containing '{s['must']}'"
            bound = "up to " if s["exclude"] else "~"
            text = f"{bound}{total:,} {kind}  |  about {fmt_duration(total * s['delay'])} at {s['dmin']:g}-{s['dmax']:g}s delays (more if rate limited)"
            if s["dmin"] < 1.5:
                warn = "\nHeads up: a minimum delay under 1.5 seconds makes rate limits more likely."
            ex = sample_names(s)
            if ex:
                examples = "\nExamples: " + ", ".join(ex) + (" ..." if len(ex) >= 6 else "")
        for entry in list(self._est_labels):
            lbl, with_examples = entry
            try:
                lbl.configure(text=text + ((warn + examples) if with_examples else ""))
            except tk.TclError:
                self._est_labels.remove(entry)

    def _refresh_stats(self):
        for key, tile in self.tiles.items():
            tile.set(self.stats[key])
        c, t = self.stats["checked"], self.total
        if t:
            frac = c / t
            self.progress.set(frac)
            text = f"{frac * 100:.1f}%   |   {c:,} / {t:,}"
            el = time.time() - self.start_time
            if self.worker and self.worker.is_alive() and c >= 3 and el > 0:
                text += f"   |   {c / el * 60:.0f}/min"
                if self._pace:
                    text += f"   |   {self._pace:.2f}s per check"
                text += f"   |   ETA {fmt_duration((t - c) / (c / el))}"
            self.progress_label.configure(text=text, fg=TEXT)
            self._set_title()

    def _log(self, text, tag="info"):
        stamp = time.strftime("%H:%M:%S")
        self._log_hist.append((stamp, text, tag))
        allowed = self.LOG_FILTERS.get(self.log_filter.get())
        if allowed is not None and tag not in allowed:
            return
        self.log.configure(state="normal")
        if self.logts_var.get():
            self.log.insert("end", stamp + "  ", "ts")
        self.log.insert("end", text + "\n", tag)
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _render_log(self):
        allowed = self.LOG_FILTERS.get(self.log_filter.get())
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for stamp, text, tag in self._log_hist:
            if allowed is None or tag in allowed:
                if self.logts_var.get():
                    self.log.insert("end", stamp + "  ", "ts")
                self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self._log_hist.clear()
        self._render_log()

    def _save_log(self):
        if not self._log_hist:
            self.toast("The log is empty")
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save log", defaultextension=".txt",
                                            initialfile="dsv_log.txt", filetypes=[("Text file", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(f"{st}  {tx}" for st, tx, _ in self._log_hist) + "\n")
            self.toast("Log saved")
        except OSError as e:
            messagebox.showerror("Couldn't save the log", str(e))

    def _apply_topmost(self):
        try:
            self.attributes("-topmost", bool(self.ontop_var.get()))
        except tk.TclError:
            pass

    def _set_title(self):
        if self._running():
            pct = f"{self.stats['checked'] / self.total * 100:.0f}%" if self.total else ""
            tag = "Paused " if self.pause_event.is_set() else ""
            self.title(f"{tag}{pct} - {APP_TITLE}".strip())
        else:
            self.title(APP_TITLE)

    # ---- results file location
    def _results_file(self):
        return self.results_path or RESULTS_FILE

    def _refresh_results_lbl(self):
        lbl = getattr(self, "results_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=self._results_file())

    def _change_results(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Where should available names be saved?", defaultextension=".txt",
            initialfile=os.path.basename(self._results_file()), confirmoverwrite=False,
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")])
        if path:
            self.results_path = path
            self._refresh_results_lbl()
            self._patch_settings({"results_path": path})
            self.toast("Results location changed")

    def _default_results(self):
        self.results_path = ""
        self._refresh_results_lbl()
        self._patch_settings({"results_path": ""})
        self.toast("Using the default results file")

    # ---- backup of settings
    SECRET_KEYS = ("token_enc", "hook_enc", "remember_token", "geometry", "tab", "results_path", "accounts")

    def export_settings(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export settings", defaultextension=".json",
                                            initialfile="dsv_settings_backup.json",
                                            filetypes=[("Settings file", "*.json")])
        if not path:
            return
        self._save_settings()
        data = {k: v for k, v in self._load_settings().items() if k not in self.SECRET_KEYS}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.toast("Settings exported")
        except OSError as e:
            messagebox.showerror("Couldn't export", str(e))

    def import_settings(self):
        path = filedialog.askopenfilename(parent=self, title="Import settings",
                                          filetypes=[("Settings file", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("not a settings file")
        except (OSError, ValueError) as e:
            messagebox.showerror("Couldn't import", f"That file can't be read as settings: {e}")
            return
        d = {k: v for k, v in d.items() if k not in self.SECRET_KEYS}
        self._apply_settings(d)
        self._set_dict(self.dict_spec, self.dict_file)
        th = d.get("theme")
        th = th if isinstance(th, dict) and th.get("v") == THEME_VERSION else dict(THEME)
        self.after_idle(lambda: self.set_theme(th))
        self._patch_settings({"presets": self.presets})
        self._save_settings()
        self.toast("Settings imported")

    # ---- name lists
    def _update_check_count(self):
        names, bad = parse_name_list(self.check_text.get("1.0", "end"))
        text = f"{len(names):,} valid name(s)" + (f", {bad} ignored" if bad else "")
        if self.check_count.winfo_exists():
            self.check_count.configure(text=text)

    def _load_name_file(self):
        path = filedialog.askopenfilename(parent=self, title="Load names",
                                          filetypes=[("Text files", "*.txt *.csv *.lst"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                self.check_text.insert("end", f.read() + "\n")
            self._update_check_count()
        except OSError as e:
            messagebox.showerror("Couldn't read the file", str(e))

    def _check_listed(self):
        names, _ = parse_name_list(self.check_text.get("1.0", "end"))
        if not names:
            messagebox.showinfo("Check names", "Type or paste at least one valid name first.")
            return
        self.start(names=names)

    def retry_errors(self):
        if self._running():
            return
        if not self.error_names:
            self.toast("No errors to retry")
            return
        self.start(names=list(dict.fromkeys(self.error_names)))

    @staticmethod
    def _open_path(path):
        try:
            if IS_WINDOWS:
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Couldn't open it", str(e))

    def open_results(self):
        if not os.path.exists(self._results_file()):
            messagebox.showinfo("No results yet", "No available usernames have been saved yet.")
            return
        self._open_path(self._results_file())

    def open_folder(self):
        self._open_path(BASE_DIR)

    def _copy_selected(self, _event=None):
        sel = self.found_list.curselection()
        if sel:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self.found_list.get(i) for i in sel))
            self.toast("Copied" if len(sel) == 1 else f"Copied {len(sel)} names")

    def _export_found(self):
        if not self.found:
            self.toast("Nothing to export yet")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export available names", defaultextension=".txt",
            initialfile="available_usernames_export.txt",
            filetypes=[("Text file", "*.txt"), ("CSV file", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                if path.lower().endswith(".csv"):
                    f.write("username\n")
                f.write("\n".join(self.found) + "\n")
            self.toast(f"Exported {len(self.found)} names")
        except OSError as e:
            messagebox.showerror("Couldn't export", str(e))

    def _clear_found(self):
        self.found.clear()
        self.found_list.delete(0, "end")
        self.toast("List cleared (the saved file is untouched)")

    def _paste_token(self):
        try:
            text = (self.clipboard_get() or "").strip()
        except tk.TclError:
            text = ""
        if text:
            self.token_var.set(text)
            self.toast("Token pasted")
        else:
            self.toast("The clipboard is empty")

    def _copy_all(self):
        if self.found:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self.found))
            self.toast("Copied all")

    # ------------------------------------------------------ token / webhook
    @staticmethod
    def _headers(token):
        return {"Authorization": token, "Content-Type": "application/json"}

    # ---- saved accounts (manual switching only)
    def _acct_index(self):
        tok = self.token_var.get().strip()
        for i, a in enumerate(self.accounts):
            if tok and a["token"] == tok:
                return i
        return -1

    def _acct_sel(self):
        lb = getattr(self, "_acct_list", None)
        sel = lb.curselection() if lb is not None and lb.winfo_exists() else ()
        if sel and sel[0] < len(self.accounts):
            return sel[0]
        self.toast("Pick an account from the list first")
        return None

    def _fill_accounts(self):
        lb = getattr(self, "_acct_list", None)
        if lb is None or not lb.winfo_exists():
            return
        active = self._acct_index()
        lb.delete(0, "end")
        for i, a in enumerate(self.accounts):
            status = "\u2717 rejected" if a["bad"] else a["name"]
            mark = "\u25b6 " if i == active else "  "
            tail = "\u2026" + a["token"][-4:]
            lb.insert("end", f"{mark}{a['label']:<24}{status:<24}{tail}")
        if active >= 0:
            lb.selection_set(active)

    def _save_accounts(self):
        data = []
        if IS_WINDOWS and self.remember_var.get():
            try:
                data = [{"label": a["label"], "enc": protect(a["token"]), "name": a["name"], "bad": a["bad"]}
                        for a in self.accounts]
            except Exception:
                data = []
        self._patch_settings({"accounts": data})
        self._fill_accounts()

    def _note_account(self, token, name, bad):
        """Remember what a test (or a rejected scan) told us about a saved token."""
        for a in self.accounts:
            if a["token"] == token:
                if name:
                    a["name"] = name[:40]
                a["bad"] = bad
                self._save_accounts()
                return

    def add_account(self):
        tok = self.token_var.get().strip()
        if not tok:
            self.toast("Paste a token in the box above first")
            return
        label = self.acct_name_var.get().strip()[:30]
        for a in self.accounts:
            if a["token"] == tok:
                if label:
                    a["label"] = label
                self._save_accounts()
                self.toast("That token is already in the list")
                return
        if len(self.accounts) >= MAX_ACCOUNTS:
            messagebox.showwarning("Saved accounts", f"You can keep up to {MAX_ACCOUNTS} accounts. Remove one first.")
            return
        self.accounts.append({"label": label or f"Account {len(self.accounts) + 1}", "token": tok,
                              "name": "", "bad": False})
        self.acct_name_var.set("")
        self._save_accounts()
        self.toast("Account added")

    def _use_account(self, i):
        a = self.accounts[i]
        self.token_var.set(a["token"])
        self._tok = a["token"]
        self.acct_label.configure(text=f"Switched to {a['label']}" + (f" ({a['name']})" if a["name"] else "")
                                       + ". Press Test token to check it.", fg=MUTED)
        self.avatar.set_letter(a["name"][:1] if a["name"] else "")
        self._fill_accounts()
        if self._running():
            self._log(f"Switched to account '{a['label']}'", "info")
        self.toast(f"Using {a['label']}")

    def use_selected_account(self):
        i = self._acct_sel()
        if i is not None:
            self._use_account(i)

    def cycle_account(self, step=1):
        n = len(self.accounts)
        if not n:
            self.toast("No saved accounts yet - add some on the Account tab")
            return
        cur = self._acct_index()
        start = cur if cur >= 0 else (-1 if step > 0 else 0)
        pick = None
        for k in range(1, n + 1):
            i = (start + step * k) % n
            if not self.accounts[i]["bad"]:
                pick = i
                break
        if pick is None:                       # every account was rejected: still move on
            pick = (start + step) % n
        if pick == cur:
            self.toast("There is no other usable saved account")
            return
        self._use_account(pick)

    def rename_account(self):
        i = self._acct_sel()
        label = self.acct_name_var.get().strip()[:30]
        if i is None:
            return
        if not label:
            self.toast("Type the new label in the Label box first")
            return
        self.accounts[i]["label"] = label
        self.acct_name_var.set("")
        self._save_accounts()

    def remove_account(self):
        i = self._acct_sel()
        if i is None:
            return
        if not messagebox.askyesno("Remove account", f"Remove '{self.accounts[i]['label']}' from the list?"):
            return
        self.accounts.pop(i)
        self._save_accounts()
        self.toast("Account removed")

    def import_accounts(self):
        path = filedialog.askopenfilename(parent=self, title="Import tokens",
                                          filetypes=[("Text files", "*.txt *.lst"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            messagebox.showerror("Couldn't read the file", str(e))
            return
        known, added = {a["token"] for a in self.accounts}, 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            label, _, tok = line.rpartition("|")     # "label|token" or just "token"
            tok = tok.strip().strip('"')
            if not tok or any(c.isspace() for c in tok) or tok in known:
                continue
            if len(self.accounts) >= MAX_ACCOUNTS:
                break
            self.accounts.append({"label": label.strip()[:30] or f"Account {len(self.accounts) + 1}",
                                  "token": tok, "name": "", "bad": False})
            known.add(tok)
            added += 1
        self._save_accounts()
        self.toast(f"Imported {added} account(s)" if added else "No new tokens found in that file")

    def test_token(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("No token", "Paste a token first.")
            return
        self.acct_label.configure(text="Checking...", fg=MUTED)

        def job():
            try:
                r = requests.get(ME_URL, headers=self._headers(token), timeout=15)
                if r.status_code == 200:
                    who = r.json().get("username", "?")
                    self.q.put(("acct", f"Connected as {who}", OK, token, who, False))
                else:
                    self.q.put(("acct", f"Token rejected (HTTP {r.status_code})", ERR, token, None,
                                r.status_code == 401))
            except Exception as e:
                self.q.put(("acct", f"Connection error: {e}", ERR))

        threading.Thread(target=job, daemon=True).start()

    def _hook_url(self):
        url = self.hook_var.get().strip()
        if not url:
            messagebox.showwarning("Webhook", "Enter a webhook URL first (Webhook tab).")
        elif not WEBHOOK_RE.match(url):
            messagebox.showwarning("Webhook", "That doesn't look like a Discord webhook URL.\n"
                                              "It should start with https://discord.com/api/webhooks/")
        else:
            return url
        return None

    @staticmethod
    def _send_webhook(url, cfg, ctx):
        return requests.post(url, json=build_webhook_payload(cfg, ctx), timeout=15)

    def test_webhook(self):
        url = self._hook_url()
        if not url:
            return
        cfg, ctx = self._hook_cfg(), hook_ctx("example", self.stats)

        def job():
            try:
                r = self._send_webhook(url, cfg, ctx)
                ok = r.status_code in (200, 204)
                msg = "Test message sent." if ok else f"Test failed (HTTP {r.status_code})."
                self.q.put(("log", "Webhook: " + msg, "ok" if ok else "err"))
                self.q.put(("hook_info", msg, "ok" if ok else "err"))
            except Exception as e:
                self.q.put(("log", f"Webhook error: {e}", "err"))
                self.q.put(("hook_info", f"Webhook error: {e}", "err"))

        threading.Thread(target=job, daemon=True).start()

    def _pick_avatar(self):
        path = filedialog.askopenfilename(
            parent=self, title="Choose a picture",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.webp"), ("All files", "*.*")])
        if path:
            self._avatar_path = path
            self.avatar_label.configure(text=os.path.basename(path))

    def apply_hook_identity(self, remove_avatar=False):
        """Change the webhook's own name and/or profile picture (PATCH on the webhook URL)."""
        url = self._hook_url()
        if not url:
            return
        name, path = self.hook_name_var.get().strip(), self._avatar_path
        if not (name or path or remove_avatar):
            messagebox.showinfo("Webhook", "Type a name and/or choose a picture first.")
            return
        self._hook_info = ("Updating...", "muted")
        self.hook_info_lbl.configure(text="Updating...", fg=MUTED)

        def job():
            try:
                body = {}
                if name:
                    body["name"] = name[:80]
                if remove_avatar:
                    body["avatar"] = None
                elif path:
                    with open(path, "rb") as f:
                        data = f.read()
                    if len(data) > 8 * 1024 * 1024:
                        raise ValueError("that picture is larger than 8 MB")
                    mime = sniff_image(data)
                    if not mime:
                        raise ValueError("unsupported picture type (use PNG, JPG, GIF or WEBP)")
                    body["avatar"] = f"data:{mime};base64," + base64.b64encode(data).decode()
                r = requests.patch(url.split("?")[0], json=body, timeout=30)
                if r.status_code == 200:
                    self.q.put(("hook_info", "Webhook updated.", "ok"))
                else:
                    try:
                        why = r.json().get("message", "")
                    except ValueError:
                        why = ""
                    self.q.put(("hook_info", f"Discord refused the change (HTTP {r.status_code}) {why}".strip(), "err"))
            except Exception as e:
                self.q.put(("hook_info", f"Couldn't update the webhook: {e}", "err"))

        threading.Thread(target=job, daemon=True).start()

    def load_hook_identity(self):
        url = self._hook_url()
        if not url:
            return

        def job():
            try:
                r = requests.get(url.split("?")[0], timeout=15)
                if r.status_code == 200:
                    self.q.put(("hook_name", r.json().get("name") or ""))
                    self.q.put(("hook_info", "Loaded the webhook's current name.", "ok"))
                else:
                    self.q.put(("hook_info", f"Couldn't load it (HTTP {r.status_code}).", "err"))
            except Exception as e:
                self.q.put(("hook_info", f"Couldn't load it: {e}", "err"))

        threading.Thread(target=job, daemon=True).start()

    # -------------------------------------------------------------- running
    def _names_plan(self, names):
        """A plan that checks exactly these names (Check names tab / Retry errors)."""
        pace = self._read_pacing(lambda t, m: messagebox.showerror(t, m))
        if pace is None:
            return None
        return {"lo": 2, "hi": 32, "tot_lo": 2, "tot_hi": 32, "limit": 0, "chars": "", "words": list(names),
                "wmin": 1, "wmax": 1, "must": "", "place": "any", "sequential": True, "exclude": [],
                "prefix": "", "suffix": "", "skip_known": False, "hook_done": self.hook_done_var.get(),
                "results_file": self._results_file(), **pace}

    def start(self, names=None):
        token = self.token_var.get().strip()
        self._tok = token
        if not token:
            messagebox.showwarning("No token", "Paste your token first (Account tab).")
            self._show_tab("account")
            return
        hook = ""
        if self.hook_on.get():
            hook = self._hook_url()
            if not hook:
                return
        settings = self._read_settings() if names is None else self._names_plan(names)
        if not settings or (self.worker and self.worker.is_alive()):
            return
        eta = count_names(settings) * settings["delay"]
        if eta > 3 * 86400 and not messagebox.askyesno(
                "Very long scan", f"At this delay the scan would take about {fmt_duration(eta)}.\n"
                                  f"(You can stop it at any time and the progress is remembered.)\n\nStart anyway?"):
            return
        self._save_settings()
        self.error_names = []

        self.stop_event.clear()
        self.stats = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
        self.total = count_names(settings)
        self.start_time = time.time()
        self.progress.reset()
        self._refresh_stats()
        settings["token"], settings["hook"], settings["hook_cfg"] = token, hook, self._hook_cfg()
        self.pause_event.clear()
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self._sync_pause_btn(True)
        self.set_status("Scanning", "run")
        extra = f", must contain '{settings['must']}' ({settings['place']})" if settings["must"] else ""
        if settings["prefix"] or settings["suffix"]:
            extra += f", start '{settings['prefix']}' end '{settings['suffix']}'"
        pace = f"delay {settings['dmin']:g}-{settings['dmax']:g}s"
        if names is not None:
            self._log(f"Checking {len(names):,} listed name(s), {pace}", "info")
        elif settings["words"] is not None:
            self._log(f"Scan started: {LANG_NAMES.get(self.dict_spec, 'custom list')} dictionary "
                      f"({len(settings['words']):,} words), {settings['wmin']}-{settings['wmax']} word(s) per name, "
                      f"lengths {settings['tot_lo']}-{settings['tot_hi']}{extra}, {pace}", "info")
        else:
            self._log(f"Scan started: lengths {settings['tot_lo']}-{settings['tot_hi']}, "
                      f"charset '{settings['chars']}'{extra}, {pace}", "info")
        self.worker = threading.Thread(target=self._run, args=(settings,), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.set_status("Stopping", "warn")

    def _wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(min(0.1, max(0.0, end - time.time())))

    def _run(self, s):
        self._tok = s["token"]
        session = requests.Session()
        reason = "Finished"
        pacer = Pacer(s["dmin"], s["dmax"])
        results_file = s.get("results_file") or RESULTS_FILE
        last_break = 0

        # names we already got an answer for in earlier runs (available ones are in RESULTS_FILE)
        done_before = set()
        for path in ((CHECKED_FILE, results_file) if s.get("skip_known", True) else ()):
            try:
                with open(path, encoding="utf-8") as f:
                    done_before.update(line.strip() for line in f if line.strip())
            except OSError:
                pass
        skipped = 0
        n = {"checked": 0, "available": 0, "taken": 0, "errors": 0}   # this scan's own tally
        found_names, t0 = [], time.time()

        def tally(kind):
            n[kind] += 1
            n["checked"] += 1

        try:
            checked_f = open(CHECKED_FILE, "a", encoding="utf-8", buffering=1)
        except OSError:
            checked_f = None

        try:
            for name in name_stream(s):
                if self.stop_event.is_set():
                    reason = "Stopped"
                    break
                if name in done_before:
                    skipped += 1
                    continue
                if s.get("time_limit") and time.time() - self.start_time > s["time_limit"] * 60:
                    reason = "Done: time limit reached"
                    break
                if s.get("break_every") and s.get("break_secs") and n["checked"] - last_break >= s["break_every"]:
                    last_break = n["checked"]
                    secs = s["break_secs"] * random.uniform(0.9, 1.1)
                    self.q.put(("log", f"Taking a {secs:.0f}s break...", "info"))
                    self.q.put(("status", "Break", "warn"))
                    self._wait(secs)
                    self.q.put(("status", "Scanning", "run"))
                self._wait(pacer.wait_time())
                retries = 0
                while not self.stop_event.is_set():
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.1)          # paused: hold here until resumed or stopped
                    if self.stop_event.is_set():
                        break
                    try:
                        tok_used = self._tok or s["token"]
                        r = session.post(CHECK_URL, headers=self._headers(tok_used), json={"username": name},
                                         timeout=15)
                    except requests.RequestException as e:
                        retries += 1
                        if retries > 3:
                            tally("errors")
                            self.q.put(("result", name, "error", f"network error: {e}"))
                            break
                        self.q.put(("log", f"Network error, retrying ({retries}/3)...", "warn"))
                        self._wait(5)
                        continue

                    if r.status_code == 429:
                        try:
                            wait = float(r.headers.get("Retry-After") or r.json().get("retry_after", 5))
                        except Exception:
                            wait = 5.0
                        pacer.limited(wait)
                        self.q.put(("log", f"Rate limited. Waiting {wait:.1f}s, "
                                           f"slowing to {pacer.interval:.2f}s/req", "warn"))
                        self.q.put(("status", "Rate limited", "warn"))
                        self._wait(pacer.wait_time())
                        self.q.put(("status", "Scanning", "run"))
                        continue

                    if r.status_code == 401:
                        label = next((a["label"] for a in list(self.accounts) if a["token"] == tok_used), "")
                        self.q.put(("log", "Token was rejected (401)" + (f" for '{label}'" if label else "")
                                    + ". Stopping - switch account and start again.", "err"))
                        self.q.put(("acct_bad", tok_used))
                        reason = "Invalid token"
                        self.stop_event.set()
                        break

                    try:
                        data = r.json()
                    except ValueError:
                        tally("errors")
                        self.q.put(("result", name, "error", f"bad response (HTTP {r.status_code})"))
                        break

                    pacer.ok(r.headers)
                    self._pace = pacer.interval
                    done_before.add(name)
                    if data.get("taken") is True:
                        if checked_f:
                            checked_f.write(name + "\n")
                        tally("taken")
                        self.q.put(("result", name, "taken", ""))
                    elif data.get("taken") is False:
                        self._save_result(results_file, name)
                        if s["hook"]:
                            try:
                                self._send_webhook(s["hook"], s["hook_cfg"], hook_ctx(name, n))
                            except Exception as e:
                                self.q.put(("log", f"Webhook failed: {e}", "err"))
                        tally("available")
                        found_names.append(name)
                        self.q.put(("result", name, "available", ""))
                        if s.get("stop_after") and n["available"] >= s["stop_after"]:
                            reason = f"Done: found {n['available']} name(s)"
                            self.stop_event.set()
                    else:
                        tally("errors")
                        self.q.put(("result", name, "error", str(data.get("message", data))))
                    break

                if self.stop_event.is_set():
                    if reason == "Finished":
                        reason = "Stopped"
                    break
            if skipped:
                self.q.put(("log", f"Skipped {skipped:,} names checked in earlier runs.", "info"))
        except Exception as e:
            reason = f"Crashed: {e}"
        finally:
            if checked_f:
                checked_f.close()
        if s["hook"] and s.get("hook_done") and reason != "Invalid token":
            try:
                desc = (f"{reason}. {n['checked']:,} checked, {n['available']} available, "
                        f"{n['taken']} taken, {n['errors']} errors in {fmt_duration(time.time() - t0)}.")
                if found_names:
                    desc += "\nAvailable: " + ", ".join(found_names[:20]) + (" ..." if len(found_names) > 20 else "")
                embed = {"title": "Scan finished", "description": desc[:4000],
                         "color": s["hook_cfg"]["color"] & 0xFFFFFF,
                         "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
                requests.post(s["hook"], json={"embeds": [embed]}, timeout=15)
            except Exception as e:
                self.q.put(("log", f"Summary webhook failed: {e}", "err"))
        self.q.put(("done", reason))

    @staticmethod
    def _save_result(path, name):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(name + "\n")
        except OSError:
            pass

    # ------------------------------------------------ queue -> UI thread
    def _drain_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1], msg[2])
                elif kind == "acct":
                    self.acct_label.configure(text=msg[1], fg=msg[2])
                    self.avatar.set_letter(msg[1][13:14] if msg[1].startswith("Connected as ") else "")
                    if len(msg) > 3:
                        self._note_account(msg[3], msg[4], msg[5])
                elif kind == "acct_bad":
                    self._note_account(msg[1], None, True)
                elif kind == "dict_done":
                    self._dict_finished(msg[1], msg[2])
                elif kind == "hook_info":
                    self._hook_info = (msg[1], msg[2])
                    if self.hook_info_lbl.winfo_exists():
                        self.hook_info_lbl.configure(text=msg[1], fg=self._hook_color(msg[2]))
                elif kind == "hook_name":
                    self.hook_name_var.set(msg[1])
                elif kind == "status":
                    if not self.stop_event.is_set() and not self.pause_event.is_set():
                        self.set_status(msg[1], msg[2])
                elif kind == "result":
                    _, name, status, extra = msg
                    self.stats["checked"] += 1
                    if status == "available":
                        self.stats["available"] += 1
                        self.found.append(name)
                        self.found_list.insert("end", name)
                        self.found_list.see("end")
                        self._log(f"{name}  AVAILABLE", "ok")
                        self._notify_found()
                        if self.autocopy_var.get():
                            self.clipboard_clear()
                            self.clipboard_append(name)
                    elif status == "taken":
                        self.stats["taken"] += 1
                        if self.logtaken_var.get():
                            self._log(f"{name}  taken", "taken")
                        else:
                            self._log_hist.append((time.strftime("%H:%M:%S"), f"{name}  taken", "taken"))
                    else:
                        self.stats["errors"] += 1
                        self._log(f"{name}  error: {extra}", "err")
                        if len(self.error_names) < 5000:
                            self.error_names.append(name)
                    self._refresh_stats()
                elif kind == "done":
                    reason = msg[1]
                    self.pause_event.clear()
                    self.start_btn.set_enabled(True)
                    self.stop_btn.set_enabled(False)
                    self._sync_pause_btn(False)
                    self._pace = 0.0
                    self.set_status(reason.split(":")[0], "err" if reason == "Invalid token" else "idle")
                    if reason == "Finished":
                        self.progress.set(1.0)
                    self.progress_label.configure(
                        text=f"{reason}   |   {self.stats['checked']:,} checked", fg=TEXT)
                    self._log(f"{reason}. {self.stats['available']} available name(s) saved to "
                              f"{self._results_file()}", "info")
                    self._save_settings()
                    self._refresh_history()
                    self.title(APP_TITLE)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _on_close(self):
        if self._running() and not messagebox.askyesno("Scan in progress",
                                                        "A scan is still running. Quit anyway?"):
            return
        self.stop_event.set()
        self.pause_event.clear()
        self._save_settings()
        self.destroy()


def main():
    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
