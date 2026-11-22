"""Publish a Pro Tool update.

    python release.py --make-key                 (once) create your private signing key
    python release.py 2.2.0 "What changed"       build, sign and publish version 2.2.0 (and add it to CHANGELOG.md)
    python release.py 2.2.0                      same, using the v2.2.0 entry you already wrote in CHANGELOG.md
    python release.py 2.2.0 "What changed" --dry-run    everything except uploading

Everyone running a copy with the update system gets an "Update now / Later" prompt the next time they
open it. Updates are signed with your private key (kept OUTSIDE this folder, see KEY_FILE); copies of
the app only install updates carrying that signature. Back the key up: without it you can't publish
updates (people would have to download a new copy by hand). Never share it.
"""
import argparse, base64, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
APP = os.path.join(SRC, "dsv_web.py")
KEY_DIR = os.path.join(os.path.expanduser("~"), ".protool-release")
KEY_FILE = os.path.join(KEY_DIR, "signing_key.pem")
SHARE_ZIP = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Pro Tool v2.zip")
CHANGELOG = os.path.join(HERE, "CHANGELOG.md")


def changelog_notes(version):
    """This version's entry in CHANGELOG.md, if you wrote one."""
    try:
        text = open(CHANGELOG, encoding="utf-8").read()
    except OSError:
        return ""
    m = re.search(r"^## v" + re.escape(version) + r"\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def add_to_changelog(version, notes):
    """Put this version at the top of CHANGELOG.md (skipped if it's already there)."""
    if changelog_notes(version):
        return
    import datetime
    lines = [l.strip() for l in notes.splitlines() if l.strip()] or [f"Pro Tool v{version}"]
    entry = f"## v{version} - {datetime.date.today():%Y-%m-%d}\n"
    entry += "".join((l if l.startswith("- ") else "- " + l) + "\n" for l in lines) + "\n"
    try:
        text = open(CHANGELOG, encoding="utf-8").read()
    except OSError:
        text = "# Changelog\n\n"
    i = text.find("\n## ")
    text = text[:i + 1] + entry + text[i + 1:] if i >= 0 else text.rstrip() + "\n\n" + entry
    open(CHANGELOG, "w", encoding="utf-8").write(text)


def save_to_github(version):
    """Commit the new version number and changelog, and push them."""
    for cmd in (["git", "add", "src/dsv_web.py", "CHANGELOG.md"], ["git", "commit", "-q", "-m", f"Version {version}"],
                ["git", "push", "-q", "origin", "main"]):
        if subprocess.run(cmd, cwd=HERE).returncode != 0:
            print("Couldn't save the version change to GitHub automatically - commit and push it yourself.")
            return


def app_text():
    return open(APP, encoding="utf-8").read()


def app_const(name):
    m = re.search(rf'^{name} = "([^"]*)"', app_text(), re.M)
    return m.group(1) if m else ""


def set_app_const(name, value):
    s = app_text()
    s2, n = re.subn(rf'^{name} = "[^"]*"', f'{name} = "{value}"', s, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"Couldn't find {name} in dsv_web.py")
    open(APP, "w", encoding="utf-8").write(s2)


def vt(v):
    return tuple(int(x) for x in v.split("."))


def gh():
    for c in ("gh", r"C:\Program Files\GitHub CLI\gh.exe"):
        if shutil.which(c) or os.path.isfile(c):
            return c
    sys.exit("GitHub CLI (gh) not found. Install it: winget install --id GitHub.cli")


def make_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    if os.path.exists(KEY_FILE):
        sys.exit(f"A signing key already exists: {KEY_FILE}\n(Making a new one would stop existing copies from updating.)")
    os.makedirs(KEY_DIR, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    set_app_const("UPDATE_PUBKEY", base64.b64encode(pub).decode())
    print(f"Signing key saved to {KEY_FILE}\nBack it up somewhere safe and never share it.")
    print("Its public half is now built into dsv_web.py (UPDATE_PUBKEY).")


def load_key():
    from cryptography.hazmat.primitives import serialization
    if not os.path.exists(KEY_FILE):
        sys.exit(f"No signing key at {KEY_FILE}. Run: python release.py --make-key")
    return serialization.load_pem_private_key(open(KEY_FILE, "rb").read(), password=None)


def build(out_dir):
    import webview
    work = tempfile.mkdtemp(prefix="protool-build-")
    # pywebview's files without its __pycache__ (compiled files there record paths on this PC, e.g. your user name)
    wv = os.path.join(work, "webview")
    shutil.copytree(os.path.dirname(webview.__file__), wv, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "Pro Tool v2",
           "--icon", os.path.join(SRC, "web", "icons", "app.ico"),
           "--add-data", f"{os.path.join(SRC, 'web')};web",
           "--add-data", f"{wv};webview", "--collect-all", "webview", "--copy-metadata", "pywebview",
           "--hidden-import", "PIL.ImageGrab",
           "--hidden-import", "cryptography.hazmat.primitives.asymmetric.ed25519",
           "--distpath", out_dir, "--workpath", os.path.join(work, "work"), "--specpath", os.path.join(work, "spec"), APP]
    print("Building the exe (about a minute)...")
    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    shutil.rmtree(work, ignore_errors=True)
    exe = os.path.join(out_dir, "Pro Tool v2.exe")
    if r.returncode != 0 or not os.path.isfile(exe):
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit("Build failed.")
    return exe


def private_strings(exe):
    """Personal details that must never ship inside the exe: this PC's user name and home folder."""
    home = os.path.expanduser("~")
    words = {os.path.basename(home), home, home.replace("\\", "/"), os.environ.get("USERNAME", "")}
    data, found = open(exe, "rb").read(), []
    from PyInstaller.archive.readers import CArchiveReader
    blobs = [data]
    try:
        arc = CArchiveReader(exe)
        for name in arc.toc:
            try:
                d = arc.extract(name)
                if isinstance(d, (bytes, bytearray)):
                    blobs.append(bytes(d))
            except Exception:
                pass
    except Exception:
        pass
    for w in filter(None, words):
        for enc in ("utf-8", "utf-16-le"):
            if any(w.encode(enc) in b for b in blobs):
                found.append(repr(w)); break
    return sorted(set(found))


def sign(exe, version, notes, repo, asset):
    data = open(exe, "rb").read()
    m = {"version": version, "notes": notes, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
         "url": f"https://github.com/{repo}/releases/download/v{version}/{asset}"}
    msg = f"pro-tool-update\n{m['version']}\n{m['sha256']}\n{int(m['size'])}".encode()   # same as the app checks
    m["sig"] = base64.b64encode(load_key().sign(msg)).decode()
    return m


def main():
    ap = argparse.ArgumentParser(description="Build, sign and publish a Pro Tool update.")
    ap.add_argument("version", nargs="?", help="new version, e.g. 2.2.0")
    ap.add_argument("notes", nargs="?", default="", help="what changed (shown in the update prompt)")
    ap.add_argument("--make-key", action="store_true", help="create your signing key (once)")
    ap.add_argument("--dry-run", action="store_true", help="build and sign, but don't upload")
    ap.add_argument("--out", help="folder for the built files (default: a temp folder)")
    a = ap.parse_args()
    if a.make_key:
        return make_key()
    if not a.version or not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        sys.exit('Give a version like 2.2.0, e.g.: python release.py 2.2.0 "Faster scans"')
    if not app_const("UPDATE_PUBKEY"):
        sys.exit("No signing key yet. Run: python release.py --make-key")
    load_key()
    current, repo, asset = app_const("APP_VERSION"), app_const("UPDATE_REPO"), app_const("UPDATE_ASSET")
    if vt(a.version) <= vt(current) and not a.dry_run:
        sys.exit(f"The new version must be higher than the current one ({current}).")
    if not a.dry_run:
        g = gh()
        if subprocess.run([g, "release", "view", f"v{a.version}", "--repo", repo], capture_output=True).returncode == 0:
            sys.exit(f"v{a.version} is already published.")
    set_app_const("APP_VERSION", a.version)
    out = a.out or tempfile.mkdtemp(prefix="protool-release-")
    os.makedirs(out, exist_ok=True)
    try:
        exe = build(out)
    except SystemExit:
        set_app_const("APP_VERSION", current)          # build failed: leave the version as it was
        raise
    up = os.path.join(out, asset)
    shutil.copyfile(exe, up)
    leak = private_strings(up)                                       # checked before anything is copied anywhere
    if leak:
        set_app_const("APP_VERSION", current)
        sys.exit(f"Stopped: the build contains {', '.join(leak)} (personal info). Nothing was published or replaced.")
    notes = a.notes or changelog_notes(a.version)
    manifest = sign(up, a.version, notes, repo, asset)
    mf = os.path.join(out, "latest.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    if a.dry_run:                                                    # a test build changes nothing here
        set_app_const("APP_VERSION", current)
        print(f"Built and signed v{a.version} (dry run - nothing uploaded, nothing replaced). Files are in {out}")
        return
    shutil.copyfile(exe, os.path.join(HERE, "Pro Tool v2.exe"))     # the project's own copy
    if os.path.isfile(SHARE_ZIP):                                    # the copy you hand out
        try:
            readme = zipfile.ZipFile(SHARE_ZIP).read("Pro Tool v2/README.txt")
        except Exception:
            readme = b""
        with zipfile.ZipFile(SHARE_ZIP + ".new", "w", zipfile.ZIP_DEFLATED) as z:
            z.write(exe, "Pro Tool v2/Pro Tool v2.exe")
            if readme:
                z.writestr("Pro Tool v2/README.txt", readme)
        os.replace(SHARE_ZIP + ".new", SHARE_ZIP)
    print(f"Built and signed v{a.version}: {up}")
    print(f"Publishing to github.com/{repo} ...")
    r = subprocess.run([gh(), "release", "create", f"v{a.version}", up, mf, "--repo", repo,
                        "--title", f"Pro Tool v{a.version}", "--notes",
                        (notes or f"Pro Tool v{a.version}")
                        + f"\n\n**Download:** `{asset}` below - or just open Pro Tool and choose **Update now**."
                        + f"\n\n[Full changelog](https://github.com/{repo}/blob/main/CHANGELOG.md)"])
    if r.returncode != 0:
        sys.exit("Upload failed (see above). The build is still in " + out)
    add_to_changelog(a.version, notes)
    save_to_github(a.version)
    print(f"Published v{a.version}. Every copy will offer the update the next time it opens.")


if __name__ == "__main__":
    main()
