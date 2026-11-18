---
tier: standard
approval: true
---

# develop

> Fix or improve existing code.
> Orchestrates: /review-code → /refactor → /test → /review-code

---

## Instructions

TODO: implement this pipeline.

---

## automated invocation

When loaded by a planner (optionally with `ticket=<path to ticket note>`):

- If `ticket` is given: use its `## ticket` section and the files in its folder as input, write all outputs into the ticket folder, and link them under `## outputs`
- Approval points still apply — ask as usual when interactive; when running as a subagent for orchestration, stop and report the approval point instead of asking
