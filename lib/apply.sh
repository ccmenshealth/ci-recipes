# lib/apply.sh: file placement shared by sync.sh and new-repo.sh. Source it after setting RECIPES to the ci-recipes root.
# shellcheck shell=bash disable=SC2034  # the variables are read by the scripts that source this file

GUARD_SRC="$RECIPES/identifier-guard/identifier-guard.py"
HOOK_SRC="$RECIPES/identifier-guard/pre-commit"
PLAIN_CALLER="$RECIPES/identifier-guard/caller-workflow.yml"
STANDARD_CALLER="$RECIPES/template/.github/workflows/identifier-guard.yml"
TEMPLATE_PR="$RECIPES/template/.github/PULL_REQUEST_TEMPLATE.md"
TEMPLATE_CONFIG="$RECIPES/template/.repo-standard.json"
CHECK="$RECIPES/repo-standard/check.py"

# Callers pin an exact release tag (v1.N); there is no moving tag. PIN_TAG is the tag written into every caller placed
# here; sync.sh and new-repo.sh set it (latest_release_tag, or --tag) and check it with check_pin_tag first.

# latest_release_tag <remote>: the highest v1.N tag on that remote (v1, v2-rc and other shapes are ignored).
latest_release_tag() {
  git -C "$RECIPES" ls-remote --tags --refs "$1" 'v1.*' | sed 's#.*refs/tags/##' | { grep -E '^v1\.[0-9]+$' || true; } \
    | sort -t. -k2,2n | tail -n 1
}

# release_tag_shape <tag>: true for an exact v1.N tag; anything else (v1, v1.2.3, a branch, sed or shell syntax) is refused.
release_tag_shape() {
  [[ "$1" =~ ^v1\.[0-9]+$ ]] || { echo "pin tag '$1' is not an exact v1.N release tag" >&2; return 1; }
}

# check_pin_tag <tag>: the tag is a v1.N tag on origin whose guard script, hook and standard check are this clone's
# files, so a caller pinned to it passes the reusable workflow's hash check and runs the check.py this clone ran.
# Prints the reason and returns 1 otherwise.
check_pin_tag() {
  local tag="$1" f
  release_tag_shape "$tag" || return 1
  if ! git -C "$RECIPES" fetch -q --no-tags origin "refs/tags/$tag" 2>/dev/null; then
    echo "tag $tag is not on origin; cut and push it first (git tag $tag <commit> && git push origin $tag)" >&2; return 1
  fi
  for f in identifier-guard/identifier-guard.py identifier-guard/pre-commit repo-standard/check.py; do
    if [ "$(git -C "$RECIPES" rev-parse "FETCH_HEAD:$f" 2>/dev/null)" != "$(git -C "$RECIPES" hash-object "$RECIPES/$f")" ]; then
      echo "tag $tag has a different $f than this clone; check out the tag, or merge and cut a new tag" >&2; return 1
    fi
  done
}

# place_caller <source caller> <dest>: the caller with its ci-recipes reference pinned to PIN_TAG (checked for shape first,
# so nothing else ever reaches sed). Writes nothing and returns 1 if the source has no pinnable uses: line.
place_caller() {
  local out
  [ -n "${PIN_TAG:-}" ] || { echo "PIN_TAG is not set" >&2; return 1; }
  release_tag_shape "$PIN_TAG" || return 1
  out="$(sed -E "s#^( *uses: ccmenshealth/ci-recipes/\.github/workflows/identifier-guard\.yml)@[^[:space:]]*#\1@${PIN_TAG}#" "$1")" || return 1
  grep -qE "^ *uses: ccmenshealth/ci-recipes/\.github/workflows/identifier-guard\.yml@${PIN_TAG}([[:space:]]|$)" <<< "$out" \
    || { echo "$1 has no ci-recipes identifier-guard uses: line to pin" >&2; return 1; }
  printf '%s\n' "$out" > "$2"
}

# caller_for <dir>: the caller workflow a repo should carry. A repo that has opted into the standard (it has
# .repo-standard.json) keeps the standard caller, so a plain guard sync never reverts the opt-in.
caller_for() {
  if [ -f "$1/.repo-standard.json" ]; then echo "$STANDARD_CALLER"; else echo "$PLAIN_CALLER"; fi
}

# apply_guard <dir>: the guard script, the hook and the right caller workflow, pinned to PIN_TAG.
apply_guard() {
  [ -n "${PIN_TAG:-}" ] || { echo "PIN_TAG is not set" >&2; return 1; }
  mkdir -p "$1/scripts" "$1/.githooks" "$1/.github/workflows"
  cp "$GUARD_SRC" "$1/scripts/identifier-guard.py"
  cp "$HOOK_SRC" "$1/.githooks/pre-commit"; chmod +x "$1/.githooks/pre-commit"
  place_caller "$(caller_for "$1")" "$1/.github/workflows/identifier-guard.yml"
}

# apply_standard <dir> <public: 0|1>: PR template (or the CI-cost section appended to an existing one that lacks the
# line), .repo-standard.json (kept if present; "public" set when the argument is 1, never cleared), a CLAUDE.md when
# missing (one line that imports AGENTS.md if the repo has one, else a pointer to the standard), then the guard with the
# standard caller. Returns 1 without touching the guard files when an existing .repo-standard.json is not valid JSON.
apply_standard() {
  local dir="$1" public="$2" pr="$1/.github/PULL_REQUEST_TEMPLATE.md"
  if [ -f "$dir/.repo-standard.json" ] && ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$dir/.repo-standard.json" 2>/dev/null; then
    echo "$dir/.repo-standard.json is not valid JSON; fix it first" >&2; return 1
  fi
  mkdir -p "$dir/.github"
  if [ ! -f "$dir/CLAUDE.md" ]; then
    if [ -f "$dir/AGENTS.md" ]; then echo "@AGENTS.md" > "$dir/CLAUDE.md"
    else echo "Rules: the ccmenshealth repo standard, https://github.com/ccmenshealth/ci-recipes/blob/main/STANDARD.md" > "$dir/CLAUDE.md"; fi
  fi
  if [ ! -f "$pr" ]; then
    cp "$TEMPLATE_PR" "$pr"
  elif ! grep -qiE '^[^[:alnum:]]*CI cost([^[:alnum:]]|$)' "$pr"; then
    printf '\n## CI cost\n\n- CI cost: <!-- "no workflow change", or the added GitHub-hosted minutes and $ per month -->\n' >> "$pr"
  fi
  [ -f "$dir/.repo-standard.json" ] || cp "$TEMPLATE_CONFIG" "$dir/.repo-standard.json"
  python3 - "$dir/.repo-standard.json" "$public" <<'EOF'
import json, sys
path, public = sys.argv[1], sys.argv[2] == "1"
cfg = json.load(open(path))
cfg["public"] = public or cfg.get("public") is True
open(path, "w").write(json.dumps(cfg, indent=2) + "\n")
EOF
  apply_guard "$dir"
}

# in_sync <dir> <standard: 0|1>: true when every file apply_guard (and apply_standard when asked) would write is current,
# including the caller's pin (a repo on an older tag is not in sync).
in_sync() {
  local dir="$1" want_caller pinned rc
  if [ "$2" = 1 ]; then
    [ -f "$dir/.repo-standard.json" ] && [ -f "$dir/CLAUDE.md" ] && [ -f "$dir/.github/PULL_REQUEST_TEMPLATE.md" ] \
      && grep -qiE '^[^[:alnum:]]*CI cost([^[:alnum:]]|$)' "$dir/.github/PULL_REQUEST_TEMPLATE.md" || return 1
    want_caller="$STANDARD_CALLER"
  else
    want_caller="$(caller_for "$dir")"
  fi
  pinned="$(mktemp)"
  place_caller "$want_caller" "$pinned" || { rm -f "$pinned"; return 1; }
  rc=0
  cmp -s "$GUARD_SRC" "$dir/scripts/identifier-guard.py" && cmp -s "$HOOK_SRC" "$dir/.githooks/pre-commit" \
    && cmp -s "$pinned" "$dir/.github/workflows/identifier-guard.yml" || rc=1
  rm -f "$pinned"
  return "$rc"
}
