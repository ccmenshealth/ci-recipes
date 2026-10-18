#!/usr/bin/env bash
# sync.sh: push the canonical identifier guard into every ccmenshealth repo, one PR per repo.
#
# For each repo: clone the default branch fresh into a work directory, branch ci/identifier-guard-v1, copy
# identifier-guard/identifier-guard.py -> scripts/identifier-guard.py, identifier-guard/pre-commit -> .githooks/pre-commit,
# identifier-guard/caller-workflow.yml -> .github/workflows/identifier-guard.yml, scan the whole repo with the OLD copy and
# the NEW copy and put the delta (file:line + pattern only, never the matched text) in the PR body, commit, push, open a PR.
# A repo whose three files already match is skipped. A repo with an open ci/identifier-guard-v1 PR is skipped unless --force.
#
# usage: ./sync.sh [--only repo1,repo2] [--dry-run] [--force] [--workdir DIR]
#   --only     comma-separated repo names (the rollout waves); default: every repo in REPOS below
#   --dry-run  clone, copy, scan and print the PR body; no commit, push or PR
#   --force    push and open a PR even if one is already open for the branch (the branch is replaced)
# Requires: git, gh (logged in as the ccmenshealth account), python3.
set -euo pipefail

OWNER=ccmenshealth
BRANCH=ci/identifier-guard-v1
REPOS=(ccmh-docs-upload mock-quest-quantum-hub
       vcmc-cerner-agent ccmehr vcmc-notes-webapp vcmc-visit-notes vcmc-paste-helper ccmh-med-tracker
       ccmh-admin ccmh-contact-form ccmh-forms ccmh-forms-webapp ccmh-surgery-tracker oe-module-ccmehr-bridge physician-invoice-portal
       pane-management)

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_SCRIPT="$HERE/identifier-guard/identifier-guard.py"
SRC_HOOK="$HERE/identifier-guard/pre-commit"
SRC_WF="$HERE/identifier-guard/caller-workflow.yml"
WORKDIR="${TMPDIR:-/tmp}/ci-recipes-sync"
DRY=0; FORCE=0; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force) FORCE=1; shift ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -f "$SRC_SCRIPT" ] && [ -f "$SRC_HOOK" ] && [ -f "$SRC_WF" ] || { echo "run from a ci-recipes clone" >&2; exit 2; }
if [ -n "$ONLY" ]; then IFS=',' read -r -a REPOS <<< "${ONLY//$OWNER\//}"; fi
mkdir -p "$WORKDIR"
CANON_SHA="$(git -C "$HERE" hash-object "$SRC_SCRIPT")"
RECIPES_REV="$(git -C "$HERE" rev-parse --short HEAD)"

# full_scan <script> <clone>: every tracked file through the guard in --files mode; prints "path:line pattern" lines sorted.
full_scan() {
  ( cd "$2" && git ls-files -z | xargs -0 python3 "$1" --files 2>/dev/null || true ) | { grep '^  ' || true; } | sed 's/^  //' | sort
}

for repo in "${REPOS[@]}"; do
  echo "=== $repo"
  clone="$WORKDIR/$repo"
  rm -rf "$clone"
  default="$(gh api "repos/$OWNER/$repo" --jq .default_branch)"
  git clone -q --branch "$default" "https://github.com/$OWNER/$repo.git" "$clone"
  if [ -f "$clone/scripts/identifier-guard.py" ] && cmp -s "$SRC_SCRIPT" "$clone/scripts/identifier-guard.py" \
     && cmp -s "$SRC_HOOK" "$clone/.githooks/pre-commit" && cmp -s "$SRC_WF" "$clone/.github/workflows/identifier-guard.yml"; then
    echo "    already in sync; skipped"; continue
  fi
  open_pr="$(gh pr list -R "$OWNER/$repo" --head "$BRANCH" --state open --json number --jq '.[0].number // empty')"
  if [ -n "$open_pr" ] && [ "$FORCE" = 0 ]; then echo "    PR #$open_pr already open for $BRANCH; skipped (use --force)"; continue; fi

  # Delta: the repo's current copy versus the canonical one, whole files at the default branch head.
  before="$WORKDIR/$repo.before.txt"; after="$WORKDIR/$repo.after.txt"
  if [ -f "$clone/scripts/identifier-guard.py" ]; then cp "$clone/scripts/identifier-guard.py" "$WORKDIR/$repo.old-guard.py"; full_scan "$WORKDIR/$repo.old-guard.py" "$clone" > "$before"; else : > "$before"; fi
  full_scan "$SRC_SCRIPT" "$clone" > "$after"
  gone="$(comm -23 "$before" "$after")"; new="$(comm -13 "$before" "$after")"
  n_before=$(wc -l < "$before" | tr -d ' '); n_after=$(wc -l < "$after" | tr -d ' ')
  n_gone=$(printf '%s' "$gone" | grep -c . || true); n_new=$(printf '%s' "$new" | grep -c . || true)

  git -C "$clone" checkout -q -b "$BRANCH"
  mkdir -p "$clone/scripts" "$clone/.githooks" "$clone/.github/workflows"
  cp "$SRC_SCRIPT" "$clone/scripts/identifier-guard.py"
  cp "$SRC_HOOK" "$clone/.githooks/pre-commit"; chmod +x "$clone/.githooks/pre-commit"
  cp "$SRC_WF" "$clone/.github/workflows/identifier-guard.yml"
  git -C "$clone" add scripts/identifier-guard.py .githooks/pre-commit .github/workflows/identifier-guard.yml

  body="$WORKDIR/$repo.body.md"
  {
    echo "Thin caller for the shared identifier guard: \`scripts/identifier-guard.py\` and \`.githooks/pre-commit\` become byte-identical copies of \`ccmenshealth/ci-recipes\` @ ${RECIPES_REV} (script blob \`${CANON_SHA:0:8}\`); \`.github/workflows/identifier-guard.yml\` now calls the reusable workflow at \`@v1\`, which fails if this repo's copy ever differs from the canonical one and then runs this repo's own copy on added lines, exactly as before. Hook behaviour is unchanged (added lines only, offline)."
    echo
    echo "Plan: \`vcmc-notes/docs/plans/F_2026-10-17_identifier-guard-hardening.plan.md\` (F1-F4, G1-G5). Hits are reported as file:line + pattern only."
    echo
    echo "## Full-scan delta on \`$default\` (whole files, informational: the hook and CI scan added lines only)"
    echo
    echo "| | hits |"; echo "|---|---|"
    echo "| current copy | $n_before |"; echo "| canonical copy | $n_after |"; echo "| gone | $n_gone |"; echo "| new | $n_new |"
    if [ "$n_new" -gt 0 ]; then
      echo; echo "New hits by pattern:"; echo; printf '%s\n' "$new" | awk '{print $2}' | sort | uniq -c | awk '{print "- " $2 ": " $1}'
      echo; echo "<details><summary>New hits (file:line pattern)</summary>"; echo; printf '%s\n' "$new" | sed 's/^/    /'; echo; echo "</details>"
    fi
    if [ "$n_gone" -gt 0 ]; then
      echo; echo "<details><summary>Hits no longer reported (file:line pattern)</summary>"; echo; printf '%s\n' "$gone" | sed 's/^/    /'; echo; echo "</details>"
    fi
    echo
    echo "## CI cost"
    echo
    echo "Same triggers as before (ready PRs, Monday 8:17 AM PT, manual); one GitHub-hosted Linux job per run billed as 1 minute (\$0.006). The second checkout adds ~5 s inside that minute: +\$0/month. No macOS/Windows jobs."
    echo
    echo "## Review"
    echo
    echo "- [ ] CI: identifier-guard (hash check + scan) green"
    echo "- [ ] Independent review"
    echo
    echo "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
  } > "$body"

  echo "    scan: before=$n_before after=$n_after gone=$n_gone new=$n_new"
  if [ "$DRY" = 1 ]; then echo "    dry run: body at $body"; git -C "$clone" status --short | sed 's/^/    /'; continue; fi
  if git -C "$clone" diff --cached --quiet; then echo "    nothing to commit; skipped"; continue; fi
  git -C "$clone" -c commit.gpgsign=false commit -q -F - <<EOF
Use the shared identifier guard from ccmenshealth/ci-recipes@v1

scripts/identifier-guard.py and .githooks/pre-commit are byte-identical copies of ci-recipes ${RECIPES_REV};
the workflow is a thin caller of the reusable workflow, which checks the copy against the canonical one before
running it. Full-scan delta on ${default}: ${n_before} -> ${n_after} hits (${n_gone} gone, ${n_new} new).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
  git -C "$clone" push -q --force-with-lease -u origin "$BRANCH"
  if [ -n "$open_pr" ]; then
    gh pr edit "$open_pr" -R "$OWNER/$repo" --body-file "$body" >/dev/null; echo "    updated PR #$open_pr"
  else
    gh pr create -R "$OWNER/$repo" --base "$default" --head "$BRANCH" --title "Use the shared identifier guard from ci-recipes@v1" --body-file "$body"
  fi
done
