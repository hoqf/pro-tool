# Pro Tool - browser version of the DSV GUI username scanner.
#
# Based on DSV by suenerve (https://github.com/suenerve/DSV)
# Original author credit is required wherever this code is used.
# Original license: NON-commercial use only.
#
# NOTE: Automating a user account is against Discord's Terms of Service and can get
# the account suspended. Use an alt account and a generous delay. Use at your own risk.
#
# Run:   python dsv_web.py            -> http://127.0.0.1:8765 (this PC only)
#        python dsv_web.py --lan      -> also reachable from your phone / other PCs on your
#                                        network; a private access link is printed at start-up.
#
# Uses only the Python standard library plus `requests` (already needed by the GUI).
# It shares settings, saved accounts, dictionaries and results with DSV_GUI.exe.

import argparse
import base64
import datetime
import hmac
import json
import os
import random
import re
import secrets
import socket
import string
import sys
import threading
import time
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

import dsv_gui as core   # name generator, pacer, webhook payloads, DPAPI helpers

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

# ------------------------------------------------------------------ updates
# release.py sets these. Updates are signed: the app only installs an update whose signature matches
# UPDATE_PUBKEY, so nobody but the holder of the private signing key can push one.
APP_VERSION = "2.1.2"
UPDATE_REPO = "hoqf/pro-tool-updates"
UPDATE_PUBKEY = "UIyHAE1nXAPFRJCc8ZkIEe3WjiwJoxXZoIqKpOpk8Ww="
UPDATE_ASSET = "ProToolV2.exe"
WEB_DIR = os.path.join(getattr(sys, "_MEIPASS", HERE), "web")

# ------------------------------------------------------------------ data files
# Use the same folder as DSV_GUI.exe (dist/) when it exists, so both apps share data.
DATA_DIR = HERE
SETTINGS_FILE = RESULTS_FILE = CHECKED_FILE = DICT_DIR = ""


def set_data_dir(path):
    global DATA_DIR, SETTINGS_FILE, RESULTS_FILE, CHECKED_FILE, DICT_DIR, LOGIN_FILE
    DATA_DIR = os.path.abspath(path)
    os.makedirs(DATA_DIR, exist_ok=True)
    LOGIN_FILE = os.path.join(DATA_DIR, "pro-tool-login.json")
    SETTINGS_FILE = os.path.join(DATA_DIR, "dsv_settings.json")
    RESULTS_FILE = os.path.join(DATA_DIR, "available_usernames.txt")
    CHECKED_FILE = os.path.join(DATA_DIR, "dsv_checked.txt")
    DICT_DIR = os.path.join(DATA_DIR, "dictionaries")


DEFAULTS = {
    "min": "4", "max": "4", "dmin": "2", "dmax": "4", "limit": "0", "break_every": "0",
    "break_secs": "30", "time_limit": "0", "stop_after": "0", "prefix": "", "suffix": "",
    "custom_chars": "", "wmin": "1", "wmax": "1", "must": "", "exclude": "",
    # default search: random 4-letter names, letters a-z only
    "letters": True, "digits": False, "underscore": False, "period": False,
    "mode": "random", "place": "any", "skip_known": True, "log_taken": True,
    "dict_lang": "", "dict_file": "", "dict_skip_caps": True,
    "hook_on": False, "hook_done": False, "hook_name": "",
    "hook_content": core.DEFAULT_HOOK["content"], "hook_embed": core.DEFAULT_HOOK["embed"],
    "hook_title": core.DEFAULT_HOOK["title"], "hook_desc": core.DEFAULT_HOOK["desc"],
    "hook_footer": core.DEFAULT_HOOK["footer"], "hook_color": core.DEFAULT_HOOK["color"],
    "hook_ts": core.DEFAULT_HOOK["timestamp"], "remember_token": True, "results_path": "",
    "auto_switch": False,   # move to the next saved account when the current one is rate limited
    "notify": True, "sound": True,   # browser notification / chime when a name is found
    "test_mode": False,              # generate names only: no token, Discord never contacted, nothing saved
    "describe": "",                  # Search page: words describing the kind of names you want
    "min_rating": "0",               # only make names rated from ...
    "max_rating": "10",              # ... to this, out of 10 (0-10 = any)
    # Search page word builder: base words + prefixes / suffixes / either-side affixes
    "build_on": False, "build_plain": False, "build_bases": "",
    "build_pre": "neo, pro, ultra, hyper, mega, re, un, ex, xo, lil",
    "build_suf": "er, ly, ify, ist, io, ix, os, y, ium, ism, ed, ing",
    "build_aff": "x, z, o",
    "ui": {},                        # Personalization page (look & motion); see UI_SPEC
    "theme": dict(core.DEFAULT_THEME), "presets": {},
}
STR_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, str) and k not in
            ("mode", "place", "dict_lang", "dict_file", "results_path")]
BOOL_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, bool)]
PRESET_KEYS = ("min", "max", "dmin", "dmax", "break_every", "break_secs", "time_limit", "prefix",
               "suffix", "custom_chars", "limit", "letters", "digits", "underscore", "period", "mode",
               "wmin", "wmax", "must", "place", "exclude", "stop_after", "dict_lang", "dict_file",
               "dict_skip_caps", "skip_known", "build_on", "build_plain", "build_bases", "build_pre",
               "build_suf", "build_aff")

# Personalization page: number ranges and on/off switches the browser may save.
UI_SPEC = {"glass": (0.0, 2.0), "bg": (0.0, 1.0), "parstr": (0.0, 2.0), "spin": (0.05, 1.0)}
UI_BOOLS = ("parallax", "solar", "intro")

# The settings on the web app's Search tab - what a web preset saves and restores.
SEARCH_KEYS = ("min", "max", "mode", "letters", "digits", "underscore", "period", "must", "place",
               "dict_lang", "dict_file", "wmin", "wmax", "dmin", "dmax", "stop_after", "skip_known",
               "build_on", "build_plain", "build_bases", "build_pre", "build_suf", "build_aff")

LOCK = threading.RLock()


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def patch_settings(changes):
    """Update some keys in dsv_settings.json without touching the rest (the GUI's keys survive)."""
    with LOCK:
        d = load_settings()
        d.update(changes)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)


def merged_settings():
    d = load_settings()
    if "delay" in d and "dmin" not in d:
        d["dmin"] = d["dmax"] = d["delay"]
    out = {}
    for k, v in DEFAULTS.items():
        val = d.get(k, v)
        if isinstance(v, bool):
            val = bool(val)
        elif isinstance(v, str):
            val = str(val) if val is not None else v
        out[k] = val
    if out["mode"] not in ("shortest", "random"):
        out["mode"] = "shortest"
    if out["place"] not in ("any", "start", "end"):
        out["place"] = "any"
    if not isinstance(out["presets"], dict):
        out["presets"] = {}
    if not isinstance(out["theme"], dict):
        out["theme"] = dict(core.DEFAULT_THEME)
    if not isinstance(out["ui"], dict):
        out["ui"] = {}
    out.update(WEB_FIXED)
    return out


# Options the (minimal) web UI no longer shows. They're pinned off here so values saved by the
# desktop app can't silently change a web scan. The desktop app's own copies stay untouched.
WEB_FIXED = {"prefix": "", "suffix": "", "exclude": "", "custom_chars": "", "limit": "0",
             "break_every": "0", "time_limit": "0"}


def clean_settings(body):
    """Keep only known, well-typed settings from a browser request."""
    out = {}
    body = {k: v for k, v in body.items() if k not in WEB_FIXED}
    for k in STR_KEYS:
        if k in body and isinstance(body[k], (str, int, float)):
            out[k] = str(body[k])[:500]
    for k in BOOL_KEYS:
        if k in body:
            out[k] = bool(body[k])
    if body.get("mode") in ("shortest", "random"):
        out["mode"] = body["mode"]
    if body.get("place") in ("any", "start", "end"):
        out["place"] = body["place"]
    if isinstance(body.get("ui"), dict):
        ui = {}
        for k, (lo, hi) in UI_SPEC.items():
            try:
                if k in body["ui"]:
                    ui[k] = max(lo, min(hi, float(body["ui"][k])))
            except (TypeError, ValueError):
                pass
        for k in UI_BOOLS:
            if k in body["ui"]:
                ui[k] = bool(body["ui"][k])
        out["ui"] = ui
    if isinstance(body.get("theme"), dict):
        t = body["theme"]
        try:
            out["theme"] = {"hue": float(t.get("hue", 0)) % 360, "sat": 1.0, "val": 1.0, "lift": 0.0,
                            "mode": t.get("mode") if t.get("mode") in ("dark", "black", "light") else "dark",
                            "v": core.THEME_VERSION}
        except (TypeError, ValueError):
            pass
    return out


def results_file(cfg=None):
    cfg = cfg or merged_settings()
    p = cfg.get("results_path") or ""
    return p if p and os.path.isdir(os.path.dirname(p) or ".") else RESULTS_FILE


# ------------------------------------------------------------------ secrets
class Secrets:
    """Token, saved accounts and webhook URL - kept server-side, never sent back to the browser
    in full. Saved with Windows DPAPI exactly like the desktop app."""

    def __init__(self):
        self.token = ""
        self.hook = ""
        self.accounts = []      # {"label", "token", "name", "bad", "why"}
        self.bad_why = {}       # token -> why it stopped working (also covers a pasted token that isn't saved)
        self.alerts = []        # token alerts for the page: {"id", "label", "tail", "why"}
        self._alerted = {}      # token -> when it was last alerted (no repeats within a minute)
        d = load_settings()
        if d.get("hook_enc"):
            try:
                self.hook = core.unprotect(d["hook_enc"])
            except Exception:
                pass
        if core.IS_WINDOWS and d.get("remember_token"):
            if d.get("token_enc"):
                try:
                    self.token = core.unprotect(d["token_enc"])
                except Exception:
                    pass
            for a in (d.get("accounts") or [])[:core.MAX_ACCOUNTS]:
                try:
                    tok = core.unprotect(a["enc"])
                except Exception:
                    continue
                if tok:
                    self.accounts.append({"label": str(a.get("label") or f"Account {len(self.accounts) + 1}")[:30],
                                          "token": tok, "name": str(a.get("name") or "")[:40],
                                          "bad": bool(a.get("bad")), "why": str(a.get("why") or "")[:120]})
                    if self.accounts[-1]["bad"]:
                        self.bad_why[tok] = self.accounts[-1]["why"] or "rejected"

    def save(self):
        remember = core.IS_WINDOWS and merged_settings()["remember_token"]
        ch = {"accounts": [], "token_enc": ""}
        try:
            ch["hook_enc"] = core.protect(self.hook) if self.hook else ""
            if remember:
                ch["token_enc"] = core.protect(self.token) if self.token else ""
                ch["accounts"] = [{"label": a["label"], "enc": core.protect(a["token"]), "name": a["name"],
                                   "bad": a["bad"], "why": a.get("why", "")} for a in self.accounts]
        except Exception:
            pass
        patch_settings(ch)

    def active_index(self):
        return next((i for i, a in enumerate(self.accounts) if self.token and a["token"] == self.token), -1)

    def note(self, token, name, bad):
        if not bad:
            self.bad_why.pop(token, None)                   # works again
        for a in self.accounts:
            if a["token"] == token:
                if name:
                    a["name"] = name[:40]
                a["bad"] = bad
                if not bad:
                    a["why"] = ""
                self.save()
                return

    def flag(self, token, why):
        """A token stopped working: mark it (shown in yellow in the list) and raise an alert."""
        if not token:
            return
        self.bad_why[token] = why
        label = "Pasted token"
        for a in self.accounts:
            if a["token"] == token:
                a["bad"], a["why"], label = True, why, a["label"]
        self.save()
        now = time.time()
        if now - self._alerted.get(token, 0) < 60:
            return                                          # already alerted about this one just now
        self._alerted[token] = now
        with LOCK:
            last = self.alerts[-1]["id"] if self.alerts else 0
            self.alerts.append({"id": max(int(now * 1000), last + 1), "label": label, "tail": token[-4:], "why": why})
            del self.alerts[:-20]
        for hook in list(TOKEN_HOOKS):                      # e.g. a Windows notification
            try:
                hook(label, token[-4:], why)
            except Exception:
                pass

    def public(self):
        return {
            "token_set": bool(self.token), "token_tail": self.token[-4:] if self.token else "",
            "hook": self.hook, "active": self.active_index(),
            "token_bad": self.bad_why.get(self.token, "") if self.token else "",
            "accounts": [{"label": a["label"], "name": a["name"], "bad": a["bad"], "why": a.get("why", ""),
                          "tail": a["token"][-4:]} for a in self.accounts],
        }


SECRETS = None

# ------------------------------------------------------------------ dictionaries
_dict_cache = {}
_pool_cache = [None, None]
DOWNLOADING = set()
DICT_MSG = {"text": "", "kind": "muted"}


VOWELS = "aeiouy"


def name_rating(name):
    """Rating out of 10 - the same rules as the rating shown next to found names:
    length (up to 4), digits (up to 2), dots/underscores (up to 2), easy to say (1), flows well (1)."""
    n = len(name)
    score = 4 if n <= 3 else 3 if n <= 5 else 2 if n <= 7 else 1 if n <= 9 else 0
    digits = len(re.findall(r"[0-9]", name))
    score += 2 if digits == 0 else 1 if digits == 1 else 0
    marks = len(re.findall(r"[._]", name))
    score += 2 if marks == 0 else 1 if marks == 1 else 0
    longest, run, last = 0, 0, None              # longest run of consonants or of vowels (other characters break a run)
    for ch in name:
        kind = ("v" if ch in VOWELS else "c") if "a" <= ch <= "z" else None
        run = run + 1 if kind and kind == last else (1 if kind else 0)
        last = kind
        longest = max(longest, run)
    has_vowel = any(ch in VOWELS for ch in name)
    if has_vowel and longest < 5:
        score += 1
    if has_vowel and longest < 3:
        score += 1
    return score


def rating_range(cfg):
    """(lowest, highest) rating to make, out of 10 - or None when every rating is allowed."""
    def num(key, default):
        try:
            return max(0, min(10, int(cfg.get(key) or default)))
        except (TypeError, ValueError):
            return default
    lo, hi = num("min_rating", 0), num("max_rating", 10)
    if lo > hi:
        lo, hi = hi, lo
    return None if (lo, hi) == (0, 10) else (lo, hi)


def out_of_range(name, rng):
    return rng is not None and not (rng[0] <= name_rating(name) <= rng[1])


def range_text(rng):
    return f"{rng[0]}/10" if rng[0] == rng[1] else f"{rng[0]}-{rng[1]}/10"


DESCRIBE_URL = "https://api.datamuse.com/words"     # free word-association service (no account)


def described_words(text):
    """Words related to the describing words typed on the Search page (e.g. "dark, ocean" ->
    shadow, abyss, tide, ...). Only those describing words are sent, to the word service."""
    seeds = [w for w in re.split(r"[\s,;/]+", text.lower()) if re.fullmatch(r"[a-z]{2,20}", w)]
    seeds = list(dict.fromkeys(seeds))[:10]
    if not seeds:
        raise PlanError("Describe", "Type a few describing words, for example: dark, ocean, fast.")
    found, failed = list(seeds), 0
    for w in seeds:
        try:
            r = requests.get(DESCRIBE_URL, params={"ml": w, "max": 80}, timeout=10)
            for item in r.json():
                word = str(item.get("word", "")).lower()
                if re.fullmatch(r"[a-z]{2,16}", word):        # single words that can be part of a username
                    found.append(word)
        except Exception:
            failed += 1
    return seeds, list(dict.fromkeys(found)), failed == len(seeds)


def dict_path(code):
    return os.path.join(DICT_DIR, code + ".dic")


def dict_words(cfg):
    spec = cfg["dict_lang"]
    path = cfg["dict_file"] if spec == "file" else dict_path(spec)
    skip = cfg["dict_skip_caps"] and spec not in core.KEEP_CAPS
    key = (spec, path, skip)
    if key not in _dict_cache:
        if not os.path.isfile(path):
            what = "The chosen word list file is missing." if spec == "file" else \
                f"The {core.LANG_NAMES.get(spec, spec)} dictionary hasn't been downloaded yet."
            return None, what + " Open the Dictionary tab and pick a language."
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                words = core.parse_wordlist(f.read(), skip)
        except OSError as e:
            return None, f"Couldn't read the dictionary: {e}"
        _dict_cache.clear()
        _dict_cache[key] = words
    return _dict_cache[key], None


def word_pool(cfg, hi, chars, must, exclude):
    pool, err = dict_words(cfg)
    if err:
        return None, err
    key = (id(pool), hi, chars, must, tuple(exclude))
    if _pool_cache[0] != key:
        allowed = set(chars)
        _pool_cache[0] = key
        _pool_cache[1] = [w for w in pool if len(w) <= hi and w != must and allowed.issuperset(w)
                          and not any(x in w for x in exclude)]
    return _pool_cache[1], None


def dict_label(cfg):
    if not cfg["dict_lang"]:
        return "Off (all combinations)"
    if cfg["dict_lang"] == "file":
        return "Custom list: " + os.path.basename(cfg["dict_file"])
    return core.LANG_NAMES.get(cfg["dict_lang"], cfg["dict_lang"])


def download_dict(code):
    name = core.LANG_NAMES.get(code, code)
    try:
        r = requests.get(core.DICT_URL.format(code=code), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"server answered HTTP {r.status_code}")
        os.makedirs(DICT_DIR, exist_ok=True)
        tmp = dict_path(code) + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, dict_path(code))
        patch_settings({"dict_lang": code, "dict_file": ""})
        words, err = dict_words(merged_settings())
        DICT_MSG.update(text=err or f"{name} ready: {len(words):,} usable words.", kind="err" if err else "ok")
    except Exception as e:
        DICT_MSG.update(text=f"Couldn't download {name}: {e}", kind="err")
    finally:
        DOWNLOADING.discard(code)


# ------------------------------------------------------------------ plans
class PlanError(Exception):
    def __init__(self, title, msg):
        super().__init__(msg)
        self.title, self.msg = title, msg


def charset(cfg):
    custom = "".join(dict.fromkeys(ch for ch in cfg["custom_chars"].lower()
                                   if ch in string.ascii_lowercase + string.digits + "_."))
    if custom:
        return custom
    return ((string.ascii_lowercase if cfg["letters"] else "") + (string.digits if cfg["digits"] else "")
            + ("_" if cfg["underscore"] else "") + ("." if cfg["period"] else ""))


def read_pacing(cfg):
    try:
        dmin, dmax = float(cfg["dmin"]), float(cfg["dmax"])
        break_every = int(cfg["break_every"] or 0)
        break_secs = float(cfg["break_secs"] or 0)
        time_limit = float(cfg["time_limit"] or 0)
        stop_after = int(cfg["stop_after"] or 0)
    except ValueError:
        raise PlanError("Invalid input", "Delays, breaks and stop limits must be numbers.")
    if not (core.MIN_INTERVAL <= dmin <= dmax <= core.MAX_DELAY):
        raise PlanError("Invalid delay", f"Delay must satisfy {core.MIN_INTERVAL * 1000:g} <= from <= to <= "
                                         f"{core.MAX_DELAY * 1000:g} ms.")
    if min(break_every, break_secs, time_limit, stop_after) < 0 or break_secs > 3600:
        raise PlanError("Invalid input", "Break, time limit and 'stop after' can't be negative "
                                         "(a break can last at most an hour).")
    return {"dmin": dmin, "dmax": dmax, "delay": (dmin + dmax) / 2.0, "break_every": break_every,
            "break_secs": break_secs, "time_limit": time_limit, "stop_after": stop_after}


BUILD_MAX = 500_000          # most names the word builder makes for one scan


def affix_list(text, label):
    """'neo, pro ultra' -> ['neo', 'pro', 'ultra'] (cleaned like the other name text)."""
    out = []
    for part in re.split(r"[,\s]+", (text or "").strip()):
        if not part:
            continue
        x = core.fold_text(part)
        if not x:
            raise PlanError(label, f"'{part}' can only use letters a-z, digits, _ and . (no two periods in a row).")
        if x not in out:
            out.append(x)
    return out


def built_words(cfg, lo, hi, must, exclude, pool):
    """Word builder: every base word with each prefix in front, each suffix behind, and both together.
    'Either side' parts are used as a prefix and as a suffix. Returns (names, number of base words)."""
    pre = affix_list(cfg["build_pre"], "Prefixes")
    suf = affix_list(cfg["build_suf"], "Suffixes")
    aff = affix_list(cfg["build_aff"], "Either side")
    if cfg["build_bases"].strip():
        bases = affix_list(cfg["build_bases"], "Base words")
    elif pool is not None:
        bases = pool
    else:
        raise PlanError("Word builder", "Type some base words, or turn on a word list (Real words or "
                                        "Describe the names) for the builder to use.")
    fronts = list(dict.fromkeys(pre + aff))
    backs = list(dict.fromkeys(suf + aff))
    if not (fronts or backs):
        raise PlanError("Word builder", "Add at least one prefix, suffix or either-side part.")
    bases = list(bases)
    random.shuffle(bases)                                 # a new random mix each scan (big word lists included)
    seen, out = set(), []

    def add(name):
        if lo <= len(name) <= hi and name not in seen and ".." not in name and (not must or must in name) \
                and not any(x in name for x in exclude):
            seen.add(name)
            out.append(name)

    for b in bases:
        if cfg["build_plain"]:
            add(b)
        for f in fronts:
            add(f + b)
        for k in backs:
            add(b + k)
        for f in fronts:
            for k in backs:
                add(f + b + k)
        if len(out) >= BUILD_MAX:
            break
    if not out:
        raise PlanError("Word builder", f"None of the built words fit your length setting ({lo}-{hi} characters "
                                        "after Start/End text). Raise 'To' under Length or use shorter parts.")
    return out[:BUILD_MAX], len(bases)


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def update_manifest_url():
    return os.environ.get("PT_UPDATE_URL") or f"https://github.com/{UPDATE_REPO}/releases/latest/download/latest.json"


def update_message(m):
    """The exact text the signature covers: version, file hash and size (so the file can't be swapped)."""
    return f"pro-tool-update\n{m['version']}\n{m['sha256']}\n{int(m['size'])}".encode()


def verify_manifest(m):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    import base64
    if not UPDATE_PUBKEY:
        raise ValueError("this copy has no update key")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(UPDATE_PUBKEY))
    from cryptography.exceptions import InvalidSignature
    try:
        key.verify(base64.b64decode(m["sig"]), update_message(m))
    except (InvalidSignature, ValueError):
        raise ValueError("the update isn't signed by the publisher, so it was ignored")


UPDATE = {"checked": 0, "info": None, "busy": False, "error": ""}


def check_for_update():
    """Ask the release page for the newest version. Returns {"available", "version", "notes"}."""
    r = requests.get(update_manifest_url(), params={"t": int(time.time())}, timeout=12,   # always the newest,
                     headers={"Cache-Control": "no-cache"})                               # not a cached copy
    if r.status_code == 404:
        return {"available": False, "version": APP_VERSION, "notes": ""}      # nothing published yet
    r.raise_for_status()
    m = r.json()
    for k in ("version", "sha256", "size", "url", "sig"):
        if k not in m:
            raise ValueError(f"update info is missing '{k}'")
    verify_manifest(m)
    newer = version_tuple(m["version"]) > version_tuple(APP_VERSION)
    UPDATE.update(checked=time.time(), info=m if newer else None)
    return {"available": newer, "version": m["version"], "notes": str(m.get("notes", ""))[:2000]}


def install_update():
    """Download the newer exe, check it is exactly the signed file, put it in place of this one and
    restart. The old exe is renamed (a running exe can be renamed, not overwritten) and deleted next start."""
    import hashlib, subprocess
    m = UPDATE.get("info")
    if not m:
        raise PlanError("Update", "Check for updates first.")
    if not getattr(sys, "frozen", False):
        raise PlanError("Update", "Updates install into the .exe version. This copy is running from the Python files.")
    exe = os.path.abspath(sys.executable)
    new, old = exe + ".new", exe + ".old"
    h, size = hashlib.sha256(), 0
    with requests.get(m["url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(new, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk); h.update(chunk); size += len(chunk)
    if size != int(m["size"]) or h.hexdigest() != m["sha256"]:
        os.remove(new)
        raise PlanError("Update", "The download didn't match the signed update, so it wasn't installed. Try again later.")
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    os.replace(exe, old)                       # step the running copy aside...
    try:
        os.replace(new, exe)                   # ...and put the new one in its place
    except OSError:
        os.replace(old, exe)                   # couldn't: put the old one back
        raise
    args = [a for a in sys.argv[1:] if a != "--after-update"] + ["--after-update"]

    env = {k: v for k, v in os.environ.items() if not (k.startswith("_PYI") or k.startswith("_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # start as a fresh app, not as part of this one (which is closing)

    def restart():
        time.sleep(1.0)                        # let the page get its answer first
        subprocess.Popen([exe] + args, close_fds=True, env=env, cwd=os.path.dirname(exe),
                         creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        os._exit(0)
    threading.Thread(target=restart, daemon=True).start()
    return {"ok": True, "version": m["version"]}


def cleanup_old_exe():
    """After an update: remove the previous exe (it may take a moment to be released)."""
    if not getattr(sys, "frozen", False):
        return
    old = os.path.abspath(sys.executable) + ".old"

    def go():
        for _ in range(30):
            try:
                if os.path.exists(old):
                    os.remove(old)
                return
            except OSError:
                time.sleep(1)
    threading.Thread(target=go, daemon=True).start()


def build_plan(cfg):
    """Validate settings and return a generation plan for core.name_stream (raises PlanError)."""
    pace = read_pacing(cfg)
    try:
        lo, hi = int(cfg["min"]), int(cfg["max"])
        limit = int(cfg["limit"] or 0)
    except ValueError:
        raise PlanError("Invalid input", "Lengths and max names must be numbers.")
    if lo < 2 or hi > 32 or lo > hi:
        raise PlanError("Invalid length", "Lengths must satisfy 2 <= min <= max <= 32 (Discord's limit).")
    if limit < 0:
        raise PlanError("Invalid input", "Max names can't be negative.")
    chars = charset(cfg)
    if not chars:
        raise PlanError("No characters", "Turn on at least one character type (Search tab).")
    bad_txt = "can only use letters a-z (accents are removed), digits, _ and . - and no two periods in a row."
    affixes = {}
    for key, label in (("prefix", "Start with"), ("suffix", "End with")):
        raw = cfg[key].strip()
        val = core.fold_text(raw) if raw else ""
        if raw and not val:
            raise PlanError(label, "That text " + bad_txt)
        affixes[key] = val or ""
    pre, suf = affixes["prefix"], affixes["suffix"]
    affix = len(pre) + len(suf)
    tot_lo, tot_hi = lo, hi
    lo, hi = max(1, lo - affix), hi - affix
    if hi < 1:
        raise PlanError("Start / end text", f"'{pre}' + '{suf}' already use {affix} characters, which leaves "
                                            f"no room under your max length ({tot_hi}).")
    must = ""
    raw = cfg["must"].strip()
    if raw:
        must = core.fold_text(raw)
        if not must:
            raise PlanError("Must contain", "That text " + bad_txt)
        if len(must) > hi:
            raise PlanError("Must contain", f"'{must}' has {len(must)} characters, which is longer than the "
                                            f"{hi} left under your max length ({tot_hi}).")
    exclude = []
    for part in re.split(r"[,\s]+", cfg["exclude"].strip()):
        x = core.fold_text(part) if part else None
        if x and x not in exclude:
            exclude.append(x)
    if any(x in must or x in pre or x in suf for x in exclude):
        raise PlanError("Skip names containing", "Your 'must contain' / start / end text includes something "
                                                 "you asked to skip.")
    words, wmin, wmax, built = None, 1, 1, None
    if cfg["dict_lang"] or cfg["build_on"]:
        try:
            wmin, wmax = int(cfg["wmin"]), int(cfg["wmax"])
        except ValueError:
            raise PlanError("Words per name", "Words per name must be whole numbers.")
        if not (1 <= wmin <= wmax <= core.MAX_WORDS):
            raise PlanError("Words per name", f"Words per name must satisfy 1 <= from <= to <= {core.MAX_WORDS}.")
        if cfg["dict_lang"]:
            words, err = word_pool(cfg, hi, chars, must if not cfg["build_on"] else "", exclude)
            if err:
                raise PlanError("Dictionary", err)
        if cfg["build_on"]:                               # one built word per name; 'must contain' checked here
            words, nbase = built_words(cfg, lo, hi, must, exclude, words)
            built = {"bases": nbase, "names": len(words)}
            wmin = wmax = 1
            must = ""
    plan = {"lo": lo, "hi": hi, "limit": limit, "chars": chars, "words": words, "prefix": pre, "suffix": suf,
            "tot_lo": tot_lo, "tot_hi": tot_hi, **pace, "wmin": wmin, "wmax": wmax, "must": must,
            "place": cfg["place"], "sequential": cfg["mode"] == "shortest" and not built,   # built names are always picked at random
            "exclude": exclude, "built": built,
            "skip_known": cfg["skip_known"], "hook_done": cfg["hook_done"], "results_file": results_file(cfg)}
    if core.count_names(plan) == 0:
        raise PlanError("Nothing to search", "These settings can't produce any names. Check the lengths, "
                                             "words per name, dictionary and 'must contain' text.")
    return plan


def names_plan(cfg, names):
    pace = read_pacing(cfg)
    return {"lo": 2, "hi": 32, "tot_lo": 2, "tot_hi": 32, "limit": 0, "chars": "", "words": list(names),
            "wmin": 1, "wmax": 1, "must": "", "place": "any", "sequential": True, "exclude": [],
            "prefix": "", "suffix": "", "skip_known": False, "hook_done": cfg["hook_done"],
            "results_file": results_file(cfg), **pace}


def hook_cfg(cfg):
    try:
        color = int(cfg["hook_color"].lstrip("#")[:6], 16)
    except ValueError:
        color = 0xFF0000
    return dict(content=cfg["hook_content"], embed=cfg["hook_embed"], title=cfg["hook_title"],
                desc=cfg["hook_desc"], footer=cfg["hook_footer"], timestamp=cfg["hook_ts"], color=color)


def headers(token):
    return {"Authorization": token, "Content-Type": "application/json"}


# ------------------------------------------------------------------ scanner
class Scanner:
    LOG_MAX = 3000

    def __init__(self):
        self.worker = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.log = []                 # {"id", "t", "text", "kind"}
        self.seq = 0
        self.found = []               # found since the server started (or last clear)
        self.error_names = []
        self.stats = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
        self.status, self.status_kind = "Idle", "idle"
        self.total = 0
        self.start_time = 0.0
        self.pace = 0.0
        self.reason = ""
        self.switches = 0

    def running(self):
        return bool(self.worker and self.worker.is_alive())

    def add_log(self, text, kind="info"):
        with LOCK:
            self.seq += 1
            self.log.append({"id": self.seq, "t": time.strftime("%H:%M:%S"), "text": text, "kind": kind})
            if len(self.log) > self.LOG_MAX:
                del self.log[: len(self.log) - self.LOG_MAX]

    def set_status(self, text, kind):
        if not self.stop_event.is_set() and not self.pause_event.is_set():
            self.status, self.status_kind = text, kind

    @staticmethod
    def _account_label():
        i = SECRETS.active_index()
        if i >= 0:
            return SECRETS.accounts[i]["label"]
        return f"token ...{SECRETS.token[-4:]}" if SECRETS.token else ""

    def snapshot(self, since):
        with LOCK:
            st = dict(self.stats)
            elapsed = time.time() - self.start_time if self.start_time else 0
            remaining = max(0, self.total - st["checked"]) if self.total else 0
            status = "Paused" if self.pause_event.is_set() and self.running() else self.status
            return {"running": self.running(), "paused": self.pause_event.is_set(), "status": status,
                    "status_kind": "warn" if status == "Paused" else self.status_kind,
                    "stats": st, "total": self.total, "elapsed": elapsed,
                    "eta": remaining * self.pace if self.running() and self.pace else 0,
                    "pace": self.pace, "found": list(self.found[-500:]), "errors_pending": len(self.error_names),
                    "account": self._account_label(), "active": SECRETS.active_index(), "switches": self.switches,
                    "token_alerts": list(SECRETS.alerts[-10:]),
                    "log": [e for e in self.log if e["id"] > since][-600:], "seq": self.seq}

    # -- control
    def start(self, names=None):
        if self.running():
            raise PlanError("Already running", "A scan is already running.")
        cfg = merged_settings()
        if cfg.get("test_mode"):
            return self._start_test(cfg, names)
        token = SECRETS.token
        if not token:
            raise PlanError("No token", "Add your token first (Account tab), or turn on Test mode in Personalization.")
        hook = ""
        if cfg["hook_on"]:
            hook = SECRETS.hook
            if not hook or not core.WEBHOOK_RE.match(hook):
                raise PlanError("Webhook", "Webhook notifications are on but no valid Discord webhook URL is "
                                           "set (Webhook tab).")
        s = build_plan(cfg) if names is None else names_plan(cfg, names)
        s["rating"] = rating_range(cfg) if names is None else None
        s["token"], s["hook"], s["hook_cfg"] = token, hook, hook_cfg(cfg)
        s["auto_switch"] = cfg["auto_switch"]
        if s["auto_switch"]:
            usable = sum(1 for a in SECRETS.accounts if not a["bad"])
            if usable < 2:
                self.add_log("Auto-switch is on, but you have fewer than 2 usable saved accounts, "
                             "so rate limits will just be waited out.", "warn")
        with LOCK:
            self.error_names = []
            self.stop_event.clear()
            self.pause_event.clear()
            self.stats = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
            self.total = core.count_names(s)
            self.start_time = time.time()
            self.status, self.status_kind = "Scanning", "run"
            self.reason = ""
            self.switches = 0
        extra = f", must contain '{s['must']}' ({s['place']})" if s["must"] else ""
        if s["prefix"] or s["suffix"]:
            extra += f", start '{s['prefix']}' end '{s['suffix']}'"
        pace = f"delay {s['dmin'] * 1000:g}-{s['dmax'] * 1000:g} ms"
        if names is not None:
            self.add_log(f"Checking {len(names):,} listed name(s), {pace}")
        elif s["words"] is not None:
            self.add_log(f"Scan started: {dict_label(cfg)} dictionary ({len(s['words']):,} words), "
                         f"{s['wmin']}-{s['wmax']} word(s) per name, lengths {s['tot_lo']}-{s['tot_hi']}{extra}, {pace}")
        else:
            self.add_log(f"Scan started: lengths {s['tot_lo']}-{s['tot_hi']}, charset '{s['chars']}'{extra}, {pace}")
        self.worker = threading.Thread(target=self._run, args=(s,), daemon=True)
        self.worker.start()
        return {"total": self.total, "eta": self.total * s["delay"]}

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        if self.running():
            self.status, self.status_kind = "Stopping", "warn"

    def toggle_pause(self):
        if not self.running():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.add_log("Resumed")
        else:
            self.pause_event.set()
            self.add_log("Paused", "warn")

    def _switch_account(self, tok_used, cooldown, why, force=False):
        """Move to the next saved account that isn't rejected or still cooling down.
        If every account is cooling down, pick the one that frees up first and wait for it,
        unless that's no sooner than the current account (then return False: just wait it out)."""
        accts = list(SECRETS.accounts)
        if len(accts) < 2:
            return False
        now = time.monotonic()
        cur = next((i for i, a in enumerate(accts) if a["token"] == tok_used), -1)
        order = [accts[(cur + k) % len(accts)] for k in range(1, len(accts) + 1)]
        options = [a for a in order if not a["bad"] and a["token"] != tok_used]
        if not options:
            return False
        ready = [a for a in options if cooldown.get(a["token"], 0) <= now]
        pick = ready[0] if ready else min(options, key=lambda a: cooldown.get(a["token"], 0))
        free_in = max(0.0, cooldown.get(pick["token"], 0) - now)
        if not force and free_in >= max(0.0, cooldown.get(tok_used, 0) - now):
            return False                       # nobody frees up sooner than the current account
        old = next((a["label"] for a in accts if a["token"] == tok_used), "current account")
        SECRETS.token = pick["token"]
        try:
            SECRETS.save()
        except Exception:
            pass
        with LOCK:
            self.switches += 1
        msg = f"'{old}' {why} - switched to '{pick['label']}'" + (f" (waiting {free_in:.0f}s for it)" if free_in else "")
        self.add_log(msg, "warn")
        if free_in:
            self.set_status("All accounts limited", "warn")
            self._wait(free_in)
            self.set_status("Scanning", "run")
        return True

    def _wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(min(0.1, max(0.0, end - time.time())))

    def _result(self, name, status, extra=""):
        with LOCK:
            self.stats["checked"] += 1
            if status == "available":
                self.stats["available"] += 1
                self.found.append(name)
                self.add_log(f"{name}  AVAILABLE", "ok")
            elif status == "taken":
                self.stats["taken"] += 1
                self.add_log(f"{name}  taken", "taken")
            else:
                self.stats["errors"] += 1
                self.add_log(f"{name}  error: {extra}", "err")
                if len(self.error_names) < 5000:
                    self.error_names.append(name)
        if status == "available":
            for hook in list(FOUND_HOOKS):         # e.g. the desktop app's Windows notification
                try:
                    hook(name)
                except Exception as e:
                    print(f"Notification failed: {e}")

    TEST_PACE = 0.1          # seconds per name in test mode (fast, but still readable in the log)

    def _start_test(self, cfg, names):
        """Test mode: run the name generator exactly like a scan, but check nothing. No token is needed,
        Discord is never contacted, nothing is written to the results or history files, and no webhooks
        or notifications are sent."""
        s = build_plan(cfg) if names is None else names_plan(cfg, names)
        s["rating"] = rating_range(cfg) if names is None else None
        with LOCK:
            self.error_names = []
            self.stop_event.clear()
            self.pause_event.clear()
            self.stats = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
            self.total = core.count_names(s)
            self.start_time = time.time()
            self.status, self.status_kind = "Test mode", "run"
            self.reason = ""
            self.switches = 0
        what = f"{len(names):,} listed name(s)" if names is not None else f"names, lengths {s['tot_lo']}-{s['tot_hi']}"
        self.add_log(f"TEST MODE: generating {what}. Nothing is checked or saved, and Discord isn't contacted.", "warn")
        self.worker = threading.Thread(target=self._run_test, args=(s,), daemon=True)
        self.worker.start()
        return {"total": self.total, "eta": self.total * self.TEST_PACE, "test": True}

    def _run_test(self, s):
        reason = "Finished"
        low = 0
        try:
            for name in core.name_stream(s):
                if out_of_range(name, s.get("rating")):
                    low += 1
                    continue
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.1)
                if self.stop_event.is_set():
                    reason = "Stopped"
                    break
                if s.get("time_limit") and time.time() - self.start_time > s["time_limit"] * 60:
                    reason = "Done: time limit reached"
                    break
                with LOCK:
                    self.stats["checked"] += 1
                    self.add_log(name, "test")              # shown, rated, in the Scan tab's Test names window
                self._wait(self.TEST_PACE)
        except Exception as e:
            reason = f"Crashed: {e}"
        with LOCK:
            self.pause_event.clear()
            self.pace = 0.0
            self.reason = reason
            self.status = "Test " + reason.split(":")[0].lower()
            self.status_kind = "idle"
        if low:
            self.add_log(f"Skipped {low:,} names not rated {range_text(s['rating'])}.")
        self.add_log(f"Test mode {reason.lower()}: {self.stats['checked']:,} name(s) generated. Nothing was checked or saved.")

    def _run(self, s):
        session = requests.Session()
        reason = "Finished"
        pacer = core.Pacer(s["dmin"], s["dmax"])
        cooldown = {}        # token -> time.monotonic() when its rate limit ends
        rfile = s.get("results_file") or RESULTS_FILE
        last_break = 0
        done_before = set()
        for path in ((CHECKED_FILE, rfile) if s.get("skip_known", True) else ()):
            try:
                with open(path, encoding="utf-8") as f:
                    done_before.update(line.strip() for line in f if line.strip())
            except OSError:
                pass
        skipped = low_rated = 0
        n = {"checked": 0, "available": 0, "taken": 0, "errors": 0}
        found_names, t0 = [], time.time()

        def tally(kind):
            n[kind] += 1
            n["checked"] += 1

        try:
            checked_f = open(CHECKED_FILE, "a", encoding="utf-8", buffering=1)
        except OSError:
            checked_f = None
        try:
            for name in core.name_stream(s):
                if self.stop_event.is_set():
                    reason = "Stopped"
                    break
                if name in done_before:
                    skipped += 1
                    continue
                if out_of_range(name, s.get("rating")):
                    low_rated += 1                           # below the minimum rating: never sent to Discord
                    continue
                if s.get("time_limit") and time.time() - self.start_time > s["time_limit"] * 60:
                    reason = "Done: time limit reached"
                    break
                if s.get("break_every") and s.get("break_secs") and n["checked"] - last_break >= s["break_every"]:
                    last_break = n["checked"]
                    secs = s["break_secs"] * random.uniform(0.9, 1.1)
                    self.add_log(f"Taking a {secs:.0f}s break...")
                    self.set_status("Break", "warn")
                    self._wait(secs)
                    self.set_status("Scanning", "run")
                self._wait(pacer.wait_time())
                retries = 0
                while not self.stop_event.is_set():
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.1)
                    if self.stop_event.is_set():
                        break
                    tok_used = SECRETS.token or s["token"]   # follows account switches mid-scan
                    try:
                        r = session.post(core.CHECK_URL, headers=headers(tok_used), json={"username": name},
                                         timeout=15)
                    except requests.RequestException as e:
                        retries += 1
                        if retries > 3:
                            tally("errors")
                            self._result(name, "error", f"network error: {e}")
                            break
                        self.add_log(f"Network error, retrying ({retries}/3)...", "warn")
                        self._wait(5)
                        continue
                    if r.status_code == 429:
                        try:
                            wait = float(r.headers.get("Retry-After") or r.json().get("retry_after", 5))
                        except Exception:
                            wait = 5.0
                        cooldown[tok_used] = time.monotonic() + wait
                        if s.get("auto_switch") and self._switch_account(tok_used, cooldown, "was rate limited"):
                            # the new account carries on after the normal delay; no slow-down needed
                            pacer.next_at = time.monotonic() + random.uniform(s["dmin"], s["dmax"])
                            self._wait(pacer.wait_time())
                            continue
                        pacer.limited(wait)
                        self.add_log(f"Rate limited. Waiting {wait:.1f}s, slowing to {pacer.interval:.2f}s/req", "warn")
                        self.set_status("Rate limited", "warn")
                        self._wait(pacer.wait_time())
                        self.set_status("Scanning", "run")
                        continue
                    if r.status_code in (401, 403):
                        why = token_problem(r)
                        label = next((a["label"] for a in list(SECRETS.accounts) if a["token"] == tok_used), "")
                        SECRETS.flag(tok_used, why)
                        if s.get("auto_switch") and self._switch_account(tok_used, cooldown, f"stopped working ({why})", force=True):
                            continue
                        self.add_log("Token" + (f" for '{label}'" if label else "") + f" stopped working: {why}."
                                     + " Stopping - switch account and start again.", "err")
                        reason = "Token not working"
                        self.stop_event.set()
                        break
                    try:
                        data = r.json()
                    except ValueError:
                        tally("errors")
                        self._result(name, "error", f"bad response (HTTP {r.status_code})")
                        break
                    pacer.ok(r.headers)
                    self.pace = pacer.interval
                    done_before.add(name)
                    if data.get("taken") is True:
                        if checked_f:
                            checked_f.write(name + "\n")
                        tally("taken")
                        self._result(name, "taken")
                    elif data.get("taken") is False:
                        try:
                            with open(rfile, "a", encoding="utf-8") as f:
                                f.write(name + "\n")
                        except OSError:
                            pass
                        if s["hook"]:
                            try:
                                requests.post(s["hook"], json=core.build_webhook_payload(
                                    s["hook_cfg"], core.hook_ctx(name, n)), timeout=15)
                            except Exception as e:
                                self.add_log(f"Webhook failed: {e}", "err")
                        tally("available")
                        found_names.append(name)
                        self._result(name, "available")
                        if s.get("stop_after") and n["available"] >= s["stop_after"]:
                            reason = f"Done: found {n['available']} name(s)"
                            self.stop_event.set()
                    else:
                        tally("errors")
                        self._result(name, "error", str(data.get("message", data)))
                    break
                if self.stop_event.is_set():
                    if reason == "Finished":
                        reason = "Stopped"
                    break
            if skipped:
                self.add_log(f"Skipped {skipped:,} names checked in earlier runs.")
            if low_rated:
                self.add_log(f"Skipped {low_rated:,} names not rated {range_text(s['rating'])}.")
        except Exception as e:
            reason = f"Crashed: {e}"
        finally:
            if checked_f:
                checked_f.close()
        if s["hook"] and s.get("hook_done") and reason != "Invalid token":
            try:
                desc = (f"{reason}. {n['checked']:,} checked, {n['available']} available, "
                        f"{n['taken']} taken, {n['errors']} errors in {core.fmt_duration(time.time() - t0)}.")
                if found_names:
                    desc += "\nAvailable: " + ", ".join(found_names[:20]) + (" ..." if len(found_names) > 20 else "")
                embed = {"title": "Scan finished", "description": desc[:4000],
                         "color": s["hook_cfg"]["color"] & 0xFFFFFF,
                         "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
                requests.post(s["hook"], json={"embeds": [embed]}, timeout=15)
            except Exception as e:
                self.add_log(f"Summary webhook failed: {e}", "err")
        with LOCK:
            self.pause_event.clear()
            self.pace = 0.0
            self.reason = reason
            self.status = reason.split(":")[0]
            self.status_kind = "err" if reason == "Invalid token" else "idle"
        self.add_log(f"{reason}. {self.stats['available']} available name(s) saved to {rfile}")


SCANNER = Scanner()
FOUND_HOOKS = []          # functions called with each newly available username
TOKEN_HOOKS = []          # functions called with (label, last 4 characters, why) when a token stops working


# ------------------------------------------------------------------ API
def history_count():
    try:
        with open(CHECKED_FILE, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def api_config():
    cfg = merged_settings()
    langs = [{"name": nm, "code": c, "downloaded": os.path.isfile(dict_path(c)), "downloading": c in DOWNLOADING}
             for nm, c in core.LANGUAGES]
    return {"version": APP_VERSION, "can_update": bool(getattr(sys, "frozen", False) and UPDATE_PUBKEY),
            "settings": {k: v for k, v in cfg.items() if k != "presets"}, "presets": sorted(cfg["presets"]),
            "secrets": SECRETS.public(), "languages": langs, "dict_label": dict_label(cfg),
            "dict_msg": DICT_MSG, "history": history_count(), "results_file": results_file(cfg),
            "data_dir": DATA_DIR, "is_windows": core.IS_WINDOWS, "credit": core.CREDIT,
            "favourites": [f for f in (load_settings().get("favourites") or []) if isinstance(f, str)],
            "signup_url": "https://discord.com/register",
            "templates": [{"name": nm, "values": dict(core.DEFAULT_HOOK, **v)} for nm, v in core.HOOK_TEMPLATES]}


def api_estimate(body):
    cfg = merged_settings()
    cfg.update(clean_settings(body))
    try:
        p = build_plan(cfg)
    except PlanError as e:
        return {"ok": False, "title": e.title, "error": e.msg}
    total = core.count_names(p)
    out = {"ok": True, "total": total, "eta": total * p["delay"], "examples": core.sample_names(p, 8)}
    if p.get("built"):
        out["built"] = p["built"]
    elif p["words"] is not None:
        out["pool"] = len(p["words"])
    return out


def token_problem(r):
    """Why Discord turned a token away, in plain words."""
    if r.status_code == 401:
        return "rejected - invalid or expired (401)"
    try:
        msg = str(r.json().get("message") or "")[:80]
    except Exception:
        msg = ""
    return "blocked (403)" + (f": {msg}" if msg else " - the account may be locked or need verification")


def test_token(token):
    try:
        r = requests.get(core.ME_URL, headers=headers(token), timeout=15)
        if r.status_code == 200:
            who = r.json().get("username", "?")
            SECRETS.note(token, who, False)
            return {"ok": True, "msg": f"Connected as {who}", "name": who}
        if r.status_code in (401, 403):
            why = token_problem(r)
            SECRETS.flag(token, why)
            return {"ok": False, "msg": f"Token not working: {why}"}
        return {"ok": False, "msg": f"Couldn't test the token right now (HTTP {r.status_code}) - try again shortly"}
    except Exception as e:
        return {"ok": False, "msg": f"Connection error: {e}"}


def need_hook():
    url = SECRETS.hook
    if not url:
        raise PlanError("Webhook", "Enter a webhook URL first.")
    if not core.WEBHOOK_RE.match(url):
        raise PlanError("Webhook", "That doesn't look like a Discord webhook URL. "
                                   "It should start with https://discord.com/api/webhooks/")
    return url


def handle_post(path, body):
    cfg = merged_settings()
    if path == "/api/settings":
        patch_settings(clean_settings(body))
        return {"ok": True}
    if path == "/api/estimate":
        return api_estimate(body)
    if path == "/api/start":
        if body.get("settings"):
            patch_settings(clean_settings(body["settings"]))
        names = None
        if body.get("names") is not None:
            names, bad = core.parse_name_list(str(body["names"]))
            if not names:
                raise PlanError("Check names", "No valid names in the list (2-32 characters: a-z, 0-9, _ and .).")
        elif body.get("retry"):
            names = list(dict.fromkeys(SCANNER.error_names))
            if not names:
                raise PlanError("Retry errors", "There are no names with errors to retry.")
        return dict(SCANNER.start(names), ok=True)
    if path == "/api/stop":
        SCANNER.stop()
        return {"ok": True}
    if path == "/api/pause":
        SCANNER.toggle_pause()
        return {"ok": True}
    if path == "/api/clear_log":
        with LOCK:
            SCANNER.log.clear()
        return {"ok": True}
    if path == "/api/clear_found":
        with LOCK:
            SCANNER.found.clear()
        return {"ok": True}
    if path == "/api/forget_history":
        try:
            os.remove(CHECKED_FILE)
        except OSError:
            pass
        return {"ok": True, "history": 0}

    # -- account / token
    if path == "/api/token":
        tok = str(body.get("token", "")).strip().strip('"')
        if tok and any(c.isspace() for c in tok):
            raise PlanError("Token", "A token can't contain spaces.")
        if not body.get("keep"):          # keep = only re-save (e.g. "remember" was toggled)
            SECRETS.token = tok
        if "remember_token" in body:
            patch_settings({"remember_token": bool(body["remember_token"])})
        SECRETS.save()
        return {"ok": True, "secrets": SECRETS.public()}
    if path == "/api/token/test":
        tok = str(body.get("token") or SECRETS.token).strip()
        if not tok:
            raise PlanError("No token", "Paste a token first.")
        return test_token(tok)
    if path == "/api/accounts/add":
        tok = str(body.get("token") or SECRETS.token).strip().strip('"')
        label = str(body.get("label", "")).strip()[:30]
        if not tok:
            raise PlanError("Saved accounts", "Paste a token first.")
        for a in SECRETS.accounts:
            if a["token"] == tok:
                if label:
                    a["label"] = label
                SECRETS.save()
                return {"ok": True, "msg": "That token is already in the list", "secrets": SECRETS.public()}
        if len(SECRETS.accounts) >= core.MAX_ACCOUNTS:
            raise PlanError("Saved accounts", f"You can keep up to {core.MAX_ACCOUNTS} accounts. Remove one first.")
        SECRETS.accounts.append({"label": label or f"Account {len(SECRETS.accounts) + 1}", "token": tok,
                                 "name": "", "bad": False})
        SECRETS.save()
        return {"ok": True, "msg": "Account added", "secrets": SECRETS.public()}
    if path in ("/api/accounts/use", "/api/accounts/remove", "/api/accounts/rename", "/api/accounts/test"):
        try:
            i = int(body.get("i"))
            a = SECRETS.accounts[i]
        except (TypeError, ValueError, IndexError):
            raise PlanError("Saved accounts", "That account no longer exists.")
        msg = ""
        if path.endswith("use"):
            SECRETS.token = a["token"]
            msg = f"Using {a['label']}"
            if SCANNER.running():
                SCANNER.add_log(f"Switched to account '{a['label']}'")
        elif path.endswith("remove"):
            SECRETS.accounts.pop(i)
            msg = "Account removed"
        elif path.endswith("rename"):
            label = str(body.get("label", "")).strip()[:30]
            if not label:
                raise PlanError("Rename", "Type a label first.")
            a["label"] = label
        else:
            res = test_token(a["token"])
            return dict(res, secrets=SECRETS.public())
        SECRETS.save()
        return {"ok": True, "msg": msg, "secrets": SECRETS.public()}
    if path == "/api/accounts/import":
        known, added = {a["token"] for a in SECRETS.accounts}, 0
        for line in str(body.get("text", "")).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            label, _, tok = line.rpartition("|")
            tok = tok.strip().strip('"')
            if not tok or any(c.isspace() for c in tok) or tok in known:
                continue
            if len(SECRETS.accounts) >= core.MAX_ACCOUNTS:
                break
            SECRETS.accounts.append({"label": label.strip()[:30] or f"Account {len(SECRETS.accounts) + 1}",
                                     "token": tok, "name": "", "bad": False})
            known.add(tok)
            added += 1
        SECRETS.save()
        return {"ok": True, "msg": f"Imported {added} account(s)" if added else "No new tokens found",
                "secrets": SECRETS.public()}
    if path == "/api/accounts/cycle":
        accts, n = SECRETS.accounts, len(SECRETS.accounts)
        if not n:
            raise PlanError("Saved accounts", "No saved accounts yet.")
        cur = SECRETS.active_index()
        start = cur if cur >= 0 else -1
        pick = next(((start + k) % n for k in range(1, n + 1) if not accts[(start + k) % n]["bad"]),
                    (start + 1) % n)
        if pick == cur:
            raise PlanError("Saved accounts", "There is no other usable saved account.")
        return handle_post("/api/accounts/use", {"i": pick})
    if path == "/api/accounts/test_all":
        results = []
        for a in list(SECRETS.accounts):
            res = test_token(a["token"])       # updates each account's name / bad flag
            results.append({"label": a["label"], "ok": res.get("ok", False), "msg": res.get("msg", "")})
        bad = sum(1 for r in results if not r["ok"])
        return {"ok": True, "secrets": SECRETS.public(), "results": results,
                "msg": f"Tested {len(results)} - {len(results) - bad} ok, {bad} failed" if results
                       else "No saved accounts to test"}

    # -- favourites (starred names, pinned at the top of Results)
    if path == "/api/favourite":
        name = str(body.get("name", "")).strip().lower()
        if not name:
            raise PlanError("Favourite", "No name given.")
        favs = [f for f in (load_settings().get("favourites") or []) if isinstance(f, str)]
        if body.get("on") and name not in favs:
            favs.append(name)
        elif not body.get("on"):
            favs = [f for f in favs if f != name]
        patch_settings({"favourites": favs[:2000]})
        return {"ok": True, "favourites": favs}

    # -- webhook
    if path == "/api/webhook":
        url = str(body.get("url", "")).strip()
        if url and not core.WEBHOOK_RE.match(url):
            raise PlanError("Webhook", "That doesn't look like a Discord webhook URL. "
                                       "It should start with https://discord.com/api/webhooks/")
        SECRETS.hook = url
        SECRETS.save()
        return {"ok": True}
    if path == "/api/webhook/test":
        url = need_hook()
        try:
            r = requests.post(url, json=core.build_webhook_payload(hook_cfg(cfg), core.hook_ctx("example", SCANNER.stats)),
                              timeout=15)
            ok = r.status_code in (200, 204)
            return {"ok": ok, "msg": "Test message sent." if ok else f"Test failed (HTTP {r.status_code})."}
        except Exception as e:
            return {"ok": False, "msg": f"Webhook error: {e}"}
    if path == "/api/webhook/load":
        url = need_hook()
        try:
            r = requests.get(url.split("?")[0], timeout=15)
            if r.status_code == 200:
                return {"ok": True, "name": r.json().get("name") or "", "msg": "Loaded the webhook's current name."}
            return {"ok": False, "msg": f"Couldn't load it (HTTP {r.status_code})."}
        except Exception as e:
            return {"ok": False, "msg": f"Couldn't load it: {e}"}
    if path == "/api/webhook/identity":
        url = need_hook()
        name = str(body.get("name", "")).strip()
        rb = {}
        if name:
            rb["name"] = name[:80]
        if body.get("remove_avatar"):
            rb["avatar"] = None
        elif body.get("avatar"):
            try:
                data = base64.b64decode(str(body["avatar"]).split(",")[-1])
            except Exception:
                raise PlanError("Webhook", "Couldn't read that picture.")
            if len(data) > 8 * 1024 * 1024:
                raise PlanError("Webhook", "That picture is larger than 8 MB.")
            mime = core.sniff_image(data)
            if not mime:
                raise PlanError("Webhook", "Unsupported picture type (use PNG, JPG, GIF or WEBP).")
            rb["avatar"] = f"data:{mime};base64," + base64.b64encode(data).decode()
        if not rb:
            raise PlanError("Webhook", "Type a name and/or choose a picture first.")
        try:
            r = requests.patch(url.split("?")[0], json=rb, timeout=30)
            if r.status_code == 200:
                return {"ok": True, "msg": "Webhook updated."}
            try:
                why = r.json().get("message", "")
            except ValueError:
                why = ""
            return {"ok": False, "msg": f"Discord refused the change (HTTP {r.status_code}) {why}".strip()}
        except Exception as e:
            return {"ok": False, "msg": f"Couldn't update the webhook: {e}"}

    # -- dictionary
    if path == "/api/dict/use":
        code = str(body.get("code", ""))
        if code == "":
            patch_settings({"dict_lang": "", "dict_file": ""})
            DICT_MSG.update(text="Dictionary is off: every combination will be checked.", kind="muted")
            return {"ok": True}
        if code not in core.LANG_NAMES:
            raise PlanError("Dictionary", "Unknown language.")
        if os.path.isfile(dict_path(code)):
            patch_settings({"dict_lang": code, "dict_file": ""})
            words, err = dict_words(merged_settings())
            DICT_MSG.update(text=err or f"{core.LANG_NAMES[code]}: {len(words):,} usable words.",
                            kind="err" if err else "ok")
            return {"ok": True}
        if code not in DOWNLOADING:
            DOWNLOADING.add(code)
            DICT_MSG.update(text=f"Downloading {core.LANG_NAMES[code]}...", kind="info")
            threading.Thread(target=download_dict, args=(code,), daemon=True).start()
        return {"ok": True, "downloading": True}
    if path == "/api/dict/custom":
        text = str(body.get("text", ""))
        fname = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(str(body.get("filename") or "custom.txt")))[:60]
        n = len(core.parse_wordlist(text, False))
        if not n:
            raise PlanError("Dictionary", "No usable words found. Use one word per line (letters a-z, digits, _ and .).")
        os.makedirs(DICT_DIR, exist_ok=True)
        dest = os.path.join(DICT_DIR, "custom_" + fname)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        patch_settings({"dict_lang": "file", "dict_file": dest})
        DICT_MSG.update(text=f"Custom list loaded: {n:,} usable words.", kind="ok")
        return {"ok": True}

    # -- updates
    if path == "/api/update/check":
        try:
            res = check_for_update()
        except PlanError:
            raise
        except Exception as e:
            return {"ok": False, "error": f"Couldn't check for updates: {e}", "current": APP_VERSION}
        return dict(res, ok=True, current=APP_VERSION)
    if path == "/api/update/install":
        if UPDATE["busy"]:
            raise PlanError("Update", "Already updating.")
        if SCANNER.running():
            raise PlanError("Update", "Stop the scan first, then update.")
        UPDATE["busy"] = True
        try:
            return install_update()
        except PlanError:
            raise
        except Exception as e:
            raise PlanError("Update", f"The update couldn't be installed: {e}")
        finally:
            UPDATE["busy"] = False

    # -- describe the names: related words become the scan's word list
    if path == "/api/describe":
        text = str(body.get("text", ""))[:300]
        seeds, words, offline = described_words(text)
        os.makedirs(DICT_DIR, exist_ok=True)
        dest = os.path.join(DICT_DIR, "custom_described_words.txt")
        with open(dest, "w", encoding="utf-8") as f:
            f.write("\n".join(words) + "\n")
        _dict_cache.clear()                                   # same file name, new words
        patch_settings({"dict_lang": "file", "dict_file": dest, "describe": text})
        cfg = merged_settings()
        try:
            lo, hi = int(cfg["min"]), int(cfg["max"])
        except ValueError:
            lo, hi = 2, 32
        fit = sum(1 for w in words if lo <= len(w) <= hi)
        DICT_MSG.update(text=f"Described words: {len(words):,} words related to {', '.join(seeds)}.", kind="ok")
        return {"ok": True, "count": len(words), "fit": fit, "lo": lo, "hi": hi, "words": words[:60], "offline": offline}

    # -- presets
    if path == "/api/presets/save":
        name = str(body.get("name", "")).strip()[:40]
        if not name:
            raise PlanError("Presets", "Type a name for the preset.")
        presets = dict(cfg["presets"])
        if name not in presets and len(presets) >= core.MAX_PRESETS:
            raise PlanError("Presets", f"You can keep up to {core.MAX_PRESETS} presets. Delete one first.")
        presets[name] = {k: cfg[k] for k in SEARCH_KEYS}     # only what the Search tab shows
        patch_settings({"presets": presets})
        return {"ok": True, "msg": f"Saved preset '{name}'"}
    if path in ("/api/presets/load", "/api/presets/delete"):
        name = str(body.get("name", ""))
        presets = dict(cfg["presets"])
        if name not in presets:
            raise PlanError("Presets", "That preset no longer exists.")
        if path.endswith("load"):
            p = {k: v for k, v in presets[name].items() if k in SEARCH_KEYS}   # ignore anything else (e.g. desktop-app presets)
            ch = clean_settings(p)
            for k in ("dict_lang", "dict_file"):
                if isinstance(p.get(k), str):
                    ch[k] = p[k]
            patch_settings(ch)
            return {"ok": True, "msg": f"Loaded preset '{name}'"}
        presets.pop(name)
        patch_settings({"presets": presets})
        return {"ok": True, "msg": f"Deleted preset '{name}'"}
    return None


# ------------------------------------------------------------------ start-up cleanup
RESET_MARK = "pro-tool-reset.pending"     # window storage an old reset couldn't remove; cleared on the next start


def finish_pending_reset():
    """Called at start-up, before the window opens: remove the window storage a reset couldn't."""
    mark = os.path.join(DATA_DIR, RESET_MARK)
    if not os.path.exists(mark):
        return
    import shutil
    left = False
    for d in ("app-webview", "app-window"):
        p = os.path.join(DATA_DIR, d)
        shutil.rmtree(p, ignore_errors=True)
        left = left or os.path.isdir(p)
    if not left:
        try:
            os.remove(mark)
        except OSError:
            pass


# ------------------------------------------------------------------ HTTP
ACCESS_KEY = ""          # set in --lan mode; every request must carry it
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProTool/1.0"

    def log_message(self, fmt, *args):   # keep the console quiet
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, url):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if not ACCESS_KEY:
            # local-only mode: refuse other Host names (blocks DNS-rebinding pages)
            return host in LOCAL_HOSTS
        c = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        got = c["dsvkey"].value if "dsvkey" in c else ""
        got = got or (parse_qs(url.query).get("key") or [""])[0]
        return hmac.compare_digest(got, ACCESS_KEY)

    def _gate(self, url):
        if self._allowed(url):
            return True
        self._send(403, b"Forbidden: open the access link printed in the Pro Tool window.",
                   "text/plain; charset=utf-8")
        return False

    def do_GET(self):
        url = urlparse(self.path)
        if not self._gate(url):
            return
        if ACCESS_KEY and "key" in parse_qs(url.query):   # store the key in a cookie, drop it from the URL
            self._send(302, b"", "text/plain", {"Location": url.path or "/",
                                                "Set-Cookie": f"dsvkey={ACCESS_KEY}; Path=/; HttpOnly; "
                                                              f"SameSite=Strict; Max-Age=31536000"})
            return
        if url.path == "/api/config":
            return self._send(200, api_config())
        if url.path == "/api/state":
            try:
                since = int((parse_qs(url.query).get("since") or ["0"])[0])
            except ValueError:
                since = 0
            return self._send(200, SCANNER.snapshot(since))
        if url.path == "/api/results":
            try:
                with open(results_file(), encoding="utf-8") as f:
                    names = [l.strip() for l in f if l.strip()]
            except OSError:
                names = []
            if parse_qs(url.query).get("download"):
                return self._send(200, ("\n".join(names) + "\n").encode(), "text/plain; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="available_usernames.txt"'})
            return self._send(200, {"names": names, "file": results_file()})
        rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_POST(self):
        url = urlparse(self.path)
        if not self._gate(url):
            return
        # JSON only: browsers can't send this cross-site without a CORS preflight we never answer
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, {"ok": False, "error": "JSON only"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 40 * 1024 * 1024:
            return self._send(413, {"ok": False, "error": "Too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            return self._send(400, {"ok": False, "error": "Bad JSON"})
        try:
            res = handle_post(url.path, body)
        except PlanError as e:
            return self._send(200, {"ok": False, "title": e.title, "error": e.msg})
        except Exception as e:
            return self._send(500, {"ok": False, "title": "Error", "error": str(e)})
        if res is None:
            return self._send(404, {"ok": False, "error": "not found"})
        self._send(200, res)


class ProServer(ThreadingHTTPServer):
    """On Windows, Python's default port reuse lets a second copy bind a port that's already in
    use (and silently get none of the traffic). Ask for the port exclusively instead, so a second
    launch fails cleanly and the app can open a window onto the copy that's already running."""
    allow_reuse_address = not core.IS_WINDOWS

    def server_bind(self):
        if core.IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "your-pc-ip"


# ------------------------------------------------------------------ app window
# The app opens Pro Tool in a Chromium "app" window (no tabs or address bar). It uses its own
# browser profile, so it runs as a separate process we can wait on: closing the window quits the app.
APP_BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
]


class WindowApi:
    """Called from the page's own title bar (window.pywebview.api.*) in the borderless app window."""
    def __init__(self):
        self._win = None          # leading underscore: pywebview doesn't expose it to the page
        self._max = False

    def minimize(self):
        self._let_min = True          # this one is ours (after the genie): let it straight through
        try:
            self._win.minimize()
        finally:
            self._let_min = False

    def toggle_maximize(self):
        self._win.restore() if self._max else self._win.maximize()

    def close(self):
        self._win.destroy()

    def os_anim(self, enabled):
        """Turn Windows' own minimize/restore animation for this window on or off. It's switched off
        while the page's genie effect is on, so the two don't play on top of each other."""
        if not core.IS_WINDOWS:
            return False
        import ctypes
        self._genie = not enabled     # the genie plays on minimize while Windows' own animation is off
        flag = ctypes.c_int(0 if enabled else 1)
        for hwnd in find_app_windows(getattr(self, "_title", "Pro Tool")):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(flag), ctypes.sizeof(flag))   # DWMWA_TRANSITIONS_FORCEDISABLED
        return True

    def hide_to_tray(self):
        """Hide the window (it leaves the taskbar); the app keeps running and the tray icon brings it back.
        Returns False, and does nothing, when there's no working tray icon to get it back with."""
        tray = getattr(self, "_tray", None)
        if not (tray and tray.ok):
            return False
        self._win.hide()
        if not getattr(self, "_told", False):        # say once where it went
            self._told = True
            try:
                tray.notifier.notify("Pro Tool is still running", "Click the Pro Tool icon by the clock to open it again.", sound=False)
            except Exception:
                pass
        return True

    HIT = {"w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}   # HTLEFT ... HTBOTTOMRIGHT

    def begin_resize(self, edge):
        """Edge/corner drag: follow the mouse from here (not from the page, which moves under the cursor
        as the window resizes) until the button is released. The opposite edges stay put."""
        if self._max or edge not in self.HIT or not core.IS_WINDOWS or getattr(self, "_resizing", False):
            return False
        hwnds = find_app_windows(getattr(self, "_title", "Pro Tool"))
        if not hwnds:
            return False
        self._resizing = True
        threading.Thread(target=self._resize_loop, args=(hwnds[0], edge), daemon=True).start()
        return True

    def _resize_loop(self, hwnd, edge):
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        button = 0x02 if u.GetSystemMetrics(23) else 0x01          # left button (right if the buttons are swapped)
        try:
            r0, p0, p = wintypes.RECT(), wintypes.POINT(), wintypes.POINT()
            u.GetWindowRect(hwnd, ctypes.byref(r0)); u.GetCursorPos(ctypes.byref(p0))
            try:
                scale = (u.GetDpiForWindow(hwnd) or 96) / 96
            except Exception:
                scale = 1.0
            mw, mh = round(980 * scale), round(640 * scale)          # same minimum size as the window
            last = None
            while u.GetAsyncKeyState(button) & 0x8000:
                u.GetCursorPos(ctypes.byref(p))
                dx, dy = p.x - p0.x, p.y - p0.y
                l, t, r, b = r0.left, r0.top, r0.right, r0.bottom
                if "e" in edge:
                    r = max(r0.right + dx, l + mw)
                if "w" in edge:
                    l = min(r0.left + dx, r - mw)
                if "s" in edge:
                    b = max(r0.bottom + dy, t + mh)
                if "n" in edge:
                    t = min(r0.top + dy, b - mh)
                if (l, t, r, b) != last:
                    u.SetWindowPos(hwnd, None, l, t, r - l, b - t, 0x0004 | 0x0010 | 0x0200)   # no z-order / activate / owner
                    last = (l, t, r, b)
                time.sleep(0.004)
        finally:
            self._resizing = False

    def resize_to(self, width, height, edge):
        """Edge/corner drag from the page (older way, used only if begin_resize isn't available). The opposite side stays put (dragging the left edge
        keeps the right edge where it is, and so on)."""
        from webview.window import FixPoint
        if self._max:
            return
        fix = (FixPoint.SOUTH if "n" in edge else FixPoint.NORTH) | (FixPoint.EAST if "w" in edge else FixPoint.WEST)
        self._win.resize(max(980, int(width)), max(640, int(height)), fix_point=fix)


def find_app_windows(title):
    """Handles of this process's visible top-level windows with the given title."""
    import ctypes
    from ctypes import wintypes
    user32, pid = ctypes.windll.user32, os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


def taskbar_minimize(api, title):
    """Clicking the taskbar button of the open app minimizes it. Borderless windows don't do that on their
    own: Windows only minimizes windows marked as minimizable. Add that mark (it shows no frame) and catch the
    request, so the genie can play first when it's on."""
    if not core.IS_WINDOWS:
        return
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.CallWindowProcW.restype = LRESULT
    done = getattr(api, "_taskbar_hooked", set())
    for hwnd in find_app_windows(title):
        style = u.GetWindowLongW(hwnd, -16)                                    # GWL_STYLE
        if not style & 0x00020000:
            u.SetWindowLongW(hwnd, -16, style | 0x00020000 | 0x00080000)      # WS_MINIMIZEBOX | WS_SYSMENU (no visible frame)
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)   # apply, change nothing else
        if hwnd in done:
            continue
        holder = {}                                                            # this window's original handler

        def proc(h, msg, wp, lp, holder=holder):
            if msg == 0x0112 and (wp & 0xFFF0) == 0xF020:                     # WM_SYSCOMMAND, SC_MINIMIZE (taskbar click, Win+Down)
                if getattr(api, "_let_min", False):
                    api._let_min = False
                elif getattr(api, "_genie", False):                           # genie first, then the page minimizes the window
                    threading.Thread(target=lambda: api._win.evaluate_js(
                        "window.__genieOut ? window.__genieOut(() => pywebview.api.minimize()) : pywebview.api.minimize()"),
                        daemon=True).start()
                    return 0
            return u.CallWindowProcW(holder["old"], h, msg, wp, lp)

        cb = WNDPROC(proc)
        holder["old"] = u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(cb, ctypes.c_void_p))   # GWLP_WNDPROC
        api._taskbar_procs = getattr(api, "_taskbar_procs", []) + [cb]           # keep it alive
        done.add(hwnd)
    api._taskbar_hooked = done


def round_window_corners(title):
    """Windows 11: ask the window manager to round our borderless window and give it a shadow."""
    if not core.IS_WINDOWS:
        return
    import ctypes
    dwm = ctypes.windll.dwmapi

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

    for hwnd in find_app_windows(title):
        pref = ctypes.c_int(2)                                  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))   # DWMWA_WINDOW_CORNER_PREFERENCE
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(1, 1, 1, 1)))      # soft window shadow


class WindowsNotifier:
    """Windows notifications from the desktop app: the app gets a notification-area (tray) icon
    while it's open, and found usernames pop up as Windows notifications with the app's icon."""

    def __init__(self, hwnd, icon_path, tip="Pro Tool", callback_msg=0):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

        self.shell = ctypes.windll.shell32
        self.shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        self.shell.Shell_NotifyIconW.restype = wintypes.BOOL
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        small = user32.LoadImageW(None, icon_path, 1, 16, 16, 0x10)      # IMAGE_ICON, LR_LOADFROMFILE
        large = user32.LoadImageW(None, icon_path, 1, 64, 64, 0x10)
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd, self.nid.uID = hwnd, 7001
        self.nid.uFlags = 0x02 | 0x04                                      # NIF_ICON | NIF_TIP
        if callback_msg:                                                   # clicks on the icon are sent to hwnd
            self.nid.uFlags |= 0x01                                        # NIF_MESSAGE
            self.nid.uCallbackMessage = callback_msg
        self.nid.hIcon, self.nid.hBalloonIcon = small, large
        self.nid.szTip = tip
        self.ok = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(self.nid)))   # NIM_ADD
        self.nid.uVersion = 4
        self.shell.Shell_NotifyIconW(4, ctypes.byref(self.nid))            # NIM_SETVERSION (modern notifications)

    def notify(self, title, text, sound=True):
        if not self.ok:
            return False
        self.nid.uFlags = 0x10                                             # NIF_INFO
        self.nid.szInfoTitle, self.nid.szInfo = title[:63], text[:255]
        self.nid.dwInfoFlags = 0x04 | 0x20 | (0 if sound else 0x10)        # NIIF_USER | NIIF_LARGE_ICON (| NIIF_NOSOUND)
        return bool(self.shell.Shell_NotifyIconW(1, self.ct.byref(self.nid)))   # NIM_MODIFY

    def close(self):
        if self.ok:
            self.shell.Shell_NotifyIconW(2, self.ct.byref(self.nid))       # NIM_DELETE
            self.ok = False


class TrayIcon:
    """The tray icon plus a small hidden window, on its own thread, that hears clicks on it:
    left-click (or clicking a notification) opens Pro Tool, right-click shows Open / Quit."""
    WM_TRAY = 0x8001                                    # WM_APP + 1

    def __init__(self, icon_path, on_open, on_quit):
        self.on_open, self.on_quit = on_open, on_quit
        self.notifier, self.ok, self.hwnd = None, False, None
        ready = threading.Event()
        threading.Thread(target=self._run, args=(icon_path, ready), daemon=True, name="tray").start()
        ready.wait(5)

    def _run(self, icon_path, ready):
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        k.GetModuleHandleW.restype = wintypes.HMODULE

        def later(fn):                                   # never block this window's message loop
            threading.Thread(target=fn, daemon=True).start()

        def menu(hwnd):
            m = u.CreatePopupMenu()
            u.AppendMenuW(m, 0, 1, "Open Pro Tool")
            u.AppendMenuW(m, 0x800, 0, None)              # separator
            u.AppendMenuW(m, 0, 2, "Quit")
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            u.SetForegroundWindow(hwnd)                   # so the menu closes when you click elsewhere
            cmd = u.TrackPopupMenu(m, 0x0100 | 0x0020 | 0x0008, pt.x, pt.y, 0, hwnd, None)   # RETURNCMD|BOTTOMALIGN|RIGHTALIGN
            u.PostMessageW(hwnd, 0, 0, 0)
            u.DestroyMenu(m)
            if cmd == 1:
                later(self.on_open)
            elif cmd == 2:
                later(self.on_quit)

        def proc(hwnd, msg, wp, lp):
            if msg == self.WM_TRAY:
                ev = lp & 0xFFFF
                if ev in (0x0400, 0x0401, 0x0405):        # NIN_SELECT, NIN_KEYSELECT, NIN_BALLOONUSERCLICK
                    later(self.on_open)
                elif ev == 0x007B:                        # WM_CONTEXTMENU (right-click)
                    menu(hwnd)
                return 0
            if msg == 0x0002:                             # WM_DESTROY
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)                        # keep a reference: Windows calls it later

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        try:
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="ProToolTrayWindow")
            u.RegisterClassW(ctypes.byref(wc))
            self.hwnd = u.CreateWindowExW(0, "ProToolTrayWindow", "Pro Tool tray", 0, 0, 0, 0, 0, None, None, hinst, None)
            self.notifier = WindowsNotifier(self.hwnd, icon_path, callback_msg=self.WM_TRAY)
            self.ok = bool(self.hwnd) and self.notifier.ok
        except Exception as e:
            print(f"Couldn't set up the tray icon: {e}")
        ready.set()
        if not self.hwnd:
            return
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def close(self):
        if self.notifier:
            self.notifier.close()
        if self.hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)   # WM_CLOSE -> ends its thread


def found_notification(notifier):
    """Hook for FOUND_HOOKS: notify about a found username, following the app's Notify switch.
    Windows plays its own sound only when the app's Sound switch is off, so you never hear two."""
    def hook(name):
        cfg = merged_settings()
        if cfg.get("notify", True):
            ok = notifier.notify("Username available", f"{name}  is free to claim", sound=not cfg.get("sound", True))
            print(f"Windows notification for {name}: {'sent' if ok else 'not sent'}")
    return hook


def token_notification(notifier):
    """Hook for TOKEN_HOOKS: a Windows notification when a token stops working (seen even if the app is hidden)."""
    def hook(label, tail, why):
        notifier.notify("Token not working", f"{label} (...{tail}): {why}", sound=True)
    return hook


def run_see_through_window(url):
    """The app window, see-through version (WebView2 composition control in a transparent WPF window; see
    wpf_window.py). Blocks until it closes. Returns False if it couldn't start, so the caller can fall back."""
    if not core.IS_WINDOWS:
        return False
    try:
        import webview
        import wpf_window
    except Exception as e:
        print(f"See-through window unavailable: {e}")
        return False
    webview_lib = os.path.join(os.path.dirname(webview.__file__), "lib")
    wpf_dll = os.path.join(getattr(sys, "_MEIPASS", HERE), "lib", "Microsoft.Web.WebView2.Wpf.dll")
    icon = os.path.join(WEB_DIR, "icons", "app.ico")

    def make_tray(on_open, on_quit):              # the tray icon + Windows notifications for found usernames
        tray = TrayIcon(icon, on_open=on_open, on_quit=on_quit)
        if not tray.ok:
            return None
        FOUND_HOOKS.append(found_notification(tray.notifier))
        TOKEN_HOOKS.append(token_notification(tray.notifier))
        return tray

    ok, why = wpf_window.run_window(url + "?app=1", "Pro Tool", icon, DATA_DIR, webview_lib, wpf_dll, make_tray)
    if not ok:
        print(f"See-through window didn't start ({why}); using the standard window.")
    return ok


def run_app_window(url):
    """Borderless pywebview window with the page drawing its own title bar. Blocks until the window
    closes. Returns False if pywebview isn't available (the caller then falls back to Edge)."""
    try:
        import webview
    except Exception:
        return False
    title = "Pro Tool"
    api = WindowApi()
    win = webview.create_window(title, url + "?app=1", js_api=api, width=1440, height=920, min_size=(980, 640),
                                frameless=True, easy_drag=False, background_color="#000000")
    api._win = win
    api._title = title

    def set_max(state):
        api._max = state
        try:
            win.evaluate_js(f"document.documentElement.classList.toggle('maxed', {str(state).lower()})")
        except Exception:
            pass

    notifier = {}

    def bring_back():                          # from the tray icon: show the window again, in front
        try:
            win.show()
            win.restore()
            import ctypes
            for hwnd in find_app_windows(title):
                ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            print(f"Couldn't bring the window back: {e}")

    def on_shown():
        round_window_corners(title)
        try:
            taskbar_minimize(api, title)       # clicking the taskbar button minimizes the app (with the genie)
        except Exception as e:
            print(f"Couldn't set up taskbar minimize: {e}")
        if core.IS_WINDOWS and "n" not in notifier:   # once: the tray icon + notifications for found usernames
            try:
                tray = TrayIcon(os.path.join(WEB_DIR, "icons", "app.ico"), on_open=bring_back, on_quit=win.destroy)
                if tray.ok:
                    notifier["n"] = tray
                    api._tray = tray
                    FOUND_HOOKS.append(found_notification(tray.notifier))
                    TOKEN_HOOKS.append(token_notification(tray.notifier))
                else:                          # fall back to the old way: notifications, no clicks
                    hwnds = find_app_windows(title)
                    if hwnds:
                        n = WindowsNotifier(hwnds[0], os.path.join(WEB_DIR, "icons", "app.ico"))
                        notifier["n"] = n
                        FOUND_HOOKS.append(found_notification(n))
                        TOKEN_HOOKS.append(token_notification(n))
            except Exception as e:
                print(f"Couldn't set up Windows notifications: {e}")

    win.events.shown += on_shown
    def back_from_minimized():                 # play the genie in reverse when it comes back from the taskbar
        if getattr(api, "_min", False):
            api._min = False
            try:
                win.evaluate_js("window.__genieIn && window.__genieIn()")
            except Exception:
                pass

    def on_minimized():
        api._min = True

    win.events.minimized += on_minimized
    win.events.maximized += lambda: (set_max(True), back_from_minimized())
    win.events.restored += lambda: (set_max(False), back_from_minimized())
    try:
        webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, "app-webview"))
    finally:
        if "n" in notifier:
            notifier["n"].close()              # remove the tray icon when the app closes
    return True


def open_app_window(url):
    """Open url as a standalone app window. Returns the window's process, or None if we had to
    fall back to a normal browser tab."""
    import subprocess
    for p in APP_BROWSERS:
        exe = os.path.expandvars(p)
        if os.path.isfile(exe):
            profile = os.path.join(DATA_DIR, "app-window")
            return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,920",
                                     "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"])
    webbrowser.open(url)
    return None


def main():
    global SECRETS, ACCESS_KEY
    frozen = getattr(sys, "frozen", False)
    if frozen and sys.stdout is None:          # windowed .exe: keep a log instead of a console
        try:
            os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
            sys.stdout = sys.stderr = open(os.path.join(HERE, "dist", "pro-tool.log"), "a", encoding="utf-8", buffering=1)
        except OSError:
            pass
    ap = argparse.ArgumentParser(description="Pro Tool - username scanner in your browser")
    ap.add_argument("--port", type=int, default=8766)   # v2: its own port, so v1 (8765) can run at the same time
    ap.add_argument("--lan", action="store_true", help="allow phones / other PCs on your network (needs the access link)")
    ap.add_argument("--data", help="folder for settings, results and dictionaries (default: dist/ next to this file)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    ap.add_argument("--app", action="store_true", help="open in its own app window; quit when it's closed (default for the .exe)")
    ap.add_argument("--headless", action="store_true", help="run the server only, no window (for testing)")
    ap.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    app_mode = (a.app or frozen) and not a.lan and not a.headless
    cleanup_old_exe()

    dist = os.path.join(HERE, "dist")
    set_data_dir(a.data or (dist if os.path.isdir(dist) else HERE))
    SECRETS = Secrets()

    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.after_update:                         # just updated: wait for the old copy to let go of the port
        for _ in range(40):
            try:
                ProServer((host, a.port), Handler).server_close()
                break
            except OSError:
                time.sleep(0.25)
    try:
        srv = ProServer((host, a.port), Handler)
    except OSError as e:
        if app_mode:                           # already running: just open another window onto it
            url = f"http://127.0.0.1:{a.port}/"
            try:
                if run_app_window(url):
                    return
            except Exception:
                pass
            open_app_window(url)
            return
        print(f"Couldn't start on port {a.port}: {e}\nIs Pro Tool v2 already running? Try --port 8767")
        sys.exit(1)
    finish_pending_reset()                     # first copy only, before its window opens
    if app_mode:
        srv.daemon_threads = True
        print(f"Pro Tool v2 app started {time.strftime('%Y-%m-%d %H:%M:%S')} - data folder: {DATA_DIR}")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{a.port}/"
        try:
            shown = False
            try:
                shown = run_app_window(url)   # standard window: draws at the monitor's full refresh rate (the see-through one capped at ~145 fps)
            except Exception as e:
                print(f"Borderless window failed ({e}); using an Edge app window instead.")
            if not shown:
                win = open_app_window(url)
                if win is not None:
                    win.wait()                 # the app runs until its window is closed
                else:
                    while True:                # no app-capable browser: keep serving the normal tab
                        time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            SCANNER.stop()
            srv.shutdown()
            srv.server_close()
        return
    srv.daemon_threads = True
    local = f"http://127.0.0.1:{a.port}/"
    print("Pro Tool  -  " + core.CREDIT)
    print(f"Data folder: {DATA_DIR}")
    if a.lan:
        ACCESS_KEY = load_settings().get("web_key") or secrets.token_urlsafe(24)
        patch_settings({"web_key": ACCESS_KEY})
        local += f"?key={ACCESS_KEY}"
        print(f"\nOpen on this PC:      {local}")
        print(f"Open on your phone:   http://{lan_ip()}:{a.port}/?key={ACCESS_KEY}")
        print("Keep this link private - anyone with it can use your token. Same Wi-Fi only.")
    else:
        print(f"\nOpen in your browser: {local}")
    print("Close this window (or press Ctrl+C) to stop.\n")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(local)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SCANNER.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
