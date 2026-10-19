#!/usr/bin/env python3
"""Tests for the oidc phase of org-move/move-repo.sh (stdlib unittest; python3 -m unittest discover -s org-move/tests).

move-repo.sh runs against stub `az` and `gh` commands on PATH that serve a federated-credential list and the org
repo's OIDC subject setting from files, and record every credential they are asked to create.
MOVE=/path/to/move-repo.sh runs the tests against another copy (used to show them failing against the pre-change file).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOVE = os.environ.get("MOVE") or os.path.abspath(os.path.join(HERE, "..", "move-repo.sh"))
ISSUER = "https://token.actions.githubusercontent.com"
PLAIN = "repo:org1/repo1:ref:refs/heads/main"
IMMUTABLE = "repo:org1@1000/repo1@2000:ref:refs/heads/main"

AZ = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["STUB"]; a = sys.argv[1:]
creds = os.path.join(d, "creds.json")
if a[:3] == ["ad", "app", "list"]:
    print("00000000-0000-0000-0000-00000000aaaa"); sys.exit(0)
if a[:3] == ["ad", "app", "federated-credential"]:
    have = json.load(open(creds))
    if a[3] == "list":
        if "--query" in a:
            for c in have: print("%s\t%s" % (c["name"], c["subject"]))
        else:
            print(json.dumps(have))
        sys.exit(0)
    if a[3] == "create":
        spec = json.load(open(a[a.index("--parameters") + 1].lstrip("@")))
        have.append(spec); json.dump(have, open(creds, "w"))
        open(os.path.join(d, "created.log"), "a").write(spec["subject"] + "\n")
        print("%s\t%s" % (spec["name"], spec["subject"])); sys.exit(0)
sys.exit("az stub: unexpected %s" % a)
'''

GH = r'''#!/usr/bin/env python3
import os, sys
d = os.environ["STUB"]; a = sys.argv[1:]
if a[:1] == ["api"] and a[1].endswith("/actions/oidc/customization/sub"):
    p = os.path.join(d, "sub.json")
    if not os.path.exists(p): sys.stderr.write("HTTP 404\n"); sys.exit(1)
    print(open(p).read()); sys.exit(0)
if a[:1] == ["api"] and a[1] == "repos/org1/repo1":
    sys.exit(0 if os.path.exists(os.path.join(d, "repo-exists")) else 1)
sys.exit("gh stub: unexpected %s" % a)
'''


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def cred(name, subject):
    return {"name": name, "issuer": ISSUER, "subject": subject, "audiences": ["api://AzureADTokenExchange"],
            "description": name}


class OidcPhase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td)
        self.bin = os.path.join(self.td, "bin"); os.mkdir(self.bin)
        for name, body in (("az", AZ), ("gh", GH)):
            p = os.path.join(self.bin, name); write(p, body); os.chmod(p, 0o755)
        self.creds([cred("deploy", "repo:old1/repo1:ref:refs/heads/main")])
        write(os.path.join(self.td, "repo-exists"), "")

    def creds(self, items):
        write(os.path.join(self.td, "creds.json"), json.dumps(items))

    def subject_setting(self, setting):
        write(os.path.join(self.td, "sub.json"), json.dumps(setting))

    def run_phase(self, phase="oidc", plan_only=False):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], STUB=self.td, ORG="org1",
                   OLD_OWNER="old1", WORK=os.path.join(self.td, "work"), EVIDENCE=os.path.join(self.td, "ev"))
        if plan_only: env["PLAN_ONLY"] = "1"
        return subprocess.run(["bash", MOVE, phase, "repo1", "app1"], env=env, capture_output=True, text=True)

    def created(self):
        p = os.path.join(self.td, "created.log")
        return read(p).split() if os.path.exists(p) else []

    def immutable_on(self):
        self.subject_setting({"use_default": True, "use_immutable_subject": True,
                              "sub_claim_prefix": "repo:org1@1000/repo1@2000"})

    def test_immutable_subject_creates_both_twins(self):
        self.immutable_on()
        r = self.run_phase()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(self.created()), sorted([PLAIN, IMMUTABLE]))

    def test_immutable_twin_name_is_distinct(self):
        self.immutable_on()
        self.run_phase()
        names = [c["name"] for c in json.loads(read(os.path.join(self.td, "creds.json")))]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 3)

    def test_plan_only_lists_the_immutable_twin_and_creates_nothing(self):
        self.immutable_on()
        r = self.run_phase(plan_only=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(IMMUTABLE, r.stdout)
        self.assertEqual(self.created(), [])

    def test_mutable_subject_creates_the_plain_twin_only(self):
        self.subject_setting({"use_default": True, "use_immutable_subject": False})
        r = self.run_phase()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.created(), [PLAIN])

    def test_existing_twins_are_not_recreated(self):
        self.immutable_on()
        self.creds([cred("deploy", "repo:old1/repo1:ref:refs/heads/main"), cred("deploy-org1", PLAIN),
                    cred("deploy-org1-immutable", IMMUTABLE)])
        r = self.run_phase()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.created(), [])

    def test_custom_subject_template_stops(self):
        self.subject_setting({"use_default": False, "include_claim_keys": ["repo", "context"]})
        r = self.run_phase()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("custom", (r.stdout + r.stderr).lower())
        self.assertEqual(self.created(), [])

    def test_unexpected_prefix_stops(self):
        self.subject_setting({"use_default": True, "use_immutable_subject": True,
                              "sub_claim_prefix": "repo:other@1/repo1@2"})
        r = self.run_phase()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.created(), [])

    def test_missing_org_repo_stops_unless_plan_only(self):
        os.remove(os.path.join(self.td, "repo-exists"))
        r = self.run_phase()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.created(), [])
        r = self.run_phase(plan_only=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(PLAIN, r.stdout)

    def test_status_line_names_the_format_once(self):
        self.immutable_on()
        r = self.run_phase(plan_only=True)
        line = [l for l in r.stdout.splitlines() if l.startswith("OIDC subject format")]
        self.assertEqual(line, ["OIDC subject format of org1/repo1: immutable (repo:org1@1000/repo1@2000)"])

    def test_azure_secrets_requires_the_immutable_twin(self):
        self.immutable_on()
        self.creds([cred("deploy", "repo:old1/repo1:ref:refs/heads/main"), cred("deploy-org1", PLAIN)])
        r = self.run_phase(phase="azure-secrets")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no twin credential", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
