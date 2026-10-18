#!/usr/bin/env bash
# org-move/move-repo.sh: move one repository from the personal account to the organization as ONE commit holding the
# prepared tree of main (no history, no tags, no PRs or issues). Each phase is idempotent, fails closed and prints
# evidence (hashes, counts, file:line; never matched text). Run the phases in order, one repo at a time:
#
#   move-repo.sh preflight <repo>   org reachable, personal main is the default branch, no open PRs (listed)
#   move-repo.sh bundle    <repo>   mirror clone -> git bundle of all refs (verified; size + sha256)
#   move-repo.sh prepare   <repo>   main's tree with owner references rewritten; prints the diff stat and the tree hash
#   move-repo.sh certify   <repo>   guard (whole files), phrase sweep, binary + standard check on the prepared tree
#   move-repo.sh create    <repo>   empty private repo in the org (set its secrets BEFORE push: main may deploy)
#   move-repo.sh push      <repo>   one orphan commit "Initial import" -> main; root tree must equal the prepared tree
#   move-repo.sh tag       <repo> <tag>   re-cut <tag> on the new root (only where the plan says so)
#   move-repo.sh settings  <repo>   merge settings copied from the personal repo; reads back default branch + alerts
#   move-repo.sh archive   <repo> --proven   archive the personal repo (only after its deploy is proven on the org repo)
#
# Between certify and create, an independent reviewer reads the prepared tree (README, CLAUDE.md, docs/, workflows,
# fixtures, the phrase-sweep hits) for anything that states or implies an event or a provenance instead of a rule.
# Anything found is fixed in a PR on the personal repo, then prepare + certify run again. ci-recipes itself is archived only
# after the last repo has moved (callers in the personal account still pin it): archive refuses it without --last.
#
# Rewrites (prepare): ccmenshealth/ci-recipes -> ORG/ci-recipes everywhere (the guard copy, hook and caller stay
# byte-identical to ORG/ci-recipes); ccmenshealth/<repo> -> ORG/<repo> (self-references); ghcr.io/ccmenshealth/ ->
# ghcr.io/ORG/; "the ccmenshealth repo standard" -> "the ORG repo standard". For ci-recipes itself every
# "ccmenshealth" that is not part of the ccmenshealth.com domain becomes ORG. Everything else stays byte-identical;
# remaining ccmenshealth/<other repo> references are listed (file:line) for the record.
#
# Requires: git, gh (logged in as the owner), python3, perl, shasum. Bash 3.2 compatible (macOS /bin/bash).
# Env: ORG (default ccmhinc), OLD_OWNER (default ccmenshealth), WORK (scratch dir), BUNDLES, EVIDENCE,
#      GUARD_TAG (default v1.6), CANON_OWNER (owner whose ci-recipes@GUARD_TAG runs the certification; default OLD_OWNER).
set -euo pipefail

ORG="${ORG:-ccmhinc}"
OLD_OWNER="${OLD_OWNER:-ccmenshealth}"
GUARD_TAG="${GUARD_TAG:-v1.6}"
CANON_OWNER="${CANON_OWNER:-$OLD_OWNER}"
DATE="${MOVE_DATE:-$(date +%Y%m%d)}"
WORK="${WORK:-${TMPDIR:-/tmp}/org-move}"
BUNDLES="${BUNDLES:-$HOME/Developer/output/org-move-bundles}"
EVIDENCE_ROOT="${EVIDENCE:-$HOME/Developer/output/org-move-evidence}"
PHRASES='\bPHI\b|protected health|patient data|\bleak|exposure|exposed|breach|incident|redact|scrub|saniti[sz]|purge|real patient|real data|actual patient|clean-?up|history rewrite|squash|removed from history|\bOCR\b|identifier guard'

die() { echo "FAIL: $*" >&2; exit 1; }
say() { echo "== $*"; }

usage() { sed -n '2,25p' "$0" >&2; exit 2; }
[ $# -ge 2 ] || usage
PHASE="$1"; REPO="$2"; shift 2
[[ "$REPO" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad repo name '$REPO'"
OLD="$OLD_OWNER/$REPO"; NEW="$ORG/$REPO"
EV="$EVIDENCE_ROOT/$REPO-$DATE"; PREP="$WORK/$REPO-prepared"
mkdir -p "$WORK" "$EV"
# Every phase's output also goes to its evidence file.
exec > >(tee -a "$EV/$PHASE.txt") 2>&1
say "$PHASE $OLD -> $NEW ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

repo_exists() { gh api "repos/$1" --silent >/dev/null 2>&1; }
old_main_sha() { gh api "repos/$OLD/commits/main" --jq .sha; }

prepared_tree() {
  [ -f "$PREP.tree" ] || die "no prepared tree; run: $0 prepare $REPO"
  cat "$PREP.tree"
}

# The prepared tree is only valid while personal main has not moved since prepare.
check_prepared_current() {
  local want; want="$(cat "$PREP.source" 2>/dev/null || true)"
  [ "$want" = "$(old_main_sha)" ] || die "personal main moved since prepare (prepared from ${want:-nothing}); re-run prepare + certify"
}

phase_preflight() {
  gh api "orgs/$ORG" --jq '"org \(.login): plan \(.plan.name), 2FA required \(.two_factor_requirement_enabled), base permission \(.default_repository_permission)"' \
    || die "gh api orgs/$ORG failed with the current token (OAuth app may need org approval)"
  local def arch
  def="$(gh api "repos/$OLD" --jq .default_branch)"; arch="$(gh api "repos/$OLD" --jq .archived)"
  [ "$def" = main ] || die "$OLD default branch is '$def', not main"
  [ "$arch" = false ] || die "$OLD is already archived"
  echo "$OLD default branch main at $(old_main_sha)"
  local prs; prs="$(gh pr list -R "$OLD" --state open --json number,title,isDraft --jq '.[] | "#\(.number) draft=\(.isDraft) \(.title)"')"
  if [ -n "$prs" ]; then echo "open PRs (the lead decides; they do not move):"; echo "$prs"; die "open PRs on $OLD"; fi
  echo "open PRs: 0"
  if repo_exists "$NEW"; then echo "$NEW already exists (later phases check its state)"; else echo "$NEW does not exist yet"; fi
  say "preflight OK"
}

phase_bundle() {
  local b="$BUNDLES/$REPO-$DATE.bundle" m="$WORK/$REPO-mirror.git"
  local v="$WORK/bundle-verify.git"   # empty repo: a full bundle has no prerequisites, so this proves it is self-contained
  mkdir -p "$BUNDLES"; [ -d "$v" ] || git init -q --bare "$v"
  if [ -f "$b" ] && git -C "$v" bundle verify -q "$b" 2>/dev/null \
     && [ "$(git bundle list-heads "$b" refs/heads/main | cut -d' ' -f1)" = "$(old_main_sha)" ]; then
    say "bundle exists and verifies: $b"
  else
    [ -f "$b" ] && echo "existing bundle is stale or unverifiable; rebuilding"
    rm -rf "$m"; git clone -q --mirror "https://github.com/$OLD.git" "$m"
    git -C "$m" bundle create "$b" --all
    git -C "$v" bundle verify -q "$b" || die "bundle verify failed"
  fi
  echo "refs in bundle: $(git bundle list-heads "$b" | wc -l | tr -d ' ')"
  echo "main in bundle: $(git bundle list-heads "$b" refs/heads/main | cut -d' ' -f1)"
  echo "size bytes: $(wc -c < "$b" | tr -d ' ')"
  echo "sha256: $(shasum -a 256 "$b" | cut -d' ' -f1)"
  echo "path: $b"
  say "bundle OK"
}

phase_prepare() {
  local src; src="$(old_main_sha)"
  rm -rf "$PREP"; git clone -q --no-tags --single-branch --branch main "https://github.com/$OLD.git" "$PREP"
  [ "$(git -C "$PREP" rev-parse HEAD)" = "$src" ] || die "clone HEAD differs from personal main"
  git -C "$PREP" status --porcelain | grep -q . && die "fresh clone is dirty"
  (
    cd "$PREP"
    local files
    if [ "$REPO" = ci-recipes ]; then
      files="$(git grep -lI "$OLD_OWNER" || true)"
      [ -z "$files" ] || printf '%s\n' "$files" | while IFS= read -r f; do
        O="$OLD_OWNER" N="$ORG" perl -pi -e 's/(?<![\w@.-])\Q$ENV{O}\E(?!\.com|[\w-])/$ENV{N}/g' "$f"
      done
    else
      files="$(git grep -lI -E "$OLD_OWNER/(ci-recipes|$REPO)|ghcr\.io/$OLD_OWNER/|the $OLD_OWNER repo standard" || true)"
      [ -z "$files" ] || printf '%s\n' "$files" | while IFS= read -r f; do
        O="$OLD_OWNER" N="$ORG" R="$REPO" perl -pi -e '
          s{\Q$ENV{O}\E/ci-recipes(?![\w-])}{$ENV{N}/ci-recipes}g;
          s{\Q$ENV{O}\E/\Q$ENV{R}\E(?![\w-])}{$ENV{N}/$ENV{R}}g;
          s{ghcr\.io/\Q$ENV{O}\E/}{ghcr.io/$ENV{N}/}g;
          s{the \Q$ENV{O}\E repo standard}{the $ENV{N} repo standard}g;' "$f"
      done
    fi
    if [ "$REPO" != ci-recipes ] && [ -f .github/workflows/identifier-guard.yml ]; then
      # The caller becomes ORG/ci-recipes@GUARD_TAG's own caller (standard variant when the repo has opted in), pinned to
      # GUARD_TAG. Refused if the repo's caller differs from it in anything but comments, owner and pin.
      local canon="$WORK/canon-$ORG-ci-recipes-$GUARD_TAG" src_caller
      [ -d "$canon" ] || git -c advice.detachedHead=false clone -q --depth 1 --branch "$GUARD_TAG" "https://github.com/$ORG/ci-recipes.git" "$canon"
      src_caller="$canon/identifier-guard/caller-workflow.yml"
      [ -f .repo-standard.json ] && src_caller="$canon/template/.github/workflows/identifier-guard.yml"
      strip() { sed -E -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' -e 's#(identifier-guard\.yml)@[^[:space:]]*#\1@PIN#' "$1"; }
      diff <(strip .github/workflows/identifier-guard.yml) <(strip "$src_caller") \
        || die "the repo's identifier-guard caller differs from $ORG/ci-recipes@$GUARD_TAG beyond comments and pin"
      sed -E "s#^( *uses: $ORG/ci-recipes/\.github/workflows/identifier-guard\.yml)@[^[:space:]]*#\1@$GUARD_TAG#" "$src_caller" > .github/workflows/identifier-guard.yml
      grep -qE "^ *uses: $ORG/ci-recipes/\.github/workflows/identifier-guard\.yml@$GUARD_TAG([[:space:]]|$)" .github/workflows/identifier-guard.yml \
        || die "caller pin not written"
      cmp -s scripts/identifier-guard.py "$canon/identifier-guard/identifier-guard.py" || die "scripts/identifier-guard.py differs from $ORG/ci-recipes@$GUARD_TAG after the rewrite"
      cmp -s .githooks/pre-commit "$canon/identifier-guard/pre-commit" || die ".githooks/pre-commit differs from $ORG/ci-recipes@$GUARD_TAG"
      echo "caller: $ORG/ci-recipes@$GUARD_TAG's $( [ -f .repo-standard.json ] && echo standard || echo plain ) caller; guard script and hook byte-identical to it"
    fi
    git add -A
    say "rewrite diff (stat)"; git diff --cached --stat | cat
    git diff --cached --summary | grep -E 'mode change|create|delete' && die "rewrite changed modes or file set"
    echo "remaining $OLD_OWNER references outside the .com domain (file:line):"
    git grep -nI -E "(^|[^@.[:alnum:]-])$OLD_OWNER($|[^.[:alnum:]-]|\.[^c])" | cut -d: -f1,2 || echo "  none"
  )
  local tree; tree="$(git -C "$PREP" write-tree)"
  echo "$tree" > "$PREP.tree"; echo "$src" > "$PREP.source"
  echo "source main: $src  source tree: $(git -C "$PREP" rev-parse 'HEAD^{tree}')"
  echo "prepared tree: $tree"
  say "prepare OK"
}

phase_certify() {
  check_prepared_current
  local tree canon="$WORK/canon-$CANON_OWNER-ci-recipes-$GUARD_TAG" rc=0 n
  tree="$(prepared_tree)"
  [ "$(git -C "$PREP" write-tree)" = "$tree" ] || die "prepared index no longer matches the recorded tree"
  [ -d "$canon" ] || git -c advice.detachedHead=false clone -q --depth 1 --branch "$GUARD_TAG" "https://github.com/$CANON_OWNER/ci-recipes.git" "$canon"
  echo "canonical: $CANON_OWNER/ci-recipes@$GUARD_TAG commit $(git -C "$canon" rev-parse HEAD), guard blob $(git -C "$canon" hash-object identifier-guard/identifier-guard.py)"
  echo "tree certified: $tree"
  (
    cd "$PREP"
    say "1. identifier guard, whole files (regular files only)"
    git ls-files -z | while IFS= read -r -d '' f; do [ -f "$f" ] && [ ! -L "$f" ] && printf '%s\0' "$f"; done \
      | xargs -0 python3 "$canon/identifier-guard/identifier-guard.py" --files && echo "guard: 0 findings" || { echo "guard: findings above (exit $?)"; exit 11; }
  ) || rc=1
  (
    cd "$PREP"
    say "1b. binary files (git's own text detection: index eol 'i/-text') and the repo-standard check"
    n=0
    while IFS= read -r f; do echo "binary: $f ($(wc -c < "$f" | tr -d ' ') bytes)"; n=$((n+1)); done \
      < <(git ls-files --eol | awk -F'\t' '$1 ~ /^i\/-text/ {print $2}')
    echo "binary files: $n (each must match asset_paths in .repo-standard.json and be looked at)"
    if [ -f .repo-standard.json ]; then
      local canon_guard="$canon/identifier-guard/identifier-guard.py"
      [ -f scripts/identifier-guard.py ] && canon_guard="scripts/identifier-guard.py"   # owner line rewritten; rules identical
      python3 "$canon/repo-standard/check.py" --root . --canonical "$canon_guard" && echo "repo-standard: pass" || { echo "repo-standard: FAIL"; exit 12; }
    else
      echo "repo-standard: no .repo-standard.json (not a standard repo; binaries listed above are the check)"
    fi
  ) || rc=1
  (
    cd "$PREP"
    say "2. phrase sweep (file:line and term only)"
    local hits
    hits="$(git grep -nI -i -o -E "$PHRASES" | awk -F: '{print $1":"$2"  ["tolower($3)"]"}' | sort -u || true)"
    if [ -n "$hits" ]; then printf '%s\n' "$hits"; fi
    echo "phrase hits: $(printf '%s' "$hits" | grep -c . || true) lines (each must be prevention language or be rewritten before the push)"
    echo "repo description: $(gh api "repos/$OLD" --jq '.description // ""' | grep -o -i -E "$PHRASES" | tr '\n' ' ' || true)(terms only)"
  )
  [ "$rc" = 0 ] || die "certification checks failed; fix in a PR on $OLD, then prepare + certify again"
  say "certify (machine part) OK; the independent reviewer's inference read is the remaining gate"
}

phase_create() {
  if repo_exists "$NEW"; then
    [ "$(gh api "repos/$NEW" --jq .private)" = true ] || die "$NEW exists and is not private"
    say "$NEW exists (private)"
  else
    local desc; desc="$(gh api "repos/$OLD" --jq '.description // ""' | O="$OLD_OWNER" N="$ORG" perl -pe 's/(?<![\w@.-])\Q$ENV{O}\E(?!\.com|[\w-])/$ENV{N}/g')"
    gh repo create "$NEW" --private --disable-wiki --description "$desc"
  fi
  gh api "repos/$NEW" --jq '"\(.full_name) private=\(.private) size=\(.size) default=\(.default_branch)"'
  say "create OK (set secrets/variables now, before push)"
}

phase_push() {
  check_prepared_current
  local tree commit head msgfile
  tree="$(prepared_tree)"
  head="$(gh api "repos/$NEW/commits/main" --jq .sha 2>/dev/null || true)"
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || head=""   # gh prints the error body on 404/409 (empty repo)
  if [ -n "$head" ]; then
    local t; t="$(gh api "repos/$NEW/git/commits/$head" --jq .tree.sha)"
    [ "$t" = "$tree" ] || die "$NEW main exists with tree $t, not the prepared $tree"
    [ "$(gh api "repos/$NEW/git/commits/$head" --jq '.parents | length')" = 0 ] || die "$NEW main is not a root commit"
    say "$NEW main already holds the prepared tree: $head"
    return 0
  fi
  local an ae; an="$(git -C "$PREP" log -1 --format=%an)"; ae="$(git -C "$PREP" log -1 --format=%ae)"
  msgfile="$WORK/$REPO-msg.txt"
  printf 'Initial import\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n' > "$msgfile"
  commit="$(cd "$PREP" && GIT_AUTHOR_NAME="$an" GIT_AUTHOR_EMAIL="$ae" GIT_COMMITTER_NAME="$an" GIT_COMMITTER_EMAIL="$ae" git commit-tree "$tree" -F "$msgfile")"
  git -C "$PREP" push --no-tags "https://github.com/$NEW.git" "$commit:refs/heads/main"
  local rtree; rtree="$(gh api "repos/$NEW/git/commits/$commit" --jq .tree.sha)"
  [ "$rtree" = "$tree" ] || die "remote root tree $rtree != prepared $tree"
  echo "root commit: $commit"
  echo "root tree:   $rtree (= prepared tree)"
  echo "parents: $(gh api "repos/$NEW/git/commits/$commit" --jq '.parents | length')"
  echo "tags on $NEW: $(gh api "repos/$NEW/tags" --jq 'length')"
  say "push OK"
}

phase_tag() {
  local tag="${1:-}"; [ -n "$tag" ] || die "usage: $0 tag $REPO <tag>"
  local head; head="$(gh api "repos/$NEW/commits/main" --jq .sha)"
  [ "$(gh api "repos/$NEW/git/commits/$head" --jq '.parents | length')" = 0 ] || die "$NEW main is not the root commit; re-cut only on the root"
  local cur; cur="$(gh api "repos/$NEW/git/ref/tags/$tag" --jq .object.sha 2>/dev/null || true)"
  [[ "$cur" =~ ^[0-9a-f]{40}$ ]] || cur=""
  if [ -n "$cur" ]; then [ "$cur" = "$head" ] || die "$tag exists on $NEW at $cur, not the root $head"; say "$tag already on the root"; return 0; fi
  gh api -X POST "repos/$NEW/git/refs" -f "ref=refs/tags/$tag" -f "sha=$head" --jq '"\(.ref) -> \(.object.sha) (lightweight)"'
  echo "tags on $NEW: $(gh api "repos/$NEW/tags" --jq '[.[].name] | join(",")')"
  say "tag OK"
}

phase_settings() {
  local s
  s="$(gh api "repos/$OLD" --jq '"-F allow_squash_merge=\(.allow_squash_merge) -F allow_merge_commit=\(.allow_merge_commit) -F allow_rebase_merge=\(.allow_rebase_merge) -F delete_branch_on_merge=\(.delete_branch_on_merge) -F allow_auto_merge=\(.allow_auto_merge) -F has_projects=\(.has_projects) -F has_issues=\(.has_issues)"')"
  # shellcheck disable=SC2086  # $s is a list of -F flags built above from booleans only
  gh api -X PATCH "repos/$NEW" -f default_branch=main $s --jq '"default=\(.default_branch) squash=\(.allow_squash_merge) merge=\(.allow_merge_commit) rebase=\(.allow_rebase_merge) delete_branch=\(.delete_branch_on_merge) auto_merge=\(.allow_auto_merge) issues=\(.has_issues) wiki=\(.has_wiki) private=\(.private)"'
  if gh api "repos/$NEW/vulnerability-alerts" --silent 2>/dev/null; then echo "dependabot alerts: on (204)"; else echo "dependabot alerts: OFF (404); enabling"; gh api -X PUT "repos/$NEW/vulnerability-alerts" --silent && echo "dependabot alerts: on (PUT 204)"; fi
  echo "actions secrets: $(gh api "repos/$NEW/actions/secrets" --jq '[.secrets[].name] | join(",")')"
  echo "hooks: $(gh api "repos/$NEW/hooks" --jq length)"
  say "settings OK"
}

phase_archive() {
  [ "${1:-}" = --proven ] || die "archive needs --proven (deploy proven on $NEW first)"
  if [ "$REPO" = ci-recipes ] && [ "${2:-}" != --last ]; then die "ci-recipes is archived only after the last repo has moved (--proven --last)"; fi
  repo_exists "$NEW" || die "$NEW does not exist"
  if [ "$(gh api "repos/$OLD" --jq .archived)" = true ]; then say "$OLD already archived"; else gh repo archive "$OLD" --yes; fi
  gh api "repos/$OLD" --jq '"\(.full_name) archived=\(.archived) updated_at=\(.updated_at)"'
  echo "archived at: $(date -u +%Y-%m-%dT%H:%M:%SZ) (30-day soak starts)"
  say "archive OK"
}

case "$PHASE" in
  preflight) phase_preflight ;;
  bundle) phase_bundle ;;
  prepare) phase_prepare ;;
  certify) phase_certify ;;
  create) phase_create ;;
  push) phase_push ;;
  tag) phase_tag "$@" ;;
  settings) phase_settings ;;
  archive) phase_archive "$@" ;;
  *) usage ;;
esac
