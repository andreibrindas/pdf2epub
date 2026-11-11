"""Validation and regression: epubcheck, and per-file fingerprints of a build."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile

MODIFIED = re.compile(rb'<meta property="dcterms:modified">[^<]*</meta>')


def epubcheck(epub_path):
    """Run epubcheck if installed. -> (ok or None if unavailable, output)"""
    exe = shutil.which("epubcheck")
    if not exe:
        return None, "epubcheck not found (macOS: brew install epubcheck)"
    p = subprocess.run([exe, epub_path], capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out


def fingerprint(epub_path):
    """sha256 of every entry in the .epub, ignoring the build timestamp in content.opf."""
    fp = {}
    with zipfile.ZipFile(epub_path) as z:
        for name in z.namelist():
            data = z.read(name)
            if name.endswith("content.opf"):
                data = MODIFIED.sub(b"", data)
            fp[name] = hashlib.sha256(data).hexdigest()
    return dict(sorted(fp.items()))


def compare(old, new):
    """-> list of human-readable differences between two fingerprints."""
    diffs = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            diffs.append(f"removed  {k}")
        elif k not in old:
            diffs.append(f"added    {k}")
        elif old[k] != new[k]:
            diffs.append(f"changed  {k}")
    return diffs


def load_fingerprint(book_dir):
    p = os.path.join(book_dir, "fingerprint.json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return json.load(f)


def save_fingerprint(book_dir, fp):
    with open(os.path.join(book_dir, "fingerprint.json"), "w") as f:
        json.dump(fp, f, indent=1)
        f.write("\n")
