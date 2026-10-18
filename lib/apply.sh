# lib/apply.sh: file placement shared by sync.sh and new-repo.sh. Source it after setting RECIPES to the ci-recipes root.
# shellcheck shell=bash disable=SC2034  # the variables are read by the scripts that source this file

GUARD_SRC="$RECIPES/identifier-guard/identifier-guard.py"
HOOK_SRC="$RECIPES/identifier-guard/pre-commit"
PLAIN_CALLER="$RECIPES/identifier-guard/caller-workflow.yml"
STANDARD_CALLER="$RECIPES/template/.github/workflows/identifier-guard.yml"
TEMPLATE_PR="$RECIPES/template/.github/PULL_REQUEST_TEMPLATE.md"
TEMPLATE_CONFIG="$RECIPES/template/.repo-standard.json"
CHECK="$RECIPES/repo-standard/check.py"

# caller_for <dir>: the caller workflow a repo should carry. A repo that has opted into the standard (it has
# .repo-standard.json) keeps the standard caller, so a plain guard sync never reverts the opt-in.
caller_for() {
  if [ -f "$1/.repo-standard.json" ]; then echo "$STANDARD_CALLER"; else echo "$PLAIN_CALLER"; fi
}

# apply_guard <dir>: the guard script, the hook and the right caller workflow.
apply_guard() {
  mkdir -p "$1/scripts" "$1/.githooks" "$1/.github/workflows"
  cp "$GUARD_SRC" "$1/scripts/identifier-guard.py"
  cp "$HOOK_SRC" "$1/.githooks/pre-commit"; chmod +x "$1/.githooks/pre-commit"
  cp "$(caller_for "$1")" "$1/.github/workflows/identifier-guard.yml"
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

# in_sync <dir> <standard: 0|1>: true when every file apply_guard (and apply_standard when asked) would write is current.
in_sync() {
  local dir="$1" want_caller
  if [ "$2" = 1 ]; then
    [ -f "$dir/.repo-standard.json" ] && [ -f "$dir/CLAUDE.md" ] && [ -f "$dir/.github/PULL_REQUEST_TEMPLATE.md" ] \
      && grep -qiE '^[^[:alnum:]]*CI cost([^[:alnum:]]|$)' "$dir/.github/PULL_REQUEST_TEMPLATE.md" || return 1
    want_caller="$STANDARD_CALLER"
  else
    want_caller="$(caller_for "$dir")"
  fi
  cmp -s "$GUARD_SRC" "$dir/scripts/identifier-guard.py" && cmp -s "$HOOK_SRC" "$dir/.githooks/pre-commit" \
    && cmp -s "$want_caller" "$dir/.github/workflows/identifier-guard.yml"
}
