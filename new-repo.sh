#!/usr/bin/env bash
# new-repo.sh: create a ccmenshealth repo from template/ that passes the repo standard (STANDARD.md) on its first commit.
#
# Builds the tree in a temp directory: template/ with __REPO__ replaced by the name, the identifier guard and hook
# (lib/apply.sh, the same placement sync.sh uses), "public" set in .repo-standard.json; commits it on main and runs
# repo-standard/check.py, which must pass. Then creates the GitHub repo, pushes, and sets: delete branch on merge, no
# wiki, Actions enabled with read-only default token and no PR approvals by Actions; a public repo allows only
# GitHub-owned actions and ci-recipes' reusable workflow. Finally prints what the owner must still do.
#
# The caller pins the latest v1.N ci-recipes tag on origin (or --tag), which must carry this clone's guard.
#
# usage: ./new-repo.sh <name> --private|--public [--dry-run] [--owner OWNER] [--tag v1.N]
#   --dry-run  build and check the tree, print the gh calls; no network (the pin is the latest local v1.N tag), nothing
#              created. The tree is kept.
# Requires: git, python3; gh (logged in as the owner) unless --dry-run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPES="$HERE"
# shellcheck source=lib/apply.sh
. "$HERE/lib/apply.sh"

OWNER=ccmenshealth; NAME=""; VIS=""; DRY=0; PIN_TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --private) VIS=private; shift ;;
    --public) VIS=public; shift ;;
    --dry-run) DRY=1; shift ;;
    --owner) [ -n "${2:-}" ] || { echo "--owner needs a value" >&2; exit 2; }; OWNER="$2"; shift 2 ;;
    --tag) [ -n "${2:-}" ] || { echo "--tag needs a value" >&2; exit 2; }; PIN_TAG="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) [ -z "$NAME" ] || { echo "one name only" >&2; exit 2; }; NAME="$1"; shift ;;
  esac
done
if [ -z "$NAME" ] || [ -z "$VIS" ]; then echo "usage: ./new-repo.sh <name> --private|--public [--dry-run]" >&2; exit 2; fi
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,99}$ ]] || { echo "name must be lower-case letters, digits and dashes" >&2; exit 2; }

if [ "$DRY" = 1 ]; then
  PIN_TAG="${PIN_TAG:-$(latest_release_tag .)}"
  if [ -z "$PIN_TAG" ]; then PIN_TAG=v1.0; echo "dry run: no local v1.N tag; the caller shows @$PIN_TAG" >&2; fi
else
  PIN_TAG="${PIN_TAG:-$(latest_release_tag origin)}"
  check_pin_tag "$PIN_TAG" || exit 2
fi
export PIN_TAG

run() { if [ "$DRY" = 1 ]; then printf '    would run:'; printf ' %q' "$@"; echo; else "$@"; fi; }

if [ "$DRY" = 0 ] && gh repo view "$OWNER/$NAME" >/dev/null 2>&1; then
  echo "$OWNER/$NAME already exists" >&2; exit 2
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/new-repo.XXXXXX")"
tree="$work/$NAME"
cp -R "$RECIPES/template" "$tree"
{ grep -rlF '__REPO__' "$tree" || true; } | while IFS= read -r f; do
  python3 - "$f" "$NAME" <<'EOF'
import sys
path, name = sys.argv[1], sys.argv[2]
text = open(path).read()
open(path, "w").write(text.replace("__REPO__", name))
EOF
done
pub=0; [ "$VIS" = public ] && pub=1
apply_standard "$tree" "$pub"

git -C "$tree" init -q -b main
git -C "$tree" add -A
python3 "$CHECK" --root "$tree" || { echo "the template tree fails the repo standard; fix template/ first" >&2; exit 1; }
git -C "$tree" -c commit.gpgsign=false -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name || echo new-repo)}" \
  -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email || echo new-repo@example.com)}" \
  commit -q -m "Start $NAME from the ci-recipes template"
echo "tree: $tree ($(git -C "$tree" ls-files | wc -l | tr -d ' ') files, repo-standard clean)"

echo "=== GitHub ($OWNER/$NAME, $VIS)"
run gh repo create "$OWNER/$NAME" "--$VIS" --source "$tree" --remote origin --push
run gh api -X PATCH "repos/$OWNER/$NAME" -F delete_branch_on_merge=true -F has_wiki=false
if [ "$VIS" = public ]; then
  run gh api -X PUT "repos/$OWNER/$NAME/actions/permissions" -F enabled=true -f allowed_actions=selected
  run gh api -X PUT "repos/$OWNER/$NAME/actions/permissions/selected-actions" -F github_owned_allowed=true \
    -F verified_allowed=false -f "patterns_allowed[]=$OWNER/ci-recipes/.github/workflows/identifier-guard.yml@*"
else
  run gh api -X PUT "repos/$OWNER/$NAME/actions/permissions" -F enabled=true -f allowed_actions=all
fi
run gh api -X PUT "repos/$OWNER/$NAME/actions/permissions/workflow" -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false

runner_note=" PUBLIC repo: never a self-hosted runner."; secrets_note=" PUBLIC repo: none, ever."
if [ "$VIS" = private ]; then
  runner_note=" To use your own Linux runner, pass runs-on to the guard caller and set runs-on in your jobs."; secrets_note=""
fi
cat <<EOF

=== Still to do by the owner
1. Clone it and enable the hook once per clone: git config core.hooksPath .githooks
2. Fill in README.md (what it is, run, deploy), CLAUDE.md (build and test gates), docs/architecture.md.
3. Secrets and variables: add only what a deploy needs (gh secret set / gh variable set).${secrets_note}
4. CodeRabbit: add the repo to the CodeRabbit GitHub app's repository list if it should review PRs.
5. CI runner: jobs run on ubuntu-latest.${runner_note}
6. Memory: start ~/.claude/projects/<repo-slug>/memory/MEMORY.md for status and hand-overs (never in the repo).
EOF
[ "$DRY" = 1 ] && echo "(dry run: nothing created; tree left at $tree)"
exit 0
