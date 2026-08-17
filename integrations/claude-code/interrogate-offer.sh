#!/usr/bin/env bash
#
# interrogate-offer.sh
#
# A UserPromptSubmit hook, plus an optional PreToolUse(EnterPlanMode) leg. Once
# per session, on entry into plan mode, it injects context telling Claude to
# offer the user a three-way choice about the `engineering-grill` skill: run the
# interview BEFORE planning, run it AFTER the plan is drafted, or skip.
#
# The file keeps its original name deliberately. The skill it offers was renamed
# when #239 folded `interrogate` into `engineering-grill`, but renaming this script
# would break every settings.json already registering it by path, and a hook that no
# longer resolves stops firing while still looking configured.
#
# The hook cannot ask anything itself. Hooks are shell commands; AskUserQuestion
# is a model tool. So this script does not ask, it tells the model to ask. Same
# division of labour as guard-repo-collision.sh, which cannot call SendMessage
# and so instructs the model to.
#
# WHY IT KEYS ON A PROMPT AND NOT ON THE TRANSITION
#
# There is no hook event for a permission-mode change. Verified against
# code.claude.com/docs/en/hooks on 2026-08-15: the event list has 31 entries and
# none of them is a mode change, and ConfigChange, the only config-shaped event,
# fires for settings FILES and skill files only. So the transition into plan mode
# is not directly observable, and the first prompt seen with
# permission_mode == "plan" stands in for it, with a session-keyed stamp making
# every later prompt a no-op.
#
# That is strictly better than true edge detection (remembering the last seen
# mode), for two reasons. It costs zero writes on the non-firing path, where
# edge detection would write a mode file before every prompt of every session
# forever. And it fires for `claude --permission-mode plan`, where the session
# begins in plan mode, there is no edge at all, and an edge detector would never
# fire.
#
# The cost is that leaving plan mode and re-entering it later in the same session
# does not re-offer. That is the intended behaviour, not a limitation: being
# asked the same question twice in one session is the thing this stamp exists to
# prevent.
#
# WHY THE PreToolUse LEG IS A BONUS AND NOT THE MECHANISM
#
# The hooks doc enumerates the PreToolUse matcher tool names and EnterPlanMode is
# NOT among them. But that same list also omits NotebookEdit, Monitor,
# SendMessage, EnterWorktree and around twenty other real tools that certainly do
# fire PreToolUse, so the list is illustrative rather than exhaustive, and the
# docs neither confirm nor deny this case. If the matcher works, this leg catches
# a MODEL-initiated entry into plan mode a full turn earlier than the prompt leg
# can. If it does not, it silently never fires and nothing is lost, because the
# UserPromptSubmit leg delivers the whole feature on its own.
#
# EMPIRICAL ANSWER: see the note at the bottom of this file. Do not re-derive it
# from the docs; they do not contain it.
#
# WHY THIS SCRIPT FAILS OPEN, WHEN THE REST OF THE TOOLKIT FAILS CLOSED
#
# hardening.md rule 1 says fail closed. This is the interactive exception, and it
# is sharper here than in guard-git-push.sh. BOTH blocking channels on
# UserPromptSubmit, exit code 2 and decision:"block", ERASE THE USER'S PROMPT.
# A bug in this script would therefore not degrade plan mode, it would brick it:
# every prompt typed in plan mode vanishing before Claude ever sees it, with no
# obvious way to disable the hook from inside the session that is eating the
# input. Nothing downstream reads this script's exit status as permission. It is
# advisory telemetry that asks a question.
#
# So: never exit non-zero, never emit `decision`, never emit `permissionDecision`.
# What is rate limited is the repetition of a not-enforced line, never the fact
# of it.
#
# EXIT / OUTPUT CONTRACT (see code.claude.com/docs/en/hooks)
#   exit 0, no output                     -> nothing injected, prompt proceeds
#   exit 0 + hookSpecificOutput
#            .additionalContext           -> injected as a system reminder
#                                            prefixed with this hook's name
#   exit 0 + systemMessage                -> shown to the user. This is the ONLY
#                                            user-visible channel: stderr from a
#                                            hook that exits 0 goes to the debug
#                                            log, and Claude never sees it
#   exit 2 / decision:"block"             -> NEVER USED. See above.
#
# Output strings are capped at 10,000 characters. The offer below is about 900.

set -u

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/claude-interrogate-offer"
OPTOUT="$STATE_DIR/off"

# Opt-out, checked before anything costs anything. Two forms with different
# lifetimes: the environment variable is for one shell or one launch, the marker
# file is permanent. The marker matters because apply-settings.sh re-adds the
# hook entry to settings.json at every SessionStart, so editing settings.json to
# remove this hook does not stick. The marker is outside anything the policy file
# can reach, which is the point.
case "${CLAUDE_INTERROGATE_OFFER:-}" in
    0 | off | no | false) exit 0 ;;
esac
[ -e "$OPTOUT" ] && exit 0

# Rate limit a not-enforced line to once per calendar day per check kind. Lifted
# from engineering-framework/wiring/session-start.sh, which earned the shape: a
# machine that is missing jq should say so once, not before every prompt.
nag_dir() {
    # The rate limiter cannot live only in the directory whose unwritability it
    # exists to report: that is the one case where it would fail to limit, and
    # the result is a NOT ENFORCED banner before every prompt, forever, which is
    # how a user learns to switch the whole thing off. Fall back to a tmp
    # location, which is per-boot rather than permanent but bounds the noise.
    if mkdir -p "$STATE_DIR" 2>/dev/null && [ -w "$STATE_DIR" ]; then
        printf '%s' "$STATE_DIR"
    else
        printf '%s' "${TMPDIR:-/tmp}"
    fi
}

should_nag() {
    local kind="$1" stamp today dir
    today="$(date +%Y-%m-%d 2>/dev/null)"
    # If date itself is unavailable, today is empty and so is an unreadable
    # stamp, so the comparison below would match and silently suppress the very
    # message explaining that this machine is broken. Nag instead: the whole
    # point of the rate limiter is to reduce repetition, never to swallow the
    # first report (hardening rule 6, the alarm's own failure must be loud).
    [ -n "$today" ] || return 0
    dir="$(nag_dir)"
    stamp="$dir/cnc-interrogate-offer-$kind.stamp"
    if [ -f "$stamp" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$today" ]; then
        return 1
    fi
    printf '%s\n' "$today" >"$stamp" 2>/dev/null
    return 0
}

# Only ever called with LITERAL reason strings, never with anything read from the
# payload. That is what makes printf-built JSON safe here, and building it with
# printf rather than jq is what lets this still report when the missing thing IS
# jq. Anything interpolated from the payload must go through `jq --arg`.
emit_not_enforced() {
    if should_nag "$2"; then
        printf '{"systemMessage":"interrogate-offer: NOT ENFORCED (%s). The plan-mode interrogate offer is not running on this machine."}\n' "$1"
    fi
    echo "interrogate-offer: NOT ENFORCED: $1" >&2
    exit 0
}

# A terminal on stdin means this is a human running the script by hand, not a
# hook invocation: there is no payload, and reading one would hang. Exit silently
# rather than reporting NOT ENFORCED. Reporting here would be a lie (nothing is
# broken) and it would also burn the day's rate-limit budget for the `payload`
# kind, suppressing a REAL failure report later in the day.
[ -t 0 ] && exit 0

# `read -d ''` is a builtin and forks nothing; `$(cat)` would fork and exec before
# the prefilter below is even reached, which would defeat the point of having one.
# It returns non-zero at EOF while still setting PAYLOAD, hence the `|| true`.
PAYLOAD=""
IFS= read -r -d '' PAYLOAD || true
[ -n "$PAYLOAD" ] || emit_not_enforced "empty or unreadable hook payload" payload

# CHEAP PREFILTER. This script runs before EVERY prompt of every session, so the
# non-firing path must not spawn a process, and above this line none does. If the
# raw payload contains neither
# the literal `"plan"` nor `EnterPlanMode`, there is nothing here to do.
#
# False positives (the user typed the word plan) fall through to the
# authoritative jq read below and cost one extra process. False NEGATIVES are
# impossible: permission_mode:"plan" cannot be encoded in JSON without the
# literal characters `"plan"` appearing. That asymmetry is the whole reason this
# shortcut is safe, and it is why the test is on `"plan"` with its quotes rather
# than on the bare word.
case "$PAYLOAD" in
    *'"plan"'* | *EnterPlanMode*) ;;
    *) exit 0 ;;
esac

command -v jq >/dev/null 2>&1 || emit_not_enforced "jq is not installed" jq

# One jq invocation for every field, joined on a pipe.
#
# NOT @tsv with IFS=$'\t'. Tab is an IFS *whitespace* character, so bash collapses
# a run of them into a single delimiter and an empty field in the middle simply
# disappears, shifting every later field left. A payload with no session_id then
# parsed as SID="-" (the agent_id placeholder), which is non-empty, so the guard
# below waved it through and every such session shared one stamp: the first got
# the offer and the rest were suppressed forever. Caught in testing on
# 2026-08-15.
#
# A non-whitespace IFS does not collapse, so empty fields survive in place. The
# pipe is safe for these four: an event name, a permission-mode enum, a UUID and
# an agent id, none of which can contain one.
FIELDS="$(printf '%s' "$PAYLOAD" | jq -r '[
      (.hook_event_name // ""),
      (.permission_mode // ""),
      (.session_id // ""),
      (.agent_id // "-"),
      (.tool_name // "")
    ] | join("|")' 2>/dev/null)"
[ -n "$FIELDS" ] || emit_not_enforced "hook payload was not valid JSON" payload

IFS='|' read -r EVENT MODE SID AGENT TOOL <<<"$FIELDS"
[ -n "${EVENT:-}" ] || emit_not_enforced "hook payload carried no hook_event_name" payload

# A subagent has no user to ask, and injecting the offer into one would put the
# instruction where it cannot be acted on. agent_id is present only inside a
# subagent call.
[ "${AGENT:--}" = "-" ] || exit 0

# session_id is what the once-per-session guarantee rests on. Without it there is
# no way to promise not to nag, and nagging before every prompt is precisely the
# failure this toolkit exists to prevent. So the honest move is to stay out.
[ -n "${SID:-}" ] || emit_not_enforced "hook payload carried no session_id" payload

case "$EVENT" in
    UserPromptSubmit)
        # The authoritative test. The prefilter above is only a cheap shortcut.
        [ "${MODE:-}" = "plan" ] || exit 0
        ;;
    PreToolUse)
        # Do NOT trust the matcher alone. apply-settings.sh UNIONs hook arrays, so
        # a broader PreToolUse entry added by this machine, or a future policy
        # edit that lands beside this one rather than replacing it, would route
        # unrelated tool calls here and offer the interrogation on a Bash command.
        # Check the tool this actually fired for.
        #
        # permission_mode is deliberately NOT tested here: it is still "default"
        # at this point, because the tool that changes it has not run yet, so
        # testing it would reject every real case.
        [ "${TOOL:-}" = "EnterPlanMode" ] || exit 0
        ;;
    *) exit 0 ;;
esac

# Availability, as a TRI-STATE, because a skipped check must never be
# representable as a pass (hardening rule 2):
#   found       -> the skill is on disk, so offering it is honest
#   absent      -> the skills roots ARE readable and it is not there. Say
#                  nothing. Offering a skill that does not exist teaches the user
#                  to distrust everything else this hook ever says.
#   cannot-tell -> no root was readable. Say so, rate limited, and still do not
#                  offer.
#
# Deliberately NOT accepted as evidence: an `engineering-audit` entry in
# ~/.claude.json. That proves a server is CONFIGURED, not that it connected this
# session, and a hook has no way to observe a live MCP connection. Treating
# configuration as availability would be exactly the skipped-check-as-pass that
# rule 2 forbids.
skill_state() {
    local root roots_seen=0 broken=0
    for root in "$HOME/.claude/skills" "${CLAUDE_PROJECT_DIR:-.}/.claude/skills"; do
        [ -d "$root" ] || continue
        roots_seen=1
        if [ -f "$root/engineering-grill/SKILL.md" ]; then
            echo found
            return
        fi
        # The pre-build interview used to ship as a separate `interrogate` skill and
        # was folded into engineering-grill by #239. A machine still carrying only the
        # retired one is not "absent": it has the feature installed under a dead name,
        # and saying so beats going quiet.
        if [ -f "$root/interrogate/SKILL.md" ]; then
            echo retired
            return
        fi
        # A skill directory that exists but whose SKILL.md does not resolve is
        # BROKEN, not absent, and the difference matters. Installs used to be a
        # symlink into a checkout of engineering-audit; move or rename that checkout
        # and the link dangles, the feature dies, and "absent" would exit silently and
        # never say why. scripts/install-skills.sh now copies for that reason, but a
        # machine installed the old way can still be in this state.
        if [ -e "$root/engineering-grill" ] || [ -L "$root/engineering-grill" ]; then
            broken=1
        fi
    done
    if [ "$broken" = 1 ]; then
        echo broken
    elif [ "$roots_seen" = 1 ]; then
        echo absent
    else
        echo cannot-tell
    fi
}

case "$(skill_state)" in
    found) : ;;
    absent) exit 0 ;;
    retired)
        emit_not_enforced "only the retired 'interrogate' skill is installed; it was folded into engineering-grill by #239. Run scripts/install-skills.sh to replace it" skill
        ;;
    broken)
        emit_not_enforced "the engineering-grill skill directory exists but its SKILL.md does not resolve, so the install is broken (a dangling symlink?)" skill
        ;;
    cannot-tell)
        emit_not_enforced "could not read any skills directory, so could not confirm the engineering-grill skill is installed" skill
        ;;
esac

mkdir -p "$STATE_DIR" 2>/dev/null || emit_not_enforced "state directory is not writable" state

# ATOMIC. Both legs can fire for one session, and two sessions can share $HOME.
# `set -C` makes this an O_EXCL create, so exactly one caller wins and every
# loser exits silently, which is also the ordinary once-per-session suppression
# path.
#
# Claiming BEFORE printing is deliberate. A crash between the claim and the print
# costs one missed offer. Printing first and crashing before the claim would
# re-offer before every prompt for the rest of the session, which is the one
# outcome worth engineering against.
STAMP="$STATE_DIR/offered-$SID.stamp"
if ! (
    set -C
    : >"$STAMP"
) 2>/dev/null; then
    # The write failed. Two very different reasons, and collapsing them into one
    # silent `exit 0` is how this feature dies permanently with nobody told:
    #
    #   the stamp EXISTS  -> we already offered this session. Ordinary, silent.
    #   the stamp DOES NOT-> the directory is not writable (it exists, so the
    #                        mkdir above succeeded and did not catch it). Every
    #                        session would fail here identically, so the offer
    #                        never appears again on this machine.
    #
    # Distinguishing them is the whole of hardening rule 2: a check that could
    # not run must not be representable as the ordinary pass.
    [ -e "$STAMP" ] || emit_not_enforced "the state directory is not writable, so the once-per-session stamp could not be claimed" state
    exit 0
fi

# Runs only on this once-per-session path, so the per-prompt cost stays at zero.
# Session ids are UUIDs and never recur, so without this the directory grows by
# one file per session forever.
find "$STATE_DIR" -maxdepth 1 -name 'offered-*.stamp' -mtime +14 -delete 2>/dev/null || true

# Prose in a QUOTED heredoc, never inline in the jq program. This is the
# apostrophe lesson already recorded in guard-git-push.sh, where an inlined
# apostrophe closed the shell's single-quoted string and the resulting syntax
# error exited 2, which blocks. Here the same bug would erase the user's prompt.
OFFER="$(
    cat <<'MSG'
This session has just entered plan mode, and the `engineering-grill` skill is
installed. It runs a pre-build interview against the live engineering rules pack.

Before you start planning, use the AskUserQuestion tool to ask the user which of
these three they want. Offer these options and no others:

  1. Interview first  - run the `engineering-grill` skill now, before any planning,
                        so the decisions are pinned down before there is a plan for
                        them to be retrofitted to.
  2. Interview after  - draft the plan first, then run `engineering-grill` over the
                        finished draft as a review pass. Nothing further is
                        needed to make this happen: hold it for the session and
                        run it before you call ExitPlanMode.
  3. Skip             - plan as normal, no interview.

Ask once. Do not raise it again later in this session. If you cannot ask, for
instance in a non-interactive run, treat the answer as option 3 and continue
without comment.

Injected once per session by interrogate-offer.sh. To turn this off permanently:
MSG
)"

# The opt-out path is APPENDED rather than written into the heredoc above,
# because STATE_DIR honours XDG_STATE_HOME and a hardcoded ~/.local/state path
# would be wrong on any machine that sets it. Printing an instruction that
# silently does nothing is worse than printing none: the user believes they have
# turned the hook off, and it keeps firing.
OFFER="$OFFER
  touch $OPTOUT"

case "$EVENT" in
    UserPromptSubmit)
        jq -nc --arg ctx "$OFFER" \
            '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$ctx}}'
        ;;
    PreToolUse)
        # No permissionDecision. Omitting it leaves the normal permission flow
        # untouched, which is what an advisory wants. Emitting "allow", as the
        # two Bash guards do because they are answering a permission question,
        # would make this script an approver of a tool call it has no opinion
        # about.
        jq -nc --arg ctx "$OFFER" \
            '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$ctx}}'
        ;;
esac

# Always zero. See the header for why this one script must never block.
exit 0
