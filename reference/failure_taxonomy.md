# Failure-cause taxonomy

A reference for labelling each failed spec's `failure_cause`. Match the captured
error (`failures_raw.json` → `first_error` / `signatures`) to a bucket, then write
a concise label. Prefer a **root cause** over a downstream symptom, and hedge
(`(likely ...)`) when the evidence is partial.

This list is meant to grow — add recurring signatures your team sees.

## Golden rules

1. **Root vs cascade.** One stuck interaction early in a form makes everything
   after it fail (`element never found`, `stepper step-content continuously
   found`, `cy.filter() requires a DOM element`). Label those `cascade of ...`,
   not as their own bug.
2. **Flaky vs hard.** `Passed on retry: yes` in the CSV → `flaky (passed on retry)`.
3. **Related vs pre-existing.** Keep environment/ordering/test-bug failures out of
   the headline cluster.
4. **Be honest about confidence.** `(likely upstream cascade)` is a valid label.

## Buckets and signatures

### Dropdown / select (paratoo-fdcp specific)
| Signature (substring) | Suggested label |
|---|---|
| `Quasar QSelect menu did not filter results within` | `filter-wait no-change (#2745)` |
| `Expected to find option but found no matches` | `dropdown filter race: found no matches (#2744)` |
| `clearStaleMenuPortals] N portal(s) still active` | `dropdown portal not closing: clearStaleMenuPortals (<field>)` |
| `.multiselect__tags > span` never found | `Vue Multiselect <field> never populated` |
| `i.q-icon.rotate-180` never found (qSelectClose) | `dropdown close race (qSelectClose/tryUntil)` |
| `cy.click() ... no longer attached / page updated` on a `q-item` | `dropdown option detached mid-click` |

### Cascades (downstream of a stuck step)
| Signature | Suggested label |
|---|---|
| `q-stepper__step-content ... continuously found` | `cascade of dropdown/form failure (stepper)` |
| `cy.filter() failed because it requires a DOM element` | `cascade: validate-field cy.filter notification` |
| `[data-cy=...] but never found it` after an earlier failure | `cascade of dropdown/form failure` |
| `[data-cy="Kitchen Sink TEST Project"]` never found | `cascade: project/Kitchen Sink not found` |

### Pre-existing / environment (usually unrelated to the cluster)
| Signature | Suggested label |
|---|---|
| `Failed to publish` / `Failed to upload to core` / `Org is unavailable` / `Failed while doing fetch()` / `No response from server` / `Bad request` | `publish/upload flake (pre-existing)` |
| `Collection has already been submitted` / `is not unique` | `publish flake: duplicate/already-submitted (pre-existing)` |
| sync test long timeout (`720000ms`, `should skip the submitted collection`) | `sync flow timeout (pre-existing)` |

### Ordering dependencies (one spec needs another to run first)
| Signature | Suggested label |
|---|---|
| `Missing previous surveys. Please run the survey spec first` | `ordering dependency cascade (needs prior survey)` |
| `No past record for <X> to test against` | `ordering dependency cascade (needs prior spec)` |

### Test / selector bugs (fix the test, not the app)
| Signature | Suggested label |
|---|---|
| `cy.scrollIntoView() can only be used to scroll to 1 element, you tried to scroll to N` | `test bug: scrollIntoView matched multiple elements` |
| `subject.as is not a function` | `test bug: subject.as is not a function` |

### App / data
| Signature | Suggested label |
|---|---|
| `Cannot read properties of undefined (reading '<x>')` | `app/data: undefined <x>` |
| count assertion mismatch e.g. `expected 110 to equal 94` | `data-count mismatch (likely upstream cascade)` |
| `tokenExpiredBanner` never found | `auth: tokenExpiredBanner (independent)` |

### Fallbacks
- A dropdown selection that times out on `cy.click()` where the element isn't
  clearly an option → `dropdown interaction timeout (likely <relevant ticket>)`.
- Anything you can't pin → `OTHER: <short error>` and flag it to the user.
