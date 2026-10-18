#!/usr/bin/env python3
"""Tests for lib/apply.sh (stdlib unittest; python3 -m unittest discover -s lib/tests).

Callers pin an exact ci-recipes release tag (v1.N); there is no moving tag. These tests drive the shell functions
through bash with RECIPES pointing at this clone, or at a throwaway git repo when a remote is needed.
APPLY=/path/to/apply.sh runs them against another copy (used to show them failing against the pre-change file).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APPLY = os.environ.get("APPLY") or os.path.join(ROOT, "lib", "apply.sh")
USES = "uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@"


def bash(script, recipes=ROOT, env=None):
    e = dict(os.environ, RECIPES=recipes, **(env or {}))
    return subprocess.run(["bash", "-c", '. "$APPLY_SH"; ' + script], env=dict(e, APPLY_SH=APPLY),
                          capture_output=True, text=True)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


def uses_line(path):
    return [l.split("#")[0].strip() for l in open(path) if "ci-recipes/.github/workflows/identifier-guard.yml@" in l]


class CallerPinsTheTag(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td)

    def test_plain_caller_pins_the_tag(self):
        r = bash('apply_guard "%s"' % self.td, env={"PIN_TAG": "v1.7"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(uses_line(os.path.join(self.td, ".github/workflows/identifier-guard.yml")), [USES + "v1.7"])

    def test_standard_caller_pins_the_tag_and_keeps_standard_true(self):
        open(os.path.join(self.td, ".repo-standard.json"), "w").write("{}\n")
        r = bash('apply_guard "%s"' % self.td, env={"PIN_TAG": "v1.12"})
        self.assertEqual(r.returncode, 0, r.stderr)
        caller = os.path.join(self.td, ".github/workflows/identifier-guard.yml")
        self.assertEqual(uses_line(caller), [USES + "v1.12"])
        self.assertIn("standard: true", open(caller).read())

    def test_only_the_uses_line_differs_from_the_source(self):
        bash('apply_guard "%s"' % self.td, env={"PIN_TAG": "v1.7"})
        got = open(os.path.join(self.td, ".github/workflows/identifier-guard.yml")).read().splitlines()
        src = open(os.path.join(ROOT, "identifier-guard/caller-workflow.yml")).read().splitlines()
        self.assertEqual(len(got), len(src))
        self.assertEqual([i for i, (a, b) in enumerate(zip(got, src)) if a != b], [i for i, l in enumerate(src) if USES in l])

    def test_no_pin_tag_is_an_error(self):
        r = bash('apply_guard "%s"' % self.td, env={"PIN_TAG": ""})
        self.assertNotEqual(r.returncode, 0)

    def test_in_sync_follows_the_tag(self):
        bash('apply_guard "%s"' % self.td, env={"PIN_TAG": "v1.7"})
        self.assertEqual(bash('in_sync "%s" 0' % self.td, env={"PIN_TAG": "v1.7"}).returncode, 0)
        self.assertNotEqual(bash('in_sync "%s" 0' % self.td, env={"PIN_TAG": "v1.8"}).returncode, 0)


class Tags(unittest.TestCase):
    """latest_release_tag reads origin; check_pin_tag refuses a tag whose guard is not this clone's."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td)
        src = os.path.join(self.td, "src")
        os.makedirs(os.path.join(src, "identifier-guard"))
        for f in ("identifier-guard.py", "pre-commit"):
            shutil.copy(os.path.join(ROOT, "identifier-guard", f), os.path.join(src, "identifier-guard", f))
        git(src, "init", "-q", "-b", "main")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "c1")
        for t in ("v1", "v1.2", "v1.9", "v1.10", "v2-rc", "v1.3-test"):
            git(src, "tag", t)
        self.bare = os.path.join(self.td, "bare.git")
        git(self.td, "clone", "-q", "--bare", src, self.bare)
        self.clone = os.path.join(self.td, "clone")
        git(self.td, "clone", "-q", self.bare, self.clone)

    def test_latest_release_tag_is_the_highest_v1_minor(self):
        r = bash("latest_release_tag origin", recipes=self.clone)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "v1.10")

    def test_check_pin_tag_accepts_a_tag_with_this_clones_guard(self):
        r = bash("check_pin_tag v1.10", recipes=self.clone)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_check_pin_tag_refuses_a_tag_whose_guard_differs(self):
        with open(os.path.join(self.clone, "identifier-guard", "identifier-guard.py"), "a") as f:
            f.write("# changed\n")
        r = bash("check_pin_tag v1.10", recipes=self.clone)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("v1.10", r.stderr)

    def test_check_pin_tag_refuses_a_missing_or_moving_tag(self):
        for tag in ("v1.99", "v1", "main"):
            self.assertNotEqual(bash("check_pin_tag %s" % tag, recipes=self.clone).returncode, 0, tag)


if __name__ == "__main__":
    unittest.main()
