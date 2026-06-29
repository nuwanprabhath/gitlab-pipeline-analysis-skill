# Ticket template

Fill this in for the dominant actionable cluster, then create with
`glab issue create --title "<title>" --description "$(cat issue.md)" --assignee <user> --label bug`.
Keep it concrete: real specs, the exact error string, repro job links, a fix
direction, and a local verify command. Drop sections that don't apply.

---

## Summary

<1–3 sentences: what's failing, which protocol/area, and the headline signature.
Include the pipeline URL and how many specs are affected. Note what was already
fixed if relevant (e.g. "X regression is gone; this is the remaining cluster").>

## Signature

```
<the exact error string, e.g.>
DropdownError: Timed out retrying after 30000ms:
[dropdown / clearStaleMenuPortals] 1 portal(s) still active ... before opening "<field>".
  at .../support/commands/dropdown/menu-guard.js:113
```

## Affected specs

| Spec | Detail (field / query / test) | First failed job |
|------|------------------------------|------------------|
| `run/<spec>.cy.js` | `<field or query>` | <first_failed_job_url> |

## Root cause

<Point at the file:line if known. Explain the mechanism. If you couldn't confirm
the exact widget/path, say so and list the open question for the assignee.>

## Suggested fix

<Concrete direction. Bullet the options if there are a few.>

## Verify locally

```bash
cd <app-dir> && yarn cypress run --browser chrome --spec \
  "test/cypress/integration/run/<spec-a>.cy.js,test/cypress/integration/run/<spec-b>.cy.js"
```
<Note which spec exercises which case.>

## Out of scope (other failures in this pipeline)

<List the unrelated failures — pre-existing flakes, ordering deps, test bugs,
flaky-on-retry — so the assignee doesn't chase them.>

## Related
- #<ticket> — <relationship>
