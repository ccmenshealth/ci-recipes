# ci-recipes

Shared CI recipes for ccmenshealth repositories. This repo is public and holds nothing private: no secrets, no
self-hosted runner, no dependency on a private repo.

## identifier-guard

`identifier-guard/identifier-guard.py` fails a commit or pull request whose added lines look like patient identifiers
(MRN/FIN, CCM record numbers, DOB, SSN, US phone numbers, `LAST, FIRST` next to a number, personal e-mail). It prints
file, line and pattern only, never the matched text. Allowed test-data shapes, the only ones it exempts: ROE/DOE/POE
personas, 555-01xx or area-code-555 phones (and any impossible NANP number), the 4711 number block, CCM-471NN,
999-prefixed sentinel record numbers, the placeholder dates 1900-01-01 and 1970-01-01, example.com e-mail, and the words
synthetic / fixture / placeholder / sentinel on the line. Record-number and DOB shapes are also allowed under fixture paths.

Every ccmenshealth repo carries a byte-identical copy at `scripts/identifier-guard.py` (so the pre-commit hook in
`.githooks/pre-commit` works offline) and a thin workflow that calls the reusable workflow here:

```yaml
jobs:
  guard:
    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1
```

The reusable workflow checks out the caller, fails if its `scripts/identifier-guard.py` differs from the canonical
copy (with the fix in the error), then runs the caller's own copy: three-dot diff against the PR base on
`pull_request`, the default branch's last 8 days on `schedule` / `workflow_dispatch`. Input `runs-on` (default
`ubuntu-latest`) lets a private caller use its own Linux runner; public callers never do.

### Changing the guard

1. Add a failing test row to `identifier-guard/tests/test_identifier_guard.py` (stdlib unittest; literals in the test
   file use only the allowed shapes, flagged values are assembled at run time), then fix the script.
2. `python3 -m unittest discover -s identifier-guard/tests` and open a PR here; CI runs the tests and scans this repo.
3. After the merge, move the `v1` tag to the new commit (`git tag -f v1 && git push -f origin v1`) and run `./sync.sh`
   from this clone: it clones each repo, copies the script, hook and caller workflow, and opens one PR per repo with the
   repo's full-scan delta (file:line + pattern) in the body. `./sync.sh --only repo1,repo2` for a subset, `--dry-run`
   to look first. Until a repo merges its PR, its next guard run fails the hash check with the same fix instruction.

## repo-standard

[`STANDARD.md`](STANDARD.md) is the standard every repo follows (doc set, images, test data, guard, PR template,
public-repo rule). `repo-standard/check.py` enforces the checkable part; a caller opts in with `with: standard: true`
and the reusable workflow runs it after the scan in the same job. `template/` is what a new repo starts from
(`./new-repo.sh <name> --private|--public [--dry-run]`); `./sync.sh --standard --only <repo>` adopts the standard in an
existing repo, one PR each. Tests: `python3 -m unittest discover -s repo-standard/tests`.

Tags: `v1` moves with every change that keeps existing callers' results the same (a new opt-in input does); a change
that would alter what an existing caller runs gets a new major tag. Release tags (`v1.1`, …) mark each release.

### Files

| Path | What |
|---|---|
| `identifier-guard/identifier-guard.py` | the canonical script |
| `identifier-guard/pre-commit` | the hook each repo installs at `.githooks/pre-commit` (`git config core.hooksPath .githooks` once per clone) |
| `identifier-guard/caller-workflow.yml` | the thin workflow each repo carries at `.github/workflows/identifier-guard.yml` |
| `identifier-guard/tests/` | unit tests |
| `.github/workflows/identifier-guard.yml` | the reusable workflow (`workflow_call`) |
| `.github/workflows/test.yml` | CI for this repo |
| `sync.sh` | rollout: one PR per repo (`--standard` adopts the repo standard) |
| `STANDARD.md` | the repo standard |
| `repo-standard/check.py`, `repo-standard/tests/` | the standard check and its tests |
| `template/` | the files a new repo starts from |
| `new-repo.sh` | create a repo from the template |
| `lib/apply.sh` | file placement shared by `sync.sh` and `new-repo.sh` |

### Cost

One GitHub-hosted Linux job per guard run in each repo, billed as 1 minute ($0.006); the reusable workflow's second
checkout adds a few seconds inside that minute, and the opt-in standard step about a second ($0). This repo's own
tests run a few times a month: under $0.10/month.
