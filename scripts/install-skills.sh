#!/usr/bin/env bash
#
# Install this repository's skills into your assistant's skills directory.
#
# It COPIES. It deliberately does not symlink.
#
# Why: a symlink from ~/.claude/skills/<name> into this checkout means whichever
# branch the checkout happens to be sitting on is what your assistant executes. Check
# out a branch to try something, and every session on the machine silently starts
# running that branch's version of the skill. Nothing announces it. On 2026-08-17 an
# unmerged feature branch was the live `interrogate` for as long as it took to review
# a pull request.
#
# The cost of copying is that editing this repository no longer changes the installed
# skill. That is the point: updating becomes a deliberate act. Use `--check` to see
# when an installed copy has fallen behind the repository.
#
# Usage:
#   scripts/install-skills.sh            install or update every skill, every host found
#   scripts/install-skills.sh --check    report status, change nothing, exit 1 on drift
#   scripts/install-skills.sh --dry-run  show what would change, write nothing
#
# Exit codes:
#   0  everything current (or installed successfully)
#   1  drift found (--check), or an install failed
#   2  could not run at all
#
set -uo pipefail

REPO=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null) || {
  echo "install-skills: not inside the engineering-audit checkout." >&2
  exit 2
}
cd "$REPO" || exit 2

MODE="install"
WANTED=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check)   MODE="check" ;;
    --dry-run) MODE="dry" ;;
    -*) echo "install-skills: unknown option '$1'. Use --check or --dry-run." >&2; exit 2 ;;
    *)  WANTED+=("$1") ;;
  esac
  shift
done

# source directory | which hosts it belongs on.
#
# engineering-grill is cross-host: it states the no-full-domain-document rule as an
# invariant and lets each host meet it its own way, sub-agents where they exist and
# serial read-and-discard where they do not. audit is Claude Code packaging.
#
# `interrogate` was a third entry here until #239 folded it into engineering-grill.
# If you installed it before that, remove the leftover: this script will not, because
# it only manages the skills it ships.
SKILLS=(
  "integrations/claude-code/audit|claude"
  "integrations/engineering-grill/engineering-grill|claude codex"
)

# Skills this repository used to ship and no longer does. They are removed on install
# rather than left behind, because a retired skill that stays installed keeps being
# offered to the assistant and there is nothing anywhere to say it is dead.
RETIRED=(
  "interrogate"
)

# A skill's identity is the content of its directory. Comparing that against the
# installed copy is what makes "current" and "STALE" distinguishable without a
# manifest file that could itself go stale.
fingerprint() {
  local dir="$1"
  [ -d "$dir" ] || { echo "MISSING"; return; }
  ( cd "$dir" && find . -type f -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1 )
}

hosts=()
[ -d "$HOME/.claude" ] && hosts+=("claude:$HOME/.claude/skills:Claude Code")
[ -d "$HOME/.codex" ]  && hosts+=("codex:$HOME/.codex/skills:Codex")

if [ ${#hosts[@]} -eq 0 ]; then
  echo "install-skills: found neither ~/.claude nor ~/.codex."
  echo "Nothing to install into. This is a could-not-run, not a clean result."
  exit 2
fi

drift=0
failed=0
seen=0

for entry in "${hosts[@]}"; do
  host_key="${entry%%:*}"
  rest="${entry#*:}"
  dest_root="${rest%%:*}"
  host_name="${rest##*:}"
  echo "== $host_name ($dest_root) =="

  for skill in "${SKILLS[@]}"; do
    src="${skill%%|*}"
    skill_hosts="${skill##*|}"
    name=$(basename "$src")

    # skip skills that do not belong on this host
    case " $skill_hosts " in *" $host_key "*) ;; *) continue ;; esac

    # skip anything not named, when names were given
    if [ ${#WANTED[@]} -gt 0 ]; then
      match=0
      for w in "${WANTED[@]}"; do [ "$w" = "$name" ] && match=1; done
      [ "$match" -eq 1 ] || continue
    fi

    dest="$dest_root/$name"
    seen=$((seen + 1))

    if [ ! -d "$src" ]; then
      printf '  %-22s %s\n' "$name" "SOURCE MISSING ($src)"
      failed=1
      continue
    fi

    want=$(fingerprint "$src")

    if [ -L "$dest" ]; then
      # The old install method. Report it as its own state rather than as "stale",
      # because the fix is different: the link has to go, not just be refreshed.
      printf '  %-22s %s\n' "$name" "SYMLINK into $(readlink "$dest")"
      drift=1
      [ "$MODE" = "install" ] || continue
      rm -f "$dest" || { failed=1; continue; }
    elif [ -d "$dest" ]; then
      have=$(fingerprint "$dest")
      if [ "$want" = "$have" ]; then
        printf '  %-22s %s\n' "$name" "current"
        continue
      fi
      printf '  %-22s %s\n' "$name" "STALE"
      drift=1
      [ "$MODE" = "install" ] || continue
      rm -rf "$dest" || { failed=1; continue; }
    else
      printf '  %-22s %s\n' "$name" "NOT INSTALLED"
      drift=1
      [ "$MODE" = "install" ] || continue
    fi

    mkdir -p "$dest_root" || { failed=1; continue; }
    if cp -R "$src" "$dest"; then
      got=$(fingerprint "$dest")
      if [ "$got" = "$want" ]; then
        printf '  %-22s %s\n' "" "-> installed"
      else
        # Never report an install as done without checking the result matches.
        printf '  %-22s %s\n' "" "-> INSTALL VERIFY FAILED (copied, but content differs)"
        failed=1
      fi
    else
      printf '  %-22s %s\n' "" "-> COPY FAILED"
      failed=1
    fi
  done
done

# Retired skills, checked on every host regardless of which skills were named, because
# a leftover is not something you would think to ask about by name.
retired_header=0
for entry in "${hosts[@]}"; do
  rest="${entry#*:}"
  dest_root="${rest%%:*}"
  host_name="${rest##*:}"
  for old in "${RETIRED[@]}"; do
    leftover="$dest_root/$old"
    [ -e "$leftover" ] || [ -L "$leftover" ] || continue
    if [ "$retired_header" -eq 0 ]; then echo "== retired, no longer shipped =="; retired_header=1; fi
    printf '  %-22s %s\n' "$old" "still installed under $host_name"
    drift=1
    if [ "$MODE" = "install" ]; then
      if rm -rf "$leftover"; then
        printf '  %-22s %s\n' "" "-> removed"
      else
        printf '  %-22s %s\n' "" "-> REMOVE FAILED"
        failed=1
      fi
    fi
  done
done

echo
if [ "$seen" -eq 0 ]; then
  echo "install-skills: examined ZERO skills. The paths in this script have moved."
  echo "That is a broken installer, not a clean run, so it exits non-zero."
  exit 2
fi

case "$MODE" in
  check)
    if [ "$failed" -ne 0 ]; then echo "check: FAILED (see above)"; exit 1; fi
    if [ "$drift" -ne 0 ]; then
      echo "check: drift found. Run scripts/install-skills.sh to bring the installed copies up to date."
      exit 1
    fi
    echo "check: all $seen installed skill(s) match this checkout."
    ;;
  dry)
    echo "dry run: nothing was written."
    [ "$drift" -ne 0 ] && exit 1
    ;;
  install)
    if [ "$failed" -ne 0 ]; then echo "install: one or more skills FAILED (see above)"; exit 1; fi
    echo "install: $seen skill(s) checked, all installed copies now match this checkout."
    echo "They are copies. Re-run this after pulling, or run --check to see when they drift."
    ;;
esac
exit 0
