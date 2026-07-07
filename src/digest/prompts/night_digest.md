Write the "End-of-Day Work Digest" for {date}. It closes out the day and sets
up tomorrow morning: what I did, what was found, what is next, what needs
verification. Keep it short; this is a note to myself, not a report.

Produce exactly these sections, in order, each as a "## " heading:

## What changed today
Max 4 bullets, primary repo first. Actual modules/files touched, with
citations. A secondary repo gets one line only if something real happened.

## Work completed
Max 5 bullets. Only finished items with direct evidence. A discovery pass is a
discovery pass, not a completed feature. Fold related commits into one bullet.

## Work in progress
Max 4 bullets: what is half-done and what state it is actually in
(uncommitted files, open branch, failing test).

## Blockers
Only real ones, one line each. If nothing is blocking, say so in one line.

## Risky or untested changes
Max 4 bullets. For each, the exact test file, script, or flow that would prove
it safe. Never state a change is safe or broken without evidence.

## Notes for tomorrow morning
Max 3 lines. Where I left off and the exact first action tomorrow: the file to
open, the command to run, or the question to answer first.

## Suggested coding-agent prompts
Max 2 prompts, based only on evidenced items. Each must name at least one
concrete file, function, or test from the evidence. Pattern:

> [1-2 lines of context.] Your job is to [exact task]. Inspect [specific
> files/functions] first. Come back with: A. [output] B. [output] C. [output].
> Do not [scope creep]. Be concrete and codebase-specific: exact file paths,
> function names, line numbers.

Never write "verify improvements" or "ensure it works"; state the exact
failing behavior or artifact to produce.

If any items are uncertain, add a final section:

## Needs verification
Anything you were tempted to state as fact but the evidence does not directly
support: suspected root causes, work that looks finished but has no completion
evidence, ambiguous fragments. One line each with why it is uncertain.

EVIDENCE:

{evidence}
