# Failure-cause taxonomy

A reference for labelling each failed spec's `failure_cause` and
`bug_likelihood_(AI)`. Match the captured error (`failures_raw_<pid>.json` →
`first_error` / `signatures`) to a bucket, then write a concise label. Prefer
a **root cause** over a downstream symptom, and hedge (`(likely ...)`) when
the evidence is partial.

This list is meant to grow — add recurring signatures your team sees.

## Golden rules

0. **The error TYPE decides glitch-eligibility — check it FIRST, and never
   override it.** `extract_failures.py` tags each spec with an `error_kind`:
   - **`value-mismatch`** (`to deeply equal`, `to equal`, count mismatch, exact
     text, `expected X to be Y`) → the app produced the wrong value/data. This
     is a **BUG signal (MEDIUM/HIGH), never a Cypress glitch**, regardless of
     any UI symptom around it.
   - **`app-error`** (`Failed to publish/load`, `Unregistered model case`,
     `Cannot read properties`, `not unique`, persistent app error banner) →
     bug/environment signal, not a glitch.
   - **`element-timeout`** (`never found`, `hidden from view`, dropdown races,
     stepper `continuously found`) → the ONLY kind eligible for the
     glitch/LOW bucket, and only when the captured error genuinely is that.

   A `value-mismatch` or `app-error` spec that you're tempted to call a
   dropdown/overlay glitch is the classic bug-masking mistake — stop and read
   the assertion instead.
1. **Ground every label in THIS spec's own captured error.** The
   `failure_cause` must paraphrase this spec's `first_error`/`signatures`.
   Never borrow an error, selector, or symptom from another spec, from the
   example phrases below, or from memory. If the cause names something not in
   this spec's captured error, it's fabricated — remove it.
2. **The raw error message is a starting point, not a verdict.** Understand
   what the failing test was *trying to assert*: read the spec code at
   `first_error_spec_line`/`first_error_frames` (and the custom command it
   calls). A bare `expected false to be true` is meaningless until you know it
   came from, e.g., a route assertion that the app "shouldn't have navigated".
3. **Root vs cascade.** One stuck interaction early in a form makes everything
   after it fail (`element never found`, `stepper step-content continuously
   found`, `cy.filter() requires a DOM element`). Label those `cascade of ...`,
   not as their own bug — but only when there IS a real upstream failure.
4. **Flaky vs hard.** `Passed on retry: yes` in the CSV → `flaky (passed on retry)`.
5. **Related vs pre-existing.** Keep environment/ordering/test-bug failures out of
   the headline cluster.
6. **Stale test vs app bug.** When a *deterministic* assertion fails (exact
   text/route/data mismatch, not a timeout), check whether the app behavior
   was intentionally changed: `git log --oneline -S "<asserted text>" --
   <app src>` at the checkout, or search recent MRs. A test asserting removed
   behavior is `stale test (app behavior changed: <feature>)`, not an app bug.
   Real example: plot-context.cy.js asserted "Cannot edit protocol ... due to
   differing context" rejection; the quick-swap feature had deliberately
   replaced rejection with auto-switching context, so the app now navigates
   into the workflow and the route assertion fails.
7. **Be honest about confidence.** `(likely upstream cascade)` is a valid label;
   `OTHER: <short error>` at MEDIUM is better than a wrong confident glitch label.

## bug_likelihood_(AI) rubric

This column separates **real app bugs** from **Cypress/test-infra glitches**
so users can locally re-run the HIGH ones first. Assign one of:

- **HIGH** — evidence points at the app, not the test runner:
  - Deterministic value mismatches: exact-text assertion fails
    (`expected 'X' to equal 'X (suffix)'`), wrong list contents
    (`expected [Array(26)] to deeply equal ['Forb','Shrub']`), wrong **object
    shape** (`expected {…} to deeply equal {…}` with a `+ expected - actual`
    diff showing a field added/removed/changed, e.g. `fauna_plot_not_walked`
    present in actual but not expected → data not persisting/leaking), wrong
    data shape (`Unregistered model case <model>`).
  - The app displays a real error state (`Failed to load protocol`, setup
    error banner, uncaught app exception in the notification).
  - The same deterministic mismatch appears in 2+ independent specs.
  - Any `error_kind: value-mismatch` where reading the assertion confirms the
    app produced the wrong value/shape.
- **MEDIUM** — deterministic but ambiguous:
  - Count mismatches (`expected 144 to equal 96`) that could be data
    pollution from earlier retries/specs rather than an app defect.
  - A behavior assertion fails and git history shows the app intentionally
    changed (stale test) — the *test* needs fixing, and product should
    confirm the new behavior is intended.
  - An expected UI state never appears (`tokenExpiredBanner` never shown)
    where timing alone probably can't explain it.
- **LOW** — matches a known Cypress-glitch/false-positive family (below), is
  a cascade of one, is an ordering dependency, is a test bug, or passed on
  retry.

When in doubt between two levels, pick the higher one and hedge in the
`failure_cause` text.

## Known Cypress-glitch (false-positive) families → LOW

These recur in this project regardless of app correctness. They are
interaction/timing races in the test driver, not app bugs. **Only apply these
when `error_kind` is `element-timeout` AND the spec's own `first_error`
actually matches the signature** — never attach one of these labels to a
`value-mismatch`/`app-error` spec, and never to a spec whose captured error
doesn't contain the signature text:

| Signature (substring) | Suggested label |
|---|---|
| `Quasar QSelect menu did not filter results within` | `filter-wait no-change (#2745)` |
| `Expected to find option but found no matches` | `dropdown filter race: found no matches (#2744)` |
| `.q-menu[role=listbox]:visible` never found | `dropdown/listbox never opened` |
| `.q-item[role=option]` never found | `dropdown options never rendered` |
| `cy.click() failed because the center of this element is hidden from view` | `element covered by popup/overlay on click` |
| `Could not find a chevron icon` | `dropdown chevron not found (dropdown internal race)` |
| `clearStaleMenuPortals] N portal(s) still active` | `dropdown portal not closing: clearStaleMenuPortals (<field>)` |
| `.multiselect__tags > span` never found | `Vue Multiselect <field> never populated` |
| `i.q-icon.rotate-180` never found (qSelectClose) | `dropdown close race (qSelectClose/tryUntil)` |
| `cy.click() ... no longer attached / page updated` on a `q-item` | `dropdown option detached mid-click` |

**Escalate to MEDIUM** only if the same field in the same test fails across
multiple retries AND pipelines (a permanently-empty dropdown can be a real
data/API bug — check whether the options are supposed to come from an API
response).

## Cascades (downstream of a stuck step) → LOW

| Signature | Suggested label |
|---|---|
| `q-stepper__step-content ... continuously found` | `cascade of dropdown/form failure (stepper)` |
| `cy.filter() failed because it requires a DOM element` | `cascade: validate-field cy.filter notification` |
| `[data-cy=...] but never found it` after an earlier failure | `cascade of dropdown/form failure` |
| `[data-cy="Kitchen Sink TEST Project"]` never found | `cascade: project/Kitchen Sink not found` |

Careful: only label a cascade when there IS an upstream failure in the same
spec (check `signatures` order and the trace). A "Kitchen Sink project not
found" as the *first* failure of a spec is not a cascade — dig into it.

## Pre-existing / environment (usually unrelated) → LOW

| Signature | Suggested label |
|---|---|
| `Failed to publish` / `Failed to upload to core` / `Org is unavailable` / `Failed while doing fetch()` / `No response from server` / `Bad request` | `publish/upload flake (pre-existing)` |
| `Collection has already been submitted` / `is not unique` | `publish flake: duplicate/already-submitted (pre-existing)` |
| sync test long timeout (`720000ms`, `should skip the submitted collection`) | `sync flow timeout (pre-existing)` |

## Ordering dependencies → LOW

| Signature | Suggested label |
|---|---|
| `Missing previous surveys. Please run the survey spec first` | `ordering dependency cascade (needs prior survey)` |
| `No past record for <X> to test against` | `ordering dependency cascade (needs prior spec)` |

## Test / selector bugs (fix the test, not the app) → LOW

| Signature | Suggested label |
|---|---|
| `cy.scrollIntoView() can only be used to scroll to 1 element, you tried to scroll to N` | `test bug: scrollIntoView matched multiple elements` |
| `cy.submit() can only be called on a single form. Your subject contained N form elements` | `test bug: cy.submit matched multiple forms` |
| `subject.as is not a function` | `test bug: subject.as is not a function` |

## App / data signals (candidates for MEDIUM/HIGH — verify against code)

| Signature | Suggested label | Likelihood |
|---|---|---|
| exact-text mismatch: `expected '<text>' to equal '<text> (<suffix>)'` | `app label regression: missing <suffix>` | HIGH |
| `expected [ Array(N) ] to deeply equal [...]` (list contents wrong) | `app/data: wrong <field> contents` | HIGH |
| `Unregistered model case <model>` | `app data-shape change: new model <model> (test helper needs case, or app leak)` | HIGH |
| `Expected not to find content: '<app error text>' but continuously found it` | `app error state: <text> shown` | HIGH |
| `Cannot read properties of undefined (reading '<x>')` | `app/data: undefined <x>` | MEDIUM-HIGH |
| count assertion mismatch e.g. `expected 110 to equal 94` | `data-count mismatch (likely upstream cascade)` | MEDIUM |
| `tokenExpiredBanner` never found | `auth: tokenExpiredBanner (independent)` | MEDIUM |
| route assertion fails (`expected false to be true` from testRoute / navigation observed in command log) | read the test + app code; either `app bug: <guard> not enforced` (HIGH) or `stale test (app behavior changed: <feature>)` (MEDIUM) | MEDIUM-HIGH |

## Fallbacks

- A dropdown selection that times out on `cy.click()` where the element isn't
  clearly an option → `dropdown interaction timeout (likely <relevant ticket>)`, LOW.
- Anything you can't pin → `OTHER: <short error>`, MEDIUM (unknowns are worth
  a human look), and flag it to the user.
