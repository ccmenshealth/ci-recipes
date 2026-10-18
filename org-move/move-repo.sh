#!/usr/bin/env bash
# org-move/move-repo.sh: move one repository from the personal account to the organization as ONE commit holding the
# prepared tree of main (no history, no tags, no PRs or issues). Each phase is idempotent, fails closed and prints
# evidence (hashes, counts, file:line; never matched text). Run the phases in order, one repo at a time:
#
#   move-repo.sh preflight <repo>   org reachable, personal SRC_BRANCH is the default branch, no open PRs (listed)
#   move-repo.sh bundle    <repo>   mirror clone -> git bundle of all refs (verified; size + sha256)
#   move-repo.sh prepare   <repo>   main's tree with owner references rewritten; prints the diff stat and the tree hash
#   move-repo.sh certify   <repo>   guard (whole files), phrase sweep, binary + standard check on the prepared tree
#   move-repo.sh create    <repo>   empty private repo in the org (set its secrets BEFORE push: main may deploy)
#   move-repo.sh oidc      <repo> <app>   add a twin Azure federated credential (subject repo:ORG/<repo>:...) for every
#                                         credential of app registration <app> whose subject is repo:OLD_OWNER/<repo>:...
#                                         (PLAN_ONLY=1 prints the plan and creates nothing)
#   move-repo.sh azure-secrets <repo> <app>   AZURE_CLIENT_ID (<app>'s appId), AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
#                                         from az, piped into gh secret set on the org repo (only names the personal repo has)
#   move-repo.sh swa-token <repo> <secret> <static web app> <resource group>   deployment token from az into gh secret set
#   move-repo.sh bindings  <repo>   secret, variable and environment names: personal vs org; fails while any is missing
#   move-repo.sh push      <repo>   one orphan commit "Initial import" -> main; root tree must equal the prepared tree
#   move-repo.sh tag       <repo> <tag>   re-cut <tag> on the new root (only where the plan says so)
#   move-repo.sh settings  <repo>   merge settings copied from the personal repo; reads back default branch + alerts
#   move-repo.sh archive   <repo> --proven   archive the personal repo (only after its deploy is proven on the org repo)
#
# SRC_BRANCH (default main) is the personal branch that moves; the org repo's branch is always main.
# The az phases never print a secret value (lengths and names only); old federated credentials stay until the archive.
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
# Requires: git, gh (logged in as the owner), python3, perl, shasum; az (logged in) for the az phases.
# Bash 3.2 compatible (macOS /bin/bash).
# Env: ORG (default ccmhinc), OLD_OWNER (default ccmenshealth), SRC_BRANCH (default main), WORK (scratch dir), BUNDLES,
#      EVIDENCE, GUARD_TAG (default v1.7), CANON_OWNER (owner whose ci-recipes@GUARD_TAG runs the certification; default OLD_OWNER).
set -euo pipefail
# xtrace would print secret values (az phases) into the evidence file.
case "$-" in *x*) echo "FAIL: refusing to run under xtrace (bash -x / set -x)" >&2; exit 1 ;; esac

ORG="${ORG:-ccmhinc}"
OLD_OWNER="${OLD_OWNER:-ccmenshealth}"
GUARD_TAG="${GUARD_TAG:-v1.7}"
SRC_BRANCH="${SRC_BRANCH:-main}"
[[ "$SRC_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "FAIL: bad SRC_BRANCH" >&2; exit 1; }
CANON_OWNER="${CANON_OWNER:-}"   # set below: OLD_OWNER for ci-recipes itself, ORG for every later repo
DATE="${MOVE_DATE:-$(date +%Y%m%d)}"
WORK="${WORK:-${TMPDIR:-/tmp}/org-move}"
BUNDLES="${BUNDLES:-$HOME/Developer/output/org-move-bundles}"
EVIDENCE_ROOT="${EVIDENCE:-$HOME/Developer/output/org-move-evidence}"
# Perl-compatible (git grep -P): on macOS, git grep -E silently treats \b as matching nothing.
PHRASES='\bPHI\b|protected health|patient data|\bleak|exposure|exposed|breach|incident|redact|scrub|saniti[sz]|purge|real patient|real data|actual patient|clean-?up|history rewrite|squash|removed from history|\bOCR\b|identifier guard'

die() { echo "FAIL: $*" >&2; exit 1; }
say() { echo "== $*"; }

usage() { sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' >&2; exit 2; }
[ $# -ge 2 ] || usage
PHASE="$1"; REPO="$2"; shift 2
[[ "$REPO" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad repo name '$REPO'"
OLD="$OLD_OWNER/$REPO"; NEW="$ORG/$REPO"
if [ -z "$CANON_OWNER" ]; then if [ "$REPO" = ci-recipes ]; then CANON_OWNER="$OLD_OWNER"; else CANON_OWNER="$ORG"; fi; fi
EV="$EVIDENCE_ROOT/$REPO-$DATE"; PREP="$WORK/$REPO-prepared"
mkdir -p "$WORK" "$EV"
# Every phase's output also goes to its evidence file.
exec > >(tee -a "$EV/$PHASE.txt") 2>&1
say "$PHASE $OLD -> $NEW ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

repo_exists() { gh api "repos/$1" --silent >/dev/null 2>&1; }
# Head of the personal SRC_BRANCH (branches/ endpoint: a tag of the same name never matches).
src_head_sha() { gh api "repos/$OLD/branches/$SRC_BRANCH" --jq .commit.sha; }

prepared_tree() {
  [ -f "$PREP.tree" ] || die "no prepared tree; run: $0 prepare $REPO"
  cat "$PREP.tree"
}

# The prepared tree is only valid while personal main has not moved since prepare.
check_prepared_current() {
  local want; want="$(cat "$PREP.source" 2>/dev/null || true)"
  [ -n "$want" ] || die "no recorded source commit; run: $0 prepare $REPO"
  [ "$want" = "$(src_head_sha)" ] || die "personal $SRC_BRANCH moved since prepare (prepared from ${want:-nothing}); re-run prepare + certify"
}

# git grep that fails closed: rc 0 (hits) and 1 (none) are answers; anything else (bad pattern, not a repo) stops.
ggrep() { local rc=0; git grep "$@" || rc=$?; [ "$rc" -le 1 ] || die "git grep failed (exit $rc)"; }

# The prepared index is what push sends; certify and push refuse a working tree that differs from it.
check_prepared_clean() {
  git -C "$PREP" diff --quiet || die "prepared working tree differs from its index; re-run prepare"
  [ -z "$(git -C "$PREP" ls-files --others --exclude-standard)" ] || die "untracked files in the prepared tree; re-run prepare"
  [ "$(git -C "$PREP" write-tree)" = "$(prepared_tree)" ] || die "prepared index no longer matches the recorded tree"
}

# The phrase sweep must find a known hit, or the pattern engine is not doing what it says.
self_test_sweep() {
  local t="$WORK/sweep-selftest.txt"
  printf 'x PHI y\nleak\nOCR\nidentifier guard\n' > "$t"
  [ "$(cd "$WORK" && git grep --no-index -h -c -i -P -e "$PHRASES" -- "$(basename "$t")" 2>/dev/null)" = 4 ] || die "phrase sweep self-test failed"
}

phase_preflight() {
  gh api "orgs/$ORG" --jq '"org \(.login): plan \(.plan.name), 2FA required \(.two_factor_requirement_enabled), base permission \(.default_repository_permission)"' \
    || die "gh api orgs/$ORG failed with the current token (OAuth app may need org approval)"
  local def arch
  def="$(gh api "repos/$OLD" --jq .default_branch)"; arch="$(gh api "repos/$OLD" --jq .archived)"
  [ "$def" = "$SRC_BRANCH" ] || die "$OLD default branch is '$def', not $SRC_BRANCH (SRC_BRANCH=$def moves it; the org branch is main)"
  [ "$arch" = false ] || die "$OLD is already archived"
  echo "$OLD default branch $SRC_BRANCH at $(src_head_sha) (org branch: main)"
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
     && [ "$(git bundle list-heads "$b" "refs/heads/$SRC_BRANCH" | cut -d' ' -f1)" = "$(src_head_sha)" ]; then
    say "bundle exists and verifies: $b"
  else
    [ -f "$b" ] && echo "existing bundle is stale or unverifiable; rebuilding"
    rm -rf "$m"; git clone -q --mirror "https://github.com/$OLD.git" "$m"
    git -C "$m" bundle create "$b" --all
    git -C "$v" bundle verify -q "$b" || die "bundle verify failed"
  fi
  echo "refs in bundle: $(git bundle list-heads "$b" | wc -l | tr -d ' ')"
  echo "$SRC_BRANCH in bundle: $(git bundle list-heads "$b" "refs/heads/$SRC_BRANCH" | cut -d' ' -f1)"
  echo "size bytes: $(wc -c < "$b" | tr -d ' ')"
  echo "sha256: $(shasum -a 256 "$b" | cut -d' ' -f1)"
  echo "path: $b"
  say "bundle OK"
}

phase_prepare() {
  local src; src="$(src_head_sha)"
  rm -rf "$PREP"; git clone -q --no-tags --single-branch --branch "$SRC_BRANCH" "https://github.com/$OLD.git" "$PREP"
  [ "$(git -C "$PREP" rev-parse HEAD)" = "$src" ] || die "clone HEAD differs from personal $SRC_BRANCH"
  git -C "$PREP" status --porcelain | grep -q . && die "fresh clone is dirty"
  (
    cd "$PREP"
    local files
    if [ "$REPO" = ci-recipes ]; then
      files="$(ggrep -lI "$OLD_OWNER")"
      [ -z "$files" ] || printf '%s\n' "$files" | while IFS= read -r f; do
        O="$OLD_OWNER" N="$ORG" perl -pi -e 's/(?<![\w@.-])\Q$ENV{O}\E(?!\.[a-z]|[\w-])/$ENV{N}/g' "$f"
      done
    else
      files="$(ggrep -lI -E "$OLD_OWNER/(ci-recipes|$REPO)|ghcr\.io/$OLD_OWNER/|the $OLD_OWNER repo standard")"
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
      strip() { sed -E -e 's/(^|[[:space:]])#.*$//' -e '/^[[:space:]]*$/d' -e 's#(identifier-guard\.yml)@[^[:space:]]*#\1@PIN#' "$1"; }
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
    local refs; refs="$(ggrep -z -nI -P "(?<![@.\w-])$OLD_OWNER(?!\.[a-z]|[\w-])" | perl -ne 'my ($f, $l) = split /\0/; print "$f:$l\n"')"
    printf '%s\n' "${refs:-  none}"
    if [ "$SRC_BRANCH" != main ]; then
      echo "references to the branch name '$SRC_BRANCH' (the org branch is main; file:line, for the record):"
      local brefs; brefs="$(ggrep -nI -w -F -e "$SRC_BRANCH" | cut -d: -f1,2)"
      printf '%s\n' "${brefs:-  none}"
      # A workflow filter on the old name would stop the org repo's main from triggering anything.
      if printf '%s\n' "$brefs" | grep -q '^\.github/workflows/'; then die "workflow files name '$SRC_BRANCH'; change them to main on $OLD first"; fi
    fi
  )
  local tree; tree="$(git -C "$PREP" write-tree)"
  echo "$tree" > "$PREP.tree"; echo "$src" > "$PREP.source"
  echo "source $SRC_BRANCH: $src  source tree: $(git -C "$PREP" rev-parse 'HEAD^{tree}')"
  echo "prepared tree: $tree"
  say "prepare OK"
}

phase_certify() {
  check_prepared_current
  check_prepared_clean; self_test_sweep
  local tree canon="$WORK/canon-$CANON_OWNER-ci-recipes-$GUARD_TAG" rc=0 n
  tree="$(prepared_tree)"
  [ -d "$canon" ] || git -c advice.detachedHead=false clone -q --depth 1 --branch "$GUARD_TAG" "https://github.com/$CANON_OWNER/ci-recipes.git" "$canon"
  echo "canonical: $CANON_OWNER/ci-recipes@$GUARD_TAG commit $(git -C "$canon" rev-parse HEAD), guard blob $(git -C "$canon" hash-object identifier-guard/identifier-guard.py)"
  echo "tree certified: $tree"
  (
    cd "$PREP"
    say "1. identifier guard, whole files (regular files only)"
    git ls-files -z | while IFS= read -r -d '' f; do if [ -f "$f" ] && [ ! -L "$f" ]; then printf '%s\0' "$f"; fi; done \
      | xargs -0 python3 "$canon/identifier-guard/identifier-guard.py" --files && echo "guard: 0 findings" || { echo "guard: findings above (exit $?)"; exit 11; }
  ) || rc=1
  (
    cd "$PREP"
    say "1b. binary files (git's own text detection: index eol 'i/-text') and the repo-standard check"
    n=0
    local list; list="$(git -c core.quotePath=false ls-files --eol -z | perl -0 -ne 'chomp; my ($meta, $f) = split /\t/, $_, 2; print "$f\n" if $meta =~ m{^i/-text}')" \
      || die "binary listing failed"
    while IFS= read -r f; do [ -n "$f" ] || continue; echo "binary: $f ($(wc -c < "$f" | tr -d ' ') bytes)"; n=$((n+1)); done <<< "$list"
    echo "binary files: $n (each must match asset_paths in .repo-standard.json and be looked at)"
    if [ -f .repo-standard.json ]; then
      # The prepared copy carries the ORG owner line, so compare against ORG/ci-recipes@GUARD_TAG (cloned by prepare).
      local canon_guard="$WORK/canon-$ORG-ci-recipes-$GUARD_TAG/identifier-guard/identifier-guard.py"
      [ -f "$canon_guard" ] || canon_guard="$canon/identifier-guard/identifier-guard.py"
      python3 "$canon/repo-standard/check.py" --root . --canonical "$canon_guard" && echo "repo-standard: pass" || { echo "repo-standard: FAIL"; exit 12; }
    else
      echo "repo-standard: no .repo-standard.json (not a standard repo; binaries listed above are the check)"
    fi
  ) || rc=1
  (
    cd "$PREP"
    say "2. phrase sweep (file:line and term only)"
    local hits
    hits="$(ggrep -z -nI -i -o -P "$PHRASES" | perl -ne 'chomp; my ($f, $l, $m) = split /\0/; print "$f:$l  [" . lc($m) . "]\n"' | sort -u)"
    if [ -n "$hits" ]; then printf '%s\n' "$hits"; fi
    echo "phrase hits: $(printf '%s' "$hits" | cut -d' ' -f1 | sort -u | grep -c . || true) lines (each must be prevention language or be rewritten before the push)"
    echo "repo description: $(gh api "repos/$OLD" --jq '.description // ""' | perl -ne 'print lc($&) . " " while /'"$PHRASES"'/gi' | tr '\n' ' ' || true)(terms only)"
  )
  [ "$rc" = 0 ] || die "certification checks failed; fix in a PR on $OLD, then prepare + certify again"
  say "certify (machine part) OK; the independent reviewer's inference read is the remaining gate"
}

phase_create() {
  if repo_exists "$NEW"; then
    [ "$(gh api "repos/$NEW" --jq .private)" = true ] || die "$NEW exists and is not private"
    say "$NEW exists (private)"
  else
    local desc; desc="$(gh api "repos/$OLD" --jq '.description // ""' | O="$OLD_OWNER" N="$ORG" perl -pe 's/(?<![\w@.-])\Q$ENV{O}\E(?!\.[a-z]|[\w-])/$ENV{N}/g')"
    gh repo create "$NEW" --private --disable-wiki --description="$desc"
  fi
  gh api "repos/$NEW" --jq '"\(.full_name) private=\(.private) size=\(.size) default=\(.default_branch)"'
  say "create OK (set secrets/variables now, before push)"
}

phase_push() {
  check_prepared_current; check_prepared_clean
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
  [ "$(gh api "repos/$NEW/git/commits/$commit" --jq '.parents | length')" = 0 ] || die "root commit has parents"
  [ "$(gh api "repos/$NEW/tags" --jq 'length')" = 0 ] || die "tags exist on $NEW after the push"
  echo "parents: 0; tags on $NEW: 0"
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
  if gh api "repos/$NEW/vulnerability-alerts" --silent 2>/dev/null; then echo "dependabot alerts: on (204)"; else echo "dependabot alerts: OFF (404); enabling"; gh api -X PUT "repos/$NEW/vulnerability-alerts" --silent || die "enabling Dependabot alerts failed"; echo "dependabot alerts: on (PUT 204)"; fi
  echo "actions secrets: $(gh api "repos/$NEW/actions/secrets" --jq '[.secrets[].name] | join(",")')"
  echo "hooks: $(gh api "repos/$NEW/hooks" --jq length)"
  say "settings OK"
}

# One app registration by exact display name; prints its appId.
app_id() {
  # --display-name is a prefix filter; the query keeps exact matches only.
  local ids; ids="$(az ad app list --display-name "$1" --query "[?displayName=='$1'].appId" -o tsv)" || die "az ad app list failed"
  [ "$(printf '%s\n' "$ids" | grep -c .)" = 1 ] || die "need exactly one app registration named '$1' (found $(printf '%s\n' "$ids" | grep -c .))"
  printf '%s' "$ids"
}

# Reads the app's federated credentials (JSON) on stdin. "plan DIR" writes one create spec per missing twin into DIR;
# "check" fails unless every twin exists. The twin of repo:OLD:ref:refs/heads/SRC_BRANCH is repo:NEW:ref:refs/heads/main
# (the org branch); other suffixes (pull_request, environment:...) are kept.
oidc_twins() {
  O="$OLD" N="$NEW" ORG="$ORG" B="$SRC_BRANCH" python3 -c '
import json, os, sys
mode = sys.argv[1]; creds = json.load(sys.stdin)
o, n, org, b = (os.environ[k] for k in ("O", "N", "ORG", "B"))
have = {c["subject"] for c in creds}; names = {c["name"] for c in creds}
old = [c for c in creds if c["subject"].startswith("repo:%s:" % o)]
if not old: sys.exit("no federated credential with subject repo:%s:..." % o)
missing = []
for i, c in enumerate(old):
    if c["issuer"] != "https://token.actions.githubusercontent.com": sys.exit("credential %s: unexpected issuer" % c["name"])
    rest = c["subject"][len("repo:%s:" % o):]
    if rest == "ref:refs/heads/%s" % b: rest = "ref:refs/heads/main"
    subj = "repo:%s:%s" % (n, rest)
    if subj in have: print("exists: %s" % subj); continue
    missing.append(subj)
    if mode == "plan":
        name = (c["name"][:120 - len(org) - 1] + "-" + org)
        if name in names: sys.exit("twin name %s already taken by another credential" % name)
        names.add(name)
        twin = {"name": name, "issuer": c["issuer"], "subject": subj, "audiences": c["audiences"],
                "description": "%s, organization repository" % (c.get("description") or c["name"])}
        json.dump(twin, open(os.path.join(sys.argv[2], "%02d.json" % i), "w")); print("create: %s (%s)" % (name, subj))
if mode == "check" and missing: sys.exit("missing twin subjects: %s" % missing)
' "$@"
}

phase_oidc() {
  local app="${1:-}"; [ -n "$app" ] || die "usage: $0 oidc $REPO <app registration display name>"
  local id; id="$(app_id "$app")"
  local spec="$WORK/$REPO-oidc"; rm -rf "$spec"; mkdir -p "$spec"
  az ad app federated-credential list --id "$id" -o json | oidc_twins plan "$spec" || die "federated credential plan failed"
  if [ "${PLAN_ONLY:-}" = 1 ]; then say "oidc plan only (PLAN_ONLY=1): nothing created"; return 0; fi
  local f
  for f in "$spec"/*.json; do
    [ -f "$f" ] || continue
    az ad app federated-credential create --id "$id" --parameters "@$f" --query '[name,subject]' -o tsv || die "create failed for $f"
  done
  echo "read back (app $app, appId ${id:0:8}...):"
  az ad app federated-credential list --id "$id" --query '[].[name,subject]' -o tsv
  az ad app federated-credential list --id "$id" -o json | oidc_twins check >/dev/null || die "read-back: a twin subject is missing"
  say "oidc OK (old subjects stay until $OLD is archived)"
}

# Pipes stdin into an org repo secret; prints the name and the length only.
set_secret() {
  local name="$1" v; v="$(cat)"; [ -n "$v" ] || die "empty value for $name"
  printf '%s' "$v" | gh secret set "$name" -R "$NEW" || die "gh secret set $name failed"
  echo "set $name (${#v} chars)"
}

phase_azure_secrets() {
  local app="${1:-}"; [ -n "$app" ] || die "usage: $0 azure-secrets $REPO <app registration display name>"
  repo_exists "$NEW" || die "$NEW does not exist; run create first"
  local id; id="$(app_id "$app")"
  # The app must be the one oidc twinned for this repo: a wrong app would only fail at the first deploy.
  az ad app federated-credential list --id "$id" -o json | oidc_twins check >/dev/null \
    || die "app $app has no twin credential for $NEW; run oidc first (or this is the wrong app)"
  echo "subscription: $(az account show --query '[name,id]' -o tsv | paste -sd' ' -) (az default; check it is the deploy target)"
  local old_names; old_names=" $(gh api --paginate "repos/$OLD/actions/secrets" --jq '.secrets[].name' | paste -sd' ' -) "
  local n=0
  case "$old_names" in *" AZURE_CLIENT_ID "*) printf '%s' "$id" | set_secret AZURE_CLIENT_ID; n=$((n+1)) ;; esac
  case "$old_names" in *" AZURE_TENANT_ID "*) az account show --query tenantId -o tsv | tr -d '\n' | set_secret AZURE_TENANT_ID; n=$((n+1)) ;; esac
  case "$old_names" in *" AZURE_SUBSCRIPTION_ID "*) az account show --query id -o tsv | tr -d '\n' | set_secret AZURE_SUBSCRIPTION_ID; n=$((n+1)) ;; esac
  [ "$n" -gt 0 ] || die "$OLD has none of AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID"
  echo "org secrets now: $(gh api --paginate "repos/$NEW/actions/secrets" --jq '.secrets[].name' | paste -sd, -)"
  say "azure-secrets OK"
}

phase_swa_token() {
  local secret="${1:-}" swa="${2:-}" rg="${3:-}"
  if [ -z "$secret" ] || [ -z "$swa" ] || [ -z "$rg" ]; then die "usage: $0 swa-token $REPO <secret name> <static web app> <resource group>"; fi
  repo_exists "$NEW" || die "$NEW does not exist; run create first"
  az staticwebapp secrets list -n "$swa" -g "$rg" --query properties.apiKey -o tsv | tr -d '\n' | set_secret "$secret"
  echo "org secrets now: $(gh api --paginate "repos/$NEW/actions/secrets" --jq '.secrets[].name' | paste -sd, -)"
  say "swa-token OK"
}

# Names only, one per line, sorted: repo secrets/variables, environments, and each environment's secrets/variables
# (as env/<name>/secret:<NAME>).
binding_names() {
  local out e envs
  out="$(gh api --paginate "repos/$1/actions/secrets" --jq '.secrets[] | "secret:" + .name')" || return 1
  out="$out
$(gh api --paginate "repos/$1/actions/variables" --jq '.variables[] | "variable:" + .name')" || return 1
  envs="$(gh api --paginate "repos/$1/environments" --jq '.environments[]?.name')" || return 1
  for e in $envs; do
    out="$out
environment:$e
$(gh api --paginate "repos/$1/environments/$e/secrets" --jq ".secrets[] | \"env/$e/secret:\" + .name")" || return 1
    out="$out
$(gh api --paginate "repos/$1/environments/$e/variables" --jq ".variables[] | \"env/$e/variable:\" + .name")" || return 1
  done
  printf '%s\n' "$out" | grep . | sort || true
}

phase_bindings() {
  local o n m
  o="$(binding_names "$OLD")" || die "reading binding names on $OLD failed"
  n="$(binding_names "$NEW")" || die "reading binding names on $NEW failed"
  m="$(comm -23 <(printf '%s\n' "$o" | grep . || true) <(printf '%s\n' "$n" | grep . || true) | paste -sd' ' -)"
  echo "personal: $(printf '%s' "$o" | paste -sd' ' -)"
  echo "org:      $(printf '%s' "$n" | paste -sd' ' -)"
  echo "missing on org: ${m:-none}"
  [ -z "$m" ] || die "bindings missing on $NEW (see above)"
  say "bindings OK"
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
  oidc) phase_oidc "$@" ;;
  azure-secrets) phase_azure_secrets "$@" ;;
  swa-token) phase_swa_token "$@" ;;
  bindings) phase_bindings ;;
  archive) phase_archive "$@" ;;
  *) usage ;;
esac
