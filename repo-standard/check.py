#!/usr/bin/env python3
"""Repo-standard check (STANDARD.md in ccmenshealth/ci-recipes). Run from a repo root, or pass --root.

Looks at tracked files only (git ls-files) and prints one tab-separated line per violation: rule, path, fix. Paths only, never file
content. Exit 0 clean, 1 violations, 2 error (not a git repo, bad .repo-standard.json, canonical guard missing).

usage: check.py [--root DIR] [--canonical PATH] [--public] [--print-default-image-paths]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
DEFAULT_CANONICAL = HERE.parent / "identifier-guard" / "identifier-guard.py"
CONFIG = ".repo-standard.json"

# Document files only (and extensionless ones): source code named after a feature (history.ts, handoff.py) is not narrative.
DOC_EXT = {"", ".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc", ".org", ".rtf", ".doc", ".docx", ".odt", ".pdf",
           ".html", ".htm"}
# "history" alone is a feature or clinical word (payment history, medical history); only a history of the work counts.
NARRATIVE_NAME = re.compile(r"hand-?off|ledger|current[-_ ]?state|(resume|startup)[-_ ]?prompt"
                            r"|(current[-_ ]?state|session|project|build|work|change|status|progress)[-_ ]?histor", re.I)
NARRATIVE_STEM = re.compile(r"progress|status|todos?|bookmarks?|histor(y|ies)", re.I)  # whole stem: status-page.md passes
NARRATIVE_DIRS = {"progress", "todo", "todos", "audit", "audits", "archive", "archived", "history", "histories",
                  "handoff", "handoffs", "ledger", "ledgers", "sessions", "status"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif", ".svg",
             ".ico", ".icns"}
# Matched case-insensitively. App and web icons include the Next.js app-router names (app/icon.*, app/apple-icon.*).
DEFAULT_IMAGE_PATHS = ["public/**", "**/public/**", "assets/**", "**/assets/**", "fixtures/golden/**",
                       "**/*.ico", "**/*.icns", "**/*.appiconset/**", "**/*.iconset/**", "**/*.xcassets/**",
                       "**/icons/**", "**/icon/**", "**/app/icon.*", "**/app/apple-icon.*"]
REQUIRED = ["README.md", "CLAUDE.md"]
PR_TEMPLATE = ".github/PULL_REQUEST_TEMPLATE.md"
CI_COST_LINE = re.compile(r"^\W*CI cost\b", re.I | re.M)
GUARD = "scripts/identifier-guard.py"
HOOK = ".githooks/pre-commit"
OWNER = "ccmenshealth"
PUBLIC_DEPS = {f"{OWNER}/ci-recipes"}
SECRET_REF = re.compile(r"\bsecrets\b(?!\.GITHUB_TOKEN\b)")
RUNS_ON = re.compile(r"^\s*runs-on:\s*(.*?)\s*(?:#.*)?$", re.M)
HOSTED_LABEL = re.compile(r"""\[?\s*['"]?(ubuntu|windows|macos)-[\w.]+['"]?\s*\]?""")
REPO_REF = re.compile(r"""^\s*(?:-\s*)?(?:uses|repository):\s*['"]?([\w.-]+/[\w.-]+)""", re.M)
# image_paths entries that would allow a screenshot at the top level or under docs/ are refused.
BROAD_PROBES = ["probe.png", "docs/probe.png", "docs/screenshots/probe.png", "some/dir/probe.png", "some/img/probe.png"]
KEYS = {"version": int, "public": bool, "image_paths": list, "allow": list}


class Error(Exception):
    pass


def glob_regex(pattern):
    """`**/` = zero or more directories, `**` = anything, `*` = within one path segment, `?` = one character."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile("".join(out) + r"\Z", re.I)


def tracked_files(root):
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise Error(f"{root} is not a git repository (or git failed): {e}") from e
    return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def load_config(root, files):
    path = root / CONFIG
    if not path.is_file():
        return {}
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Error(f"{CONFIG}: not valid JSON ({e})") from e
    if not isinstance(cfg, dict):
        raise Error(f"{CONFIG}: must be a JSON object")
    for key, value in cfg.items():
        if key not in KEYS:
            raise Error(f"{CONFIG}: unknown key {key!r} (allowed: {', '.join(KEYS)})")
        if type(value) is not KEYS[key]:
            raise Error(f"{CONFIG}: {key!r} must be {KEYS[key].__name__}")
        if KEYS[key] is list and not all(isinstance(v, str) for v in value):
            raise Error(f"{CONFIG}: {key!r} must be a list of strings")
    if cfg.get("version", 1) != 1:
        raise Error(f"{CONFIG}: version must be 1")
    for pat in cfg.get("image_paths", []):
        if pat.lower().startswith("docs/"):
            raise Error(f"{CONFIG}: image_paths entry {pat!r}: images under docs/ are never allowed")
        if any(glob_regex(pat).match(probe) for probe in BROAD_PROBES):
            raise Error(f"{CONFIG}: image_paths entry {pat!r} is too broad; name the asset directory")
    tracked = set(files)
    for p in cfg.get("allow", []):
        if p not in tracked:
            raise Error(f"{CONFIG}: allow entry {p!r} is not a tracked file; remove it")
    return cfg


def check(root, canonical, public):
    files = tracked_files(root)
    cfg = load_config(root, files)
    public = public or cfg.get("public", False)
    allow = set(cfg.get("allow", []))
    image_ok = [glob_regex(p) for p in cfg.get("image_paths", DEFAULT_IMAGE_PATHS)]
    canonical_hook = canonical.parent / "pre-commit"
    for c in (canonical, canonical_hook):
        if not c.is_file():
            raise Error(f"canonical file not found at {c}")
    v = []

    for f in files:
        p = PurePosixPath(f)
        ext = p.suffix.lower()
        if f not in allow:
            if ext in DOC_EXT and (NARRATIVE_NAME.search(p.name) or NARRATIVE_STEM.fullmatch(p.stem)):
                v.append(("narrative-name", f, "narrative goes to the memory system; delete, or rename if it is "
                          f"not narrative, or add the path to \"allow\" in {CONFIG}"))
            elif p.parts[0] == "docs" and NARRATIVE_DIRS & {s.lower() for s in p.parts[1:-1]}:
                v.append(("narrative-dir", f, "move it to the fixed doc set (docs/runbooks/, docs/decisions.md) "
                          "or into the memory system"))
        # docs/ is prose; an image there is a capture of something, whatever directory it sits in.
        if ext in IMAGE_EXT and (p.parts[0] == "docs" or not any(r.match(f) for r in image_ok)):
            v.append(("image-path", f, "images only in code-referenced asset paths; delete it, move it, or add "
                      f"its directory to \"image_paths\" in {CONFIG}"))

    tracked = set(files)
    for f in REQUIRED:
        if f not in tracked:
            v.append(("required-file", f, "add it (template/ in ci-recipes has a skeleton)"))
    if PR_TEMPLATE not in tracked:
        v.append(("pr-template", PR_TEMPLATE, "copy template/.github/PULL_REQUEST_TEMPLATE.md from ci-recipes"))
    elif not CI_COST_LINE.search((root / PR_TEMPLATE).read_text(errors="replace")):
        v.append(("pr-template", PR_TEMPLATE, "add a line starting \"CI cost:\" (see template/ in ci-recipes)"))
    if GUARD not in tracked:
        v.append(("guard", GUARD, "missing; run ./sync.sh --only <repo> from a ci-recipes clone"))
    elif (root / GUARD).read_bytes() != canonical.read_bytes():
        v.append(("guard", GUARD, "differs from the canonical copy; run ./sync.sh --only <repo>, never edit it here"))
    if HOOK not in tracked:
        v.append(("hook", HOOK, "missing; run ./sync.sh --only <repo>"))
    elif (root / HOOK).read_bytes() != canonical_hook.read_bytes():
        v.append(("hook", HOOK, "differs from identifier-guard/pre-commit; run ./sync.sh --only <repo>"))

    if public:
        for f in files:
            if not (f.startswith(".github/workflows/") or f.startswith(".github/actions/")):
                continue
            if PurePosixPath(f).suffix not in {".yml", ".yaml"}:
                continue
            text = (root / f).read_text(errors="replace")
            labels = [m.group(1) for m in RUNS_ON.finditer(text)]
            deps = [m.group(1) for m in REPO_REF.finditer(text) if m.group(1).lower().startswith(OWNER + "/")]
            if SECRET_REF.search(text):
                v.append(("public-repo", f, "a public repo uses no secret other than GITHUB_TOKEN"))
            if "self-hosted" in text or any(not HOSTED_LABEL.fullmatch(lb) for lb in labels):
                v.append(("public-repo", f, "a public repo runs only on literal GitHub-hosted labels (ubuntu-*)"))
            if any(d not in PUBLIC_DEPS for d in deps):
                v.append(("public-repo", f, f"a public repo depends on no private repo (allowed: {', '.join(PUBLIC_DEPS)})"))
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repo root (default: current directory)")
    ap.add_argument("--canonical", default=str(DEFAULT_CANONICAL), help="canonical identifier-guard.py")
    ap.add_argument("--public", action="store_true", help="apply the public-repo rule even without the config key")
    ap.add_argument("--print-default-image-paths", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.print_default_image_paths:
        print(json.dumps(DEFAULT_IMAGE_PATHS))
        return 0
    try:
        violations = check(Path(a.root), Path(a.canonical), a.public)
    except Error as e:
        print(f"repo-standard: error: {e}", file=sys.stderr)
        return 2
    for rule, path, fix in violations:
        print(f"{rule}\t{path}\t{fix}")
    if violations:
        print(f"repo-standard: {len(violations)} violation(s); STANDARD.md in ccmenshealth/ci-recipes", file=sys.stderr)
        return 1
    print("repo-standard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
