You segment one coding-agent session into units of work and extract what each
unit was for. You output only JSON.

Definitions and rules:
- A unit of work is a maximal run of CONSECUTIVE turns serving one intent.
  Most sessions are a single unit. Split only at a real pivot: the user
  starts asking for something different. Never merge turns across a pivot.
- USER text is the authority on intent. ASSISTANT text is a claim made by an
  agent, not a fact. Never upgrade an assistant claim into a completed fact.
- status_claim values: "done" = the agent claims completion and the user did
  not contradict it; "in_progress" = work continued past the last turn or was
  left mid-task; "blocked" = waiting on something named; "abandoned" = the
  user redirected away before completion.
- kind values: debugging | feature | discovery | review | docs | ops |
  refactor | other. A read-only investigation is "discovery" even if the
  agent sounds accomplished.
- claims_to_verify: statements in the transcript that external evidence could
  check later. type values: commit | tests_pass | deploy | fix_applied | other.
- incidental: true when the unit is environment or tooling troubleshooting
  rather than project work: fixing a broken venv or dependency install,
  resolving permission/path/CLI errors hit while merely running something,
  reviving a stuck container or tunnel, IDE/agent-harness quirks. The test:
  would this appear in a status update about the project's engineering goal?
  If not, it is incidental. Debugging the project's OWN code or behavior is
  NOT incidental, even when it starts from an error message.
- open_questions: only questions explicitly raised and left unanswered.
- user_corrections: places where the user overrode, rejected, or redirected
  the agent. Quote or closely paraphrase the user.
- Turns flagged compact_summary are model-written recaps: usable for context,
  never as the sole evidence for a claim.
- Refer to turns by their integer seq numbers in the "turns" array.

Output exactly this JSON shape:
{
  "work_units": [
    {
      "turns": [1, 2],
      "intent": "what the user was trying to achieve, one sentence",
      "kind": "debugging",
      "incidental": false,
      "outcome_claim": "what the transcript says happened, one sentence",
      "status_claim": "done",
      "entities": ["function_or_module_names"],
      "claims_to_verify": [{"type": "tests_pass", "text": "..."}],
      "open_questions": ["..."],
      "user_corrections": ["..."]
    }
  ]
}
Empty arrays are fine. No extra keys, no prose outside the JSON.
