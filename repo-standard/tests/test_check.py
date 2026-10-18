"""Tests for repo-standard/check.py. Each test builds a synthetic git repo in a temp dir and runs the checker on it.

python3 -m unittest discover -s repo-standard/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPES = HERE.parent.parent
CHECK = RECIPES / "repo-standard" / "check.py"
GUARD = RECIPES / "identifier-guard" / "identifier-guard.py"
HOOK = RECIPES / "identifier-guard" / "pre-commit"
PLAIN_CALLER = RECIPES / "identifier-guard" / "caller-workflow.yml"
TEMPLATE = RECIPES / "template"

PR_TEMPLATE = "## Summary\n\n## CI cost\n\n- CI cost: none\n"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class Repo:
    """A conforming repo; tests add, change or remove files and then run the checker."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        self.write("README.md", "# demo\n")
        self.write("CLAUDE.md", "rules\n")
        self.write(".github/PULL_REQUEST_TEMPLATE.md", PR_TEMPLATE)
        self.copy(GUARD, "scripts/identifier-guard.py")
        self.copy(HOOK, ".githooks/pre-commit")

    def write(self, rel, text="x\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return self

    def copy(self, src, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)
        return self

    def remove(self, rel):
        (self.root / rel).unlink()
        return self

    def config(self, obj):
        return self.write(".repo-standard.json", json.dumps(obj) if not isinstance(obj, str) else obj)

    def run(self, *extra):
        git(self.root, "add", "-A")
        r = subprocess.run([sys.executable, str(CHECK), "--root", str(self.root), *extra],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        self.tmp.cleanup()


class Base(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)

    def assertClean(self, *extra):
        code, out = self.repo.run(*extra)
        self.assertEqual(code, 0, out)

    def assertViolation(self, rule, path, *extra):
        code, out = self.repo.run(*extra)
        self.assertEqual(code, 1, out)
        hits = [ln for ln in out.splitlines() if ln.split("\t")[:2] == [rule, path]]
        self.assertTrue(hits, f"expected {rule} {path} in:\n{out}")
        return out

    def assertError(self, *extra):
        code, out = self.repo.run(*extra)
        self.assertEqual(code, 2, out)
        return out


class Baseline(Base):
    def test_conforming_repo_is_clean(self):
        self.assertClean()

    def test_untracked_files_are_ignored(self):
        # run() stages everything git would add, so an ignored file stays untracked
        self.repo.write(".gitignore", "local/\n").write("local/history.md").write("local/shot.png")
        self.assertClean()

    def test_not_a_git_repo_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(CHECK), "--root", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class NarrativeNames(Base):
    def test_flags_narrative_document_names(self):
        for rel in ["docs/current-state-history.md", "HANDOFF.md", "docs/plans/x-ledger.md", "RESUME_PROMPT.md",
                    "STARTUP-PROMPT.md", "docs/current-state.md", "notes/hand-off-notes.txt", "docs/History.rst",
                    "docs/startup_prompt.md", "docs/resume prompt.docx", "current_state.md", "docs/currentstate.md",
                    "HANDOFF.html", "HANDOFF", "progress.md", "docs/STATUS.md", "bookmark.md", "TODO.md",
                    "docs/todos.txt"]:
            with self.subTest(rel=rel):
                repo = Repo()
                self.addCleanup(repo.close)
                repo.write(rel)
                self.repo = repo
                self.assertViolation("narrative-name", rel)

    def test_source_files_with_those_words_pass(self):
        for rel in ["src/lib/historicalWidgets.ts", "src/widgetHandoff.ts", "src/widgets.ledgerReaders.test.ts",
                    "RELEASE-HANDOFF.sha256", "src/history.py", "docs/runbooks/status-page.md",
                    "docs/progress-bar-design.md", "src/progress.tsx"]:
            self.repo.write(rel)
        self.assertClean()

    def test_allow_entry_exempts_one_exact_path(self):
        self.repo.write("docs/plans/widget-ledger.md").write("docs/plans/other-ledger.md")
        self.repo.config({"allow": ["docs/plans/widget-ledger.md"]})
        out = self.assertViolation("narrative-name", "docs/plans/other-ledger.md")
        self.assertNotIn("widget-ledger", out)

    def test_history_of_the_work_is_narrative(self):
        for rel in ["HISTORY.md", "docs/session-history.md", "docs/project_history.txt", "docs/change-history.md",
                    "docs/build-history.md", "docs/histories.md"]:
            with self.subTest(rel=rel):
                repo = Repo(); self.addCleanup(repo.close); self.repo = repo
                repo.write(rel)
                self.assertViolation("narrative-name", rel)

    def test_history_as_a_feature_or_clinical_term_passes(self):
        for rel in ["docs/plans/appointment-history.md", "docs/plans/F_2026-01-01_chart-history-tab.plan.md",
                    "docs/plans/widget-payments-history.md", "forms/intake page 2 medical history.docx",
                    "forms/Adult Health History form.pdf"]:
            self.repo.write(rel)
        self.repo.config({"asset_paths": ["forms/**"]})
        self.assertClean()


class NarrativeDirs(Base):
    def test_flags_files_under_narrative_docs_dirs(self):
        for rel in ["docs/progress/week1.md", "docs/todos/eslint.md", "docs/audits/a.md", "docs/archive/old.png",
                    "docs/plans/archived/p.md", "docs/sessions/s.md", "docs/handoffs/h.json"]:
            with self.subTest(rel=rel):
                repo = Repo()
                self.addCleanup(repo.close)
                repo.write(rel)
                self.repo = repo
                code, out = repo.run()
                self.assertEqual(code, 1, out)
                self.assertIn(f"narrative-dir {rel}", " ".join(out.split()))

    def test_root_archive_handoff_history_dirs_are_flagged(self):
        for rel in ["archive/old-flow.json", "archived/x.md", "handoff/h.txt", "handoffs/a.md", "history/h.md",
                    "histories/x.json", "Archive/x.bin"]:
            with self.subTest(rel=rel):
                repo = Repo(); self.addCleanup(repo.close); self.repo = repo
                repo.write(rel)
                code, out = repo.run()
                self.assertEqual(code, 1, out)
                self.assertIn(f"narrative-dir {rel}", " ".join(out.split()))

    def test_root_dir_allow_entry(self):
        self.repo.write("archive/a.json").write("archive/b.json")
        self.repo.config({"allow": ["archive/a.json"]})
        out = self.assertViolation("narrative-dir", "archive/b.json")
        self.assertNotIn("archive/a.json", out)

    def test_root_status_and_todo_dirs_pass(self):
        self.repo.write("status/page.tsx").write("todo/list.ts").write("progress/bar.ts")
        self.assertClean()

    def test_same_names_outside_docs_pass(self):
        self.repo.write("src/app/audit/page.tsx").write("src/archive/index.ts").write("tests/progress/a.test.ts")
        self.assertClean()

    def test_runbooks_and_plans_pass(self):
        self.repo.write("docs/runbooks/deploy.md").write("docs/plans/feature.plan.md").write("docs/architecture.md")
        self.repo.write("docs/decisions.md")
        self.assertClean()


class Images(Base):
    def test_screenshot_outside_asset_paths_is_flagged(self):
        self.repo.write("docs/screenshot.png")
        self.assertViolation("binary-path", "docs/screenshot.png")

    def test_every_image_extension_is_checked(self):
        for ext in ["png", "JPG", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif", "avif", "svg"]:
            self.repo.write(f"shots/a.{ext}")
        code, out = self.repo.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(sum(1 for ln in out.splitlines() if ln.startswith("binary-path")), 12, out)

    def test_default_allowed_paths(self):
        for rel in ["public/logo.svg", "webapp/public/og.png", "assets/a.png", "src/ui/assets/b.png",
                    "fixtures/golden/page-1.synthetic.png", "src/app/favicon.ico", "Mac/App.icns",
                    "App/Assets.xcassets/AppIcon.appiconset/icon-1024.png", "static/icons/menu.svg",
                    "webapp/app/icon.png", "webapp/src/app/icon.svg", "src/app/apple-icon.png",
                    "Icons/AppIcon.iconset/icon_16x16@2x.png", "Icons/Sources/MenuBar.svg", "icon/icon-1024.png",
                    "Resources/Assets/x.png"]:
            self.repo.write(rel)
        self.assertClean()

    def test_config_image_paths_replace_the_default(self):
        self.repo.write("public/logo.svg").write("media/x.png")
        self.repo.config({"asset_paths": ["media/**"]})
        out = self.assertViolation("binary-path", "public/logo.svg")
        self.assertNotIn("media/x.png", out)

    def test_over_broad_image_paths_are_an_error(self):
        for pat in ["**", "**/*", "*", "docs/**", "**/*.png", "**/docs/**"]:
            with self.subTest(pat=pat):
                self.repo.config({"asset_paths": [pat]})
                self.assertError()

    def test_binary_documents_follow_the_asset_rule(self):
        for ext in ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "ods", "odp"]:
            with self.subTest(ext=ext):
                repo = Repo(); self.addCleanup(repo.close); self.repo = repo
                repo.write(f"forms/blank.{ext}")
                self.assertViolation("binary-path", f"forms/blank.{ext}")

    def test_binary_documents_pass_in_asset_paths(self):
        self.repo.write("fixtures/golden/page.synthetic.pdf").write("fixtures/golden/a.synthetic.xlsx")
        self.repo.write("fixtures/synthetic/b.synthetic.xlsx").write("sbsc/form.pdf").write("public/terms.pdf")
        self.repo.config({"asset_paths": ["public/**", "fixtures/golden/**", "fixtures/synthetic/**", "sbsc/**"]})
        self.assertClean()

    def test_default_allows_synthetic_fixture_documents(self):
        self.repo.write("fixtures/synthetic/b.synthetic.xlsx")
        self.assertClean()

    def test_binary_document_under_docs_is_flagged(self):
        self.repo.write("docs/assets/export.pdf")
        self.assertViolation("binary-path", "docs/assets/export.pdf")

    def test_old_image_paths_key_is_an_error_naming_the_new_one(self):
        self.repo.config('{"image_paths": ["public/**"]}')
        out = self.assertError()
        self.assertIn("asset_paths", out)

    def test_images_under_docs_are_never_allowed(self):
        for rel in ["docs/assets/screen.png", "docs/public/s.png", "docs/icons/s.png", "docs/x/app/icon.png"]:
            with self.subTest(rel=rel):
                repo = Repo(); self.addCleanup(repo.close); self.repo = repo
                repo.write(rel)
                self.assertViolation("binary-path", rel)

    def test_image_paths_entry_under_docs_is_an_error(self):
        for pat in ["docs/assets/**", "**/img/**"]:
            with self.subTest(pat=pat):
                self.repo.config({"asset_paths": [pat]})
                self.assertError()

    def test_fixtures_outside_golden_need_a_config_entry(self):
        self.repo.write("tests/fixtures/run-a/frame.png")
        self.assertViolation("binary-path", "tests/fixtures/run-a/frame.png")

    def test_glob_star_stays_in_one_segment(self):
        self.repo.write("media/deep/x.png")
        self.repo.config({"asset_paths": ["media/*"]})
        self.assertViolation("binary-path", "media/deep/x.png")


class RequiredFiles(Base):
    def test_missing_readme(self):
        self.repo.remove("README.md")
        self.assertViolation("required-file", "README.md")

    def test_missing_claude_md(self):
        self.repo.remove("CLAUDE.md").write("AGENTS.md")
        self.assertViolation("required-file", "CLAUDE.md")

    def test_missing_pr_template(self):
        self.repo.remove(".github/PULL_REQUEST_TEMPLATE.md")
        self.assertViolation("pr-template", ".github/PULL_REQUEST_TEMPLATE.md")

    def test_pr_template_without_ci_cost_line(self):
        self.repo.write(".github/PULL_REQUEST_TEMPLATE.md", "## Summary\nCosts of CI are discussed elsewhere.\n")
        self.assertViolation("pr-template", ".github/PULL_REQUEST_TEMPLATE.md")

    def test_pr_template_ci_cost_line_variants(self):
        for line in ["CI cost: none", "- **CI cost:** +$0", "## CI cost", "ci cost - none"]:
            with self.subTest(line=line):
                self.repo.write(".github/PULL_REQUEST_TEMPLATE.md", f"## Summary\n{line}\n")
                self.assertClean()


class Guard(Base):
    def test_missing_guard(self):
        self.repo.remove("scripts/identifier-guard.py")
        self.assertViolation("guard", "scripts/identifier-guard.py")

    def test_drifted_guard(self):
        p = self.repo.root / "scripts/identifier-guard.py"
        p.write_bytes(p.read_bytes() + b"\n# local edit\n")
        self.assertViolation("guard", "scripts/identifier-guard.py")

    def test_canonical_override(self):
        with tempfile.TemporaryDirectory() as d:
            alt = Path(d) / "g.py"
            alt.write_text("print('other')\n")
            shutil.copyfile(HOOK, Path(d) / "pre-commit")
            self.assertViolation("guard", "scripts/identifier-guard.py", "--canonical", str(alt))
            self.repo.copy(alt, "scripts/identifier-guard.py")
            self.assertClean("--canonical", str(alt))

    def test_missing_canonical_is_an_error(self):
        self.assertError("--canonical", str(self.repo.root / "nope.py"))

    def test_missing_hook(self):
        self.repo.remove(".githooks/pre-commit")
        self.assertViolation("hook", ".githooks/pre-commit")

    def test_hook_that_does_not_run_the_guard(self):
        self.repo.write(".githooks/pre-commit", "#!/bin/sh\nexit 0\n")
        self.assertViolation("hook", ".githooks/pre-commit")

    def test_hook_that_only_mentions_the_guard(self):
        self.repo.write(".githooks/pre-commit", "#!/bin/sh\n# scripts/identifier-guard.py\nexit 0\n")
        self.assertViolation("hook", ".githooks/pre-commit")

    def test_hook_compared_with_the_hook_next_to_the_canonical_guard(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copyfile(GUARD, Path(d) / "identifier-guard.py")
            (Path(d) / "pre-commit").write_text("#!/bin/sh\nexec true\n")
            self.assertViolation("hook", ".githooks/pre-commit", "--canonical", str(Path(d) / "identifier-guard.py"))
            self.repo.copy(Path(d) / "pre-commit", ".githooks/pre-commit")
            self.assertClean("--canonical", str(Path(d) / "identifier-guard.py"))


class PublicRepo(Base):
    WF = ".github/workflows/ci.yml"

    def flagged(self, text, rel=WF):
        self.repo.write(rel, text)
        self.repo.config({"public": True})
        self.assertViolation("public-repo", rel)

    def test_private_repo_may_use_secrets_and_self_hosted(self):
        self.repo.write(self.WF, "jobs:\n  a:\n    runs-on: [self-hosted, linux]\n    env:\n      K: ${{ secrets.K }}\n")
        self.assertClean()

    def test_public_repo_secret_is_flagged(self):
        self.flagged("jobs:\n  a:\n    runs-on: ubuntu-latest\n    env:\n      K: ${{ secrets.DEPLOY_KEY }}\n")

    def test_public_repo_secret_forms(self):
        for text in ["    secrets: inherit\n", "      K: ${{ secrets['K'] }}\n", "      J: ${{ toJSON(secrets) }}\n"]:
            with self.subTest(text=text):
                self.flagged("jobs:\n  a:\n    runs-on: ubuntu-latest\n    env:\n" + text)

    def test_public_repo_self_hosted_is_flagged_via_flag(self):
        self.repo.write(self.WF, "jobs:\n  a:\n    runs-on: self-hosted\n")
        self.assertViolation("public-repo", self.WF, "--public")

    def test_public_repo_runs_on_must_be_a_github_hosted_label(self):
        for value in ["my-linux-box", "${{ vars.RUNNER }}", "${{ inputs.runs-on }}", "[linux, x64]"]:
            with self.subTest(value=value):
                self.flagged(f"jobs:\n  a:\n    runs-on: {value}\n")

    def test_public_repo_macos_and_windows_are_flagged(self):
        for value in ["macos-latest", "windows-latest", "[macos-14]"]:
            with self.subTest(value=value):
                self.flagged(f"jobs:\n  a:\n    runs-on: {value}\n")

    def test_public_repo_github_token_any_case(self):
        self.repo.write(self.WF, "jobs:\n  a:\n    runs-on: ubuntu-latest\n    env:\n      T: ${{ secrets.github_token }}\n")
        self.repo.config({"public": True})
        self.assertClean()

    def test_public_repo_github_hosted_labels_pass(self):
        self.repo.write(self.WF, "jobs:\n  a:\n    runs-on: ubuntu-latest\n  b:\n    runs-on: 'ubuntu-24.04'\n"
                        "  c:\n    runs-on: [ubuntu-22.04]\n    env:\n      T: ${{ secrets.GITHUB_TOKEN }}\n")
        self.repo.config({"public": True})
        self.assertClean()

    def test_public_repo_scans_yaml_and_local_actions(self):
        self.flagged("jobs:\n  a:\n    runs-on: self-hosted\n", ".github/workflows/x.yaml")
        repo = Repo(); self.addCleanup(repo.close); self.repo = repo
        self.flagged("runs:\n  using: composite\n  env:\n    K: ${{ secrets.K }}\n", ".github/actions/a/action.yml")

    def test_public_repo_private_dependency_is_flagged(self):
        self.flagged("jobs:\n  a:\n    uses: ccmenshealth/some-private/.github/workflows/x.yml@v1\n")
        repo = Repo(); self.addCleanup(repo.close); self.repo = repo
        self.flagged("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v6\n"
                     "        with:\n          repository: ccmenshealth/other\n")

    def test_public_repo_may_call_ci_recipes(self):
        self.repo.write(self.WF, "jobs:\n  a:\n    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1\n")
        self.repo.config({"public": True})
        self.assertClean()


def sh(*parts):
    """Secret-shaped values are assembled at run time so this file holds none."""
    return "".join(parts)


class SecretShapes(Base):
    SHAPES = {
        "logic-app-sig": sh("https://prod.example.com/workflows/x/triggers/manual/paths/invoke?api-version=2016-10-01&sp=%2F&sv=1.0&", "sig=", "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z"),
        "sas": sh("?sv=", "2022-11-02&ss=b&srt=co&sp=rl&", "sig=", "abcDEF123%2Bxyz456789ABCdef%3D"),
        "account-key": sh("DefaultEndpointsProtocol=https;AccountName=demo;", "AccountKey=", "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5d2FsZG8rL0E9PQ=="),
        "sas-token": sh("Endpoint=sb://x.example.com/;", "SharedAccessSignature", " sr=x&sig=abc&se=1&skn=k"),
        "private-key": sh("-----BEGIN ", "RSA PRIVATE KEY-----\n", "MIIEowIBAAKCAQEA", "x" * 48),
        "openssh-key": sh("-----BEGIN ", "OPENSSH PRIVATE KEY-----\n", "b3BlbnNzaC1rZXktdjEAAAAA", "y" * 40),
        "ec-key": sh("-----BEGIN ", "EC PRIVATE KEY-----\n", "MHcCAQEEI", "z" * 48),
        "plain-key": sh("-----BEGIN ", "PRIVATE KEY-----\n", "MIIEvQIBADANBgkqhkiG9w0B", "w" * 40),
        "github-classic": sh("gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
        "github-fine": sh("github", "_pat_", "11ABCDEFG0123456789_abcdefghijklmnop"),
        "aws": sh("AK", "IA", "ABCDEFGHIJKLMNOP"),
        "slack": sh("xo", "xb-", "1234567890-abcdefghij"),
        "bearer": sh("Authorization: ", "Bearer ", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk"),
    }

    def test_each_shape_is_flagged_with_line_and_no_value(self):
        for name, value in self.SHAPES.items():
            with self.subTest(name=name):
                repo = Repo(); self.addCleanup(repo.close); self.repo = repo
                repo.write("site/config.txt", "line one\nvalue = " + value + "\n")
                out = self.assertViolation("secret-shape", "site/config.txt:2")
                self.assertNotIn(value, out)
                self.assertNotIn(value[-12:], out)

    def test_placeholders_pass(self):
        lines = [sh("...&", "sig=", "REDACTED"), sh("AccountKey=", "REDACTED"), sh("AccountKey=", "<key>"),
                 sh("AccountKey=", "${{ secrets.KEY }}"), "Authorization: Bearer <token>", sh("Bearer ", "${TOKEN}"),
                 "the SharedAccessSignature header is built at run time", sh("gh", "p_", "short")]
        self.repo.write("docs/runbooks/rotate.md", "\n".join(lines) + "\n")
        self.assertClean()

    def test_one_line_fake_pem_without_a_key_body_passes(self):
        self.repo.write("tests/sign.test.ts", "const FAKE_PEM = '" + sh("-----BEGIN ", "RSA PRIVATE KEY-----") +
                        "\\nfake-key-bytes\\n-----END RSA PRIVATE KEY-----';\n")
        self.assertClean()

    def test_pem_with_a_key_body_is_flagged_inline_or_on_the_next_line(self):
        body = "MIIEowIBAAKCAQEA" + "q" * 48
        self.repo.write("k1.txt", "k = '" + sh("-----BEGIN ", "PRIVATE KEY-----") + "\\n" + body + "'\n")
        self.repo.write("k2.pem.txt", sh("-----BEGIN ", "OPENSSH PRIVATE KEY-----") + "\n" + body + "\n")
        code, out = self.repo.run()
        self.assertEqual(code, 1, out)
        self.assertIn("secret-shape\tk1.txt:1", out)
        self.assertIn("secret-shape\tk2.pem.txt:1", out)

    def test_storage_emulator_well_known_key_passes(self):
        # The emulator's published development key, identified by its SHA-256 so this repo never holds it; a test that
        # needs the real constant reads it from the emulator docs. Here: any other 88-char key is still flagged.
        other = sh("AccountKey=", "Q" * 86, "==")
        self.repo.write("t.test.ts", other + "\n")
        self.assertViolation("secret-shape", "t.test.ts:1")

    def test_published_public_keys_pass_by_hash(self):
        import hashlib
        import importlib.util
        spec = importlib.util.spec_from_file_location("check", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        value = "Z" * 86 + "=="
        self.repo.write("t.test.ts", sh("AccountKey=", value) + "\n")
        self.assertTrue(mod.secret_hits(self.repo.root, "t.test.ts"))
        mod.PUBLIC_KEY_SHA256 = frozenset({hashlib.sha256(value.encode()).hexdigest()})
        self.assertEqual(mod.secret_hits(self.repo.root, "t.test.ts"), [])

    def test_allow_does_not_exempt_secrets(self):
        self.repo.write("cfg/app.txt", self.SHAPES["github-classic"] + "\n")
        self.repo.config({"allow": ["cfg/app.txt"]})
        self.assertViolation("secret-shape", "cfg/app.txt:1")

    def test_binary_and_large_files_are_skipped(self):
        p = self.repo.root / "public" / "blob.bin"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"\0\1" + self.SHAPES["aws"].encode())
        big = self.repo.root / "data" / "big.txt"
        big.parent.mkdir(parents=True)
        big.write_text(("x" * 1023 + "\n") * 2100 + self.SHAPES["aws"] + "\n")
        self.assertClean()

    def test_every_tracked_text_file_is_scanned(self):
        self.repo.write(".github/workflows/x.yml", "env:\n  T: " + self.SHAPES["github-classic"] + "\n")
        self.assertViolation("secret-shape", ".github/workflows/x.yml:2")


class Config(Base):
    def test_valid_full_config(self):
        self.repo.write("docs/plans/p-history.md")
        self.repo.config({"version": 1, "public": False, "asset_paths": ["public/**"],
                          "allow": ["docs/plans/p-history.md"]})
        self.assertClean()

    def test_bad_json_is_an_error(self):
        self.repo.config("{not json")
        self.assertError()

    def test_unknown_key_is_an_error(self):
        self.repo.config({"image_path": ["public/**"]})
        self.assertError()

    def test_wrong_type_is_an_error(self):
        for bad in [{"public": "yes"}, {"asset_paths": "public/**"}, {"allow": [1]}, {"version": 2}, []]:
            with self.subTest(bad=bad):
                self.repo.config(bad)
                self.assertError()

    def test_stale_allow_entry_is_an_error(self):
        self.repo.config({"allow": ["docs/gone-history.md"]})
        self.assertError()


class Template(unittest.TestCase):
    """template/ is a conforming repo once the guard is copied in, and its copies cannot drift."""

    def test_template_passes_the_check(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "t"
            shutil.copytree(TEMPLATE, root)
            (root / "scripts").mkdir()
            shutil.copyfile(GUARD, root / "scripts/identifier-guard.py")
            git(root, "init", "-q")
            git(root, "add", "-A")
            r = subprocess.run([sys.executable, str(CHECK), "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_template_has_the_documented_files(self):
        for rel in ["README.md", "CLAUDE.md", ".github/PULL_REQUEST_TEMPLATE.md", ".githooks/pre-commit",
                    ".github/workflows/identifier-guard.yml", ".repo-standard.json", "docs/architecture.md",
                    "docs/decisions.md"]:
            self.assertTrue((TEMPLATE / rel).is_file(), rel)

    def test_template_names_the_secret_rule_and_all_test_shapes(self):
        pr = (TEMPLATE / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
        self.assertIn("Secrets never tracked; trigger URLs never under a served path", pr)
        claude = (TEMPLATE / "CLAUDE.md").read_text()
        for shape in ["ROE", "DOE", "POE", "555-01xx", "4711", "CCM-471NN", "999", "1900-01-01", "1970-01-01",
                      "example.com"]:
            self.assertIn(shape, claude)

    def test_template_hook_is_the_canonical_hook(self):
        self.assertEqual((TEMPLATE / ".githooks/pre-commit").read_bytes(), HOOK.read_bytes())
        self.assertTrue(os.access(TEMPLATE / ".githooks/pre-commit", os.X_OK))

    def test_template_caller_is_the_plain_caller_plus_standard(self):
        plain = PLAIN_CALLER.read_text()
        std = (TEMPLATE / ".github/workflows/identifier-guard.yml").read_text()
        uses = "    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1\n"
        self.assertIn(uses, plain)
        with_ = "    with:\n      standard: true   # also run the repo-standard check (STANDARD.md in ci-recipes)\n"
        self.assertEqual(std, plain.replace(uses, uses + with_))

    def test_template_config_lists_the_default_image_paths(self):
        cfg = json.loads((TEMPLATE / ".repo-standard.json").read_text())
        r = subprocess.run([sys.executable, str(CHECK), "--print-default-asset-paths"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(cfg["asset_paths"], json.loads(r.stdout))


class ReusableWorkflow(unittest.TestCase):
    """The standard step is opt-in and last, so callers that do not pass `standard` run what they ran before."""

    WF = (RECIPES / ".github/workflows/identifier-guard.yml").read_text()

    def test_standard_input_defaults_to_false(self):
        block = self.WF.split("      standard:\n", 1)[1].split("permissions:", 1)[0]
        self.assertIn("type: boolean", block)
        self.assertIn("default: false", block)

    def test_standard_step_is_last_and_gated(self):
        steps = self.WF.split("      - name: ")
        self.assertTrue(steps[-1].startswith("Check the repo standard"), steps[-1][:60])
        self.assertIn("if: ${{ !cancelled() && inputs.standard }}", steps[-1])
        self.assertEqual(sum("inputs.standard" in st for st in steps), 1)

    def test_scan_step_is_unchanged(self):
        scan = [st for st in self.WF.split("      - name: ") if st.startswith("Scan added lines")][0]
        for line in ["rm -rf .ci-recipes",
                     'exec python3 scripts/identifier-guard.py --three-dot --base "$PR_BASE" --head "$PR_HEAD"',
                     'exec python3 scripts/identifier-guard.py --base "$BASE" --head HEAD']:
            self.assertIn(line, scan)
        self.assertNotIn("if:", scan.split("run:", 1)[0])


class ShellLib(unittest.TestCase):
    """lib/apply.sh, the placement sync.sh and new-repo.sh share."""

    def bash(self, root, script):
        r = subprocess.run(["bash", "-c", f'set -euo pipefail; RECIPES="$1"; . "$RECIPES/lib/apply.sh"; {script}',
                            "bash", str(RECIPES), str(root)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "README.md").write_text("# x\n")

    def caller(self):
        return (self.root / ".github/workflows/identifier-guard.yml").read_text()

    def test_plain_sync_writes_the_plain_caller(self):
        code, out = self.bash(self.root, 'apply_guard "$2"; in_sync "$2" 0')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.caller(), PLAIN_CALLER.read_text())

    def test_plain_sync_keeps_an_opted_in_repo_on_the_standard_caller(self):
        code, out = self.bash(self.root, 'apply_standard "$2" 0; apply_guard "$2"; in_sync "$2" 0; in_sync "$2" 1')
        self.assertEqual(code, 0, out)
        self.assertIn("standard: true", self.caller())

    def test_standard_makes_a_conforming_repo(self):
        code, out = self.bash(self.root, 'apply_standard "$2" 1')
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads((self.root / ".repo-standard.json").read_text())["public"])
        git(self.root, "init", "-q"); git(self.root, "add", "-A")
        r = subprocess.run([sys.executable, str(CHECK), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_claude_md_imports_agents_md_when_present(self):
        (self.root / "AGENTS.md").write_text("rules\n")
        self.bash(self.root, 'apply_standard "$2" 0')
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "@AGENTS.md\n")

    def test_existing_files_are_kept_and_ci_cost_appended(self):
        (self.root / "CLAUDE.md").write_text("mine\n")
        (self.root / ".github").mkdir()
        (self.root / ".github/PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        code, out = self.bash(self.root, 'apply_standard "$2" 0; apply_standard "$2" 0')
        self.assertEqual(code, 0, out)
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "mine\n")
        pr = (self.root / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
        self.assertTrue(pr.startswith("## Summary\n"))
        self.assertEqual(pr.count("CI cost:"), 1, pr)

    def test_public_flag_is_never_downgraded(self):
        self.bash(self.root, 'apply_standard "$2" 1')
        code, out = self.bash(self.root, 'apply_standard "$2" 0')
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads((self.root / ".repo-standard.json").read_text())["public"])

    def test_bad_existing_config_fails_the_call_not_the_shell(self):
        (self.root / ".repo-standard.json").write_text("{bad")
        code, out = self.bash(self.root, 'if apply_standard "$2" 0; then echo applied; else echo refused; fi')
        self.assertEqual(code, 0, out)
        self.assertIn("refused", out)
        self.assertFalse((self.root / "scripts/identifier-guard.py").exists())

    def test_in_sync_is_false_for_a_bare_repo(self):
        code, _ = self.bash(self.root, 'in_sync "$2" 0')
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
