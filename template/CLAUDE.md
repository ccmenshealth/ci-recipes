# __REPO__: rules

This repo follows the ccmenshealth repo standard:
https://github.com/ccmenshealth/ci-recipes/blob/main/STANDARD.md (doc set, assets, secrets, test data, guard, PR template).

## Kickoff

Read in order: `README.md` → this file → the repo's memory notes (`~/.claude/projects/<repo>/memory/`) →
`docs/decisions.md`. Status, hand-overs and "what happened when" live in memory, never in this repo.

## Rules

- No patient identifiers anywhere: code, tests, fixtures, docs, commits, PRs. Test data uses only the allowed shapes:
  personas ROE/DOE/POE/BLOGGS/MOCKLEY/STUBBS/FAKEMAN, phones 555-01xx or area code 555, the 4711 block, CCM-471NN, the
  999 sentinel, dates 1900-01-01 / 1970-01-01, e-mail at example.com. Prefer generated fixtures.
- Secrets, keys and signed trigger URLs are never tracked; a served path holds only site files.
- Records state the rule, never the event.
- The identifier guard (`scripts/identifier-guard.py`, `.githooks/pre-commit`) is managed by ci-recipes; never edit it
  here.
- Every PR fills in the CI-cost line of the PR template.

## Build and test

(Commands for lint, typecheck, test and build. Name every gate a PR must pass.)
