Write the morning "Daily Work Brief" for {date}. Its only job is to get me
executing within minutes: what I did, what was found, what is next, what needs
verification. Keep it short; this is a note to myself, not a report.

Produce exactly these sections, in order, each as a "## " heading:

## Executive summary
2-3 sentences max. The state of the primary repo work and the single most
important thing today. Start from the actual situation.

## Yesterday's completed work
Max 5 bullets, primary repo first. Only finished items with direct evidence.
Name the function/file that changed, not the theme. Fold commits about the
same change into one bullet.

## Open bugs / unresolved questions
Max 5 bullets. Each must be a complete, specific statement of what is broken
or unknown, with the failure mode if the evidence shows it. No fragments.

## Decisions made
Max 3 bullets: decision, then "The main reason is [reason]." Only decisions
visible in the evidence. Usually this section is short or empty.

## What needs testing
Max 4 bullets. For each, name the exact test file, script, command, or flow
from the evidence. If the evidence does not name one, say what would prove the
change safe in one line.

## Trello-ready updates
ONE card update for the primary workstream (add a second only if a secondary
repo had real work). Follow this shape exactly, drop lines that do not apply,
one sentence per line, no repetition of earlier sections' wording:

Today's Update
- I completed [work done] around [module / area].
- The main finding is [specific technical finding].
- I'm now moving toward [next implementation step].
- Blocker / open question: [only if real].

Write it like a quick note, not a status report. Use backticked technical
names. No vague progress language.

## Prompts to send to coding agents
Max 2 prompts, highest-priority evidenced items only. Each must name at least
one concrete file, function, or test from the evidence; if the evidence names
none, name the thing to find first. Pattern:

> [1-2 lines of context.] Your job is to [exact task]. Inspect [specific
> files/functions] first. Come back with: A. [output] B. [output] C. [output].
> Do not [scope creep]. Be concrete and codebase-specific: exact file paths,
> function names, line numbers.

Never write "verify improvements" or "ensure it works"; state the exact
failing behavior or artifact to produce.

## Top 3 tasks for today
Exactly 3, ordered by impact, primary repo first. One line each plus citation.
For task 1 only, add one line: "First 60-90 min: [the concrete first action:
the file to open, command to run, or question to answer]."

## People to follow up with
Only names/roles in the evidence and why, one line each.
If none, "Nothing found in available sources."

If any items are uncertain, add a final section:

## Needs verification
Anything you were tempted to state as fact but the evidence does not directly
support: suspected root causes, work that looks finished but has no completion
evidence, ambiguous fragments. One line each with why it is uncertain.

EVIDENCE:

{evidence}
