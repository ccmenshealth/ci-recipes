# Repo standard

Every ccmenshealth repo, present and future, follows these rules. `repo-standard/check.py` enforces the ones a machine
can check; the rest are review rules. Each rule ends with why it exists.

## Documents

A repo holds only documentation that helps someone maintain the code. The fixed set:

| Path | What | Required |
|---|---|---|
| `README.md` | what it is, how to run it, how it deploys | yes |
| `CLAUDE.md` | rules for working in the repo (may be one line, `@AGENTS.md`, when the rules live in `AGENTS.md`) | yes |
| `AGENTS.md` | the same rules for other agents | no |
| `docs/architecture.md` | how the parts fit together | when there is more than one part |
| `docs/decisions.md` | dated decisions that still hold | when decisions exist |
| `docs/runbooks/` | generic procedures (deploy, rotate, restore) | when there are procedures |
| a spec | the product spec, where one exists | no |
| `docs/plans/` | plan documents, only while they carry decisions; no ledgers inside them | no |

- **No narrative files**: no histories of the work, hand-overs, ledgers, progress logs, to-do lists, audits, archives,
  status files, bookmarks, resume or startup prompts. Status and "what happened when" go to the memory system
  (`~/.claude/projects/<repo>/memory/`), which loads per repo and never leaves the Mac. *Why: narrative goes stale,
  duplicates git, and is the kind of text most likely to carry
  an identifier.*
- **Records state the rule, never the event**: commit messages, PRs, docs and notes say what a thing now is, not what
  it was or what went wrong. *Why: a record of an event can imply what data was where; a rule cannot.*

## Images

Only images that code references: logos, app and web icons, generated synthetic goldens, tiny synthetic fixtures.
Never a screenshot of a live screen; mockups are HTML files, not captures. *Why: an image cannot be scanned for
identifiers by the guard, so the only safe image is one nobody had to look at a live screen to make.*

Default allowed paths (case-insensitive): `public/**`, `**/public/**`, `assets/**`, `**/assets/**`,
`fixtures/golden/**`, `**/*.ico`, `**/*.icns`, `**/*.appiconset/**`, `**/*.iconset/**`, `**/*.xcassets/**`,
`**/icons/**`, `**/icon/**`, `**/app/icon.*`, `**/app/apple-icon.*`. Any other asset directory (a fixture folder, a
logo folder) is named in `image_paths` in `.repo-standard.json` after someone has looked at what is in it.

## Test data

There is no "synthetic PHI"; there is test data, and it uses only these shapes, the only ones the identifier guard
exempts. *Why: a value in patient shape that is not one of these cannot be told apart from a real one.*

- Persona surnames ROE, DOE, POE, BLOGGS, MOCKLEY, STUBBS, FAKEMAN.
- Phones 555-01xx and area code 555.
- The 4711 number block; record numbers CCM-471NN; the 999-prefixed sentinel.
- Placeholder dates 1900-01-01 and 1970-01-01.
- E-mail at example.com.

Generated fixtures (a script that writes the values) are preferred; hand-typed values live only under fixture paths.
*Why: a generator states the shape once and cannot copy a real value by accident.*

## Identifier guard

Every repo carries `scripts/identifier-guard.py` (byte-identical to `identifier-guard/identifier-guard.py` here),
`.githooks/pre-commit` (byte-identical to `identifier-guard/pre-commit`; `git config core.hooksPath .githooks` once per
clone) and `.github/workflows/identifier-guard.yml`, the thin caller of the reusable workflow. *Why: the hook stops an
identifier before it is committed, offline; CI stops it before it is merged; one canonical copy keeps them identical.*
Change the guard here and run `./sync.sh`; never edit a repo's copy.

## Pull requests

`.github/PULL_REQUEST_TEMPLATE.md` exists and has a line that starts `CI cost` (template:
`template/.github/PULL_REQUEST_TEMPLATE.md`). Every PR that adds or changes a workflow trigger, job, schedule, matrix
or runner fills it in with the added GitHub-hosted minutes and dollars per month. *Why: CI minutes cost money; a
line in every PR puts the cost where the change is made.*

## Public repos

A public repo never touches a system that may hold patient data: no secret (other than `GITHUB_TOKEN`), no
self-hosted runner, no dependency on a private repo. Set `"public": true` in `.repo-standard.json`. *Why: anyone can
open a pull request against a public repo, and its workflows run with whatever the repo can reach.*

## `.repo-standard.json`

```json
{
  "version": 1,
  "public": false,
  "image_paths": ["public/**", "assets/**"],
  "allow": ["docs/plans/widget-ledger.md"]
}
```

- `public`: applies the public-repo rule.
- `image_paths`: replaces the default image allowlist when present (the template lists the default). An entry that
  would allow an image at the top level or under `docs/` is refused.
- `allow`: exact tracked paths exempt from the narrative-name and narrative-directory rules, for a file whose name
  only looks narrative (a feature called "ledger"). An entry for a file that no longer exists is an error.

Unknown keys and wrong types are errors, so a typo never switches a rule off silently.

## The check

`python3 repo-standard/check.py --root <repo>` (stdlib only) looks at tracked files and prints one tab-separated line
per violation: rule, path, fix. Paths only, never file content. Exit 0 clean, 1 violations, 2 error.

| Rule | Fails when |
|---|---|
| `narrative-name` | a document file (`.md`, `.txt`, `.rst`, `.html`, `.pdf`, `.docx`, …, or no extension) is named like narrative: contains hand-off/handoff, ledger, current-state, resume-prompt or startup-prompt; a history of the work (`session-history`, `change-history`, `HISTORY.md`); or the whole name is progress, status, todo(s), bookmark(s) |
| `narrative-dir` | any file under `docs/` sits in a directory named progress, todo(s), audit(s), archive(d), history/histories, handoff(s), ledger(s), sessions or status |
| `image-path` | an image (`png jpg jpeg gif webp bmp tif tiff heic heif avif svg ico icns`) is outside the allowed paths |
| `required-file` | `README.md` or `CLAUDE.md` is missing |
| `pr-template` | `.github/PULL_REQUEST_TEMPLATE.md` is missing or has no `CI cost` line |
| `guard` | `scripts/identifier-guard.py` is missing or differs from the canonical copy |
| `hook` | `.githooks/pre-commit` is missing or differs from the canonical hook |
| `public-repo` | (public repos) a workflow or local action names a secret other than `GITHUB_TOKEN` (including `secrets: inherit`), runs on anything but a literal GitHub-hosted label, or uses or checks out a `ccmenshealth/` repo other than ci-recipes |

Names only count for document files: a source file called `history.ts` or `handoff.py` is code, not narrative.
"History" alone is a feature or clinical word (payment history, medical history) and passes.

Not machine-checked (review rules): which documents sit under `docs/` beyond the narrative names; what an image in an
allowed path shows; wording of records (rule, not event); a repo that has no caller workflow at all (see New repos).

## In CI

The reusable workflow runs the check after the identifier scan, in the same job, when the caller passes
`standard: true`. The caller change, one block under `uses:`:

```yaml
jobs:
  guard:
    if: github.event_name != 'pull_request' || github.event.pull_request.draft == false
    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1
    with:
      standard: true
```

The scan always runs first, so a standard failure never hides its result. A public caller gets the public-repo rule
from its visibility as well as from the config. Cost: about a second inside the existing one-minute job, $0.

## New repos

`./new-repo.sh <name> --private|--public` builds the repo from `template/`, adds the guard, checks the tree, creates the
GitHub repo, and sets delete-branch-on-merge, no wiki, Actions with a read-only token (a public repo allows only
GitHub-owned actions and this repo's reusable workflow). It prints what the owner still does: secrets, CodeRabbit,
the hook, memory. `--dry-run` builds and checks the tree and prints the GitHub calls without making them.

A repo that skipped the template has no caller, so nothing in it runs the check. The owner's periodic sweep runs
`check.py` over a fresh clone of every repo.

## Existing repos

`./sync.sh --standard --only <repo>` opens one PR per repo that adds the PR template (or appends the CI-cost section to
an existing one), `.repo-standard.json`, a one-line `CLAUDE.md` if missing, and the caller with `standard: true`, and
puts the check's findings in the PR body. Fix the findings on that branch (rename, move, delete, or a config entry)
before merging. After that, a plain `./sync.sh` keeps the repo on the standard caller.

ci-recipes itself is the source of the standard, not a consumer: the canonical guard lives in `identifier-guard/`, so
it is not checked against itself.
