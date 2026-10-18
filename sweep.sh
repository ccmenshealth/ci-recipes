#!/usr/bin/env bash
# sweep.sh: read-only audit of every ccmenshealth repo against the repo standard (STANDARD.md).
#
# For each non-archived repo of the owner (ci-recipes itself excepted: it is the source): does its default branch carry
# the guard caller (.github/workflows/identifier-guard.yml calling ci-recipes' reusable workflow) with `standard: true`?
# With --check, also clone it fresh (depth 1) and run repo-standard/check.py on it (--public for a public repo).
# Prints one line per repo and, with --check, the findings. Never writes to any repo.
#
# This repo is public and its CI token cannot read private repos, so the sweep runs from the owner's Mac (gh logged in
# as the owner), e.g. from the Monday scheduled task.
#
# usage: ./sweep.sh [--check] [--only repo1,repo2] [--owner OWNER] [--workdir DIR]
# Exit 0 all conform, 1 a repo lacks the caller, lacks standard: true, or fails the check, 2 error.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OWNER=ccmenshealth; CHECK_TREE=0; ONLY=""; WORKDIR="${TMPDIR:-/tmp}/ci-recipes-sweep"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_TREE=1; shift ;;
    --only) [ -n "${2:-}" ] || { echo "--only needs a value" >&2; exit 2; }; ONLY="$2"; shift 2 ;;
    --owner) [ -n "${2:-}" ] || { echo "--owner needs a value" >&2; exit 2; }; OWNER="$2"; shift 2 ;;
    --workdir) [ -n "${2:-}" ] || { echo "--workdir needs a path" >&2; exit 2; }; WORKDIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
CALLER_USES="uses: $OWNER/ci-recipes/.github/workflows/identifier-guard.yml@"

if [ -n "$ONLY" ]; then
  list="$(printf '%s\n' "${ONLY//,/$'\n'}" | while IFS= read -r r; do
    [ -n "$r" ] && gh api "repos/$OWNER/$r" --jq '"\(.name) \(.visibility) \(.default_branch)"'; done)"
else
  list="$(gh repo list "$OWNER" --no-archived --limit 500 --json name,visibility,defaultBranchRef \
    --jq '.[] | "\(.name) \(.visibility | ascii_downcase) \(.defaultBranchRef.name // "")"' | sort)"
fi
[ -n "$list" ] || { echo "no repositories found for $OWNER" >&2; exit 2; }
mkdir -p "$WORKDIR"

bad=0
while read -r repo vis branch; do
  [ "$repo" = ci-recipes ] && continue
  if [ -z "$branch" ]; then echo "$repo  empty repository"; continue; fi
  caller="$(gh api -H 'Accept: application/vnd.github.raw' \
    "repos/$OWNER/$repo/contents/.github/workflows/identifier-guard.yml?ref=$branch" 2>/dev/null < /dev/null || true)"
  has_caller=no; has_std=no
  if printf '%s\n' "$caller" | grep -qF "$CALLER_USES"; then has_caller=yes; fi
  if printf '%s\n' "$caller" | grep -qE '^[[:space:]]+standard:[[:space:]]+true([[:space:]]|$)'; then has_std=yes; fi
  line="$repo  $vis  caller=$has_caller standard=$has_std"
  if [ "$has_caller" = no ] || [ "$has_std" = no ]; then bad=1; fi
  if [ "$CHECK_TREE" = 1 ]; then
    clone="$WORKDIR/$repo"; rm -rf "$clone"
    git clone -q --depth 1 --branch "$branch" "https://github.com/$OWNER/$repo.git" "$clone" < /dev/null
    flag=(); if [ "$vis" = public ]; then flag=(--public); fi
    rc=0; out="$(python3 "$HERE/repo-standard/check.py" --root "$clone" ${flag[@]+"${flag[@]}"} 2>&1 < /dev/null)" || rc=$?
    line="$line  check=$rc"
    if [ "$rc" != 0 ]; then bad=1; fi
    echo "$line"
    if [ "$rc" != 0 ]; then printf '%s\n' "$out" | grep -v '^repo-standard:' | cut -f1,2 | sed 's/^/    /'; fi
  else
    echo "$line"
  fi
done <<< "$list"
exit "$bad"
