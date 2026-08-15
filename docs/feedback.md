# Sending feedback about an audit run

`engineering-audit` has an optional feedback channel back to the tool author. It is opt-in and
narrow: nothing about an audited repository's findings ever leaves your machine through it, and
nothing is sent at all unless you write something.

## What triggers a send

Feedback is only sent when:

- you write something in the "Feedback to the tool author" box on the configuration page, or
- the assistant explicitly asks whether to send feedback and you say yes.

No feedback text, no send. An audit that ran cleanly and quietly is not itself a reason to
contact the tool author.

## What goes into it

If you do send feedback, the message always contains:

- **Your free text**, exactly as written.
- **Run metadata**: tool version, rules pack name, assistant, model, repository name and commit,
  and the run's start/finish timestamps. This is not a consent toggle; it is the context that
  makes the free text mean anything (a bug report with no version number is not actionable).

Then, only the sections you ticked on the configuration page:

- **Coverage statistics**: total files inspected and total files skipped, across the run. Not
  broken down by file name.
- **Findings rollup**: counts of findings by severity and by domain id. Never finding text,
  never a finding's title, body, or location.
- **Self-assessment**: the confidence level and limits note recorded for each domain. Not the
  findings that assessment is about.
- **Environment information**: a closed set of exactly three keys, `os`, `host_cli` and
  `host_cli_version`. Assistant, model and tool version are deliberately excluded: they are
  already fixed rows in the run metadata above, so this field cannot be used to carry anything
  else.
- **Consulted sources**: the URLs, titles and rule ids of any external references a domain
  sweep looked at outside the rules pack. Off by default for its own reason, not just the
  general one: a fetched URL can hint at what a private repository is about, even though the
  finding text itself never leaves the machine.
- **Rule verdict distribution**: per-domain and total counts of pass, finding, not-applicable
  and could-not-evaluate verdicts. No repository content, paths, URLs or finding text, only
  counts of the tool's own vocabulary.
- **Duration**: the assistant-supplied span, the server-measured span, and the divergence
  verdict between them. Never token counts, since the server never sees them and could not
  check a reported figure against anything.
- **Rules fetched**: which domains had their rule text served by the tool this run. Shows only
  that the text was fetched, never that it was read or applied.
- **Reader's own conclusions**: the two summary questions answered by the person reading the
  finished report, in their own words, after the fact. Never computed from repository data or
  verified by the tool.

A section you did not tick is left out of the message entirely, not sent empty. There is no way
to tell, from the message, whether an omitted section had nothing to report or was simply not
consented to, and that is deliberate: the two must look identical from the outside.

**Finding text never leaves the machine through this channel.** Only counts (the rollup) and,
separately, whatever you choose to write in the free-text box.

## Where it goes

Feedback is filed as a labelled GitHub issue on `rodlunt/engineering-audit`, via your own `gh`
CLI (the same tool this project uses to file findings as issues on the audited repository, if
you chose that delivery mode). No token is handled by this tool: it relies entirely on `gh`'s own
authentication.

If `gh` is not installed, not authenticated, or filing fails for any other reason, the feedback
is never silently dropped. You are instead offered a `mailto:` link addressed to the tool
author, pre-filled with the same subject and body, and the plain body text itself so you can
paste it into an email if the link does not open a mail client. The audit report's own Feedback
section carries this same fallback, so you can always find and send it later even if you decline
in the moment.
