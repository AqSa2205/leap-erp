# Architecture audit — remediation plan

**Audit:** external engineering audit, 8 September 2026, against `main` @ `de67daa`.
23 findings, 10 rated P1, 10 reproduced by working probes.

**Status of this document:** the plan of work. One task at a time, each with an
acceptance check that can be run. Nothing reaches `main` without an explicit
go-ahead.

---

## What was verified before planning

The audit was not taken on trust. The highest-stakes findings were reproduced
locally first, because the sequencing depends on which are actually live.

| Finding | Verified | Result |
|---|---|---|
| F01 media route has no object authorization | yes | `erp_leap/urls.py:51-57` — `@login_required` and nothing else |
| F05 destructive actions accept GET | yes | all three delete views, zero `require_POST` |
| F07 deploy reloads 311 project records | yes, with the audit's own acceptance check | **see correction below** |
| F21 view modules oversized | yes | 5,231 / 5,075 / 4,573 lines — matches an independent review |
| F22 suite not green | yes | 13 errors, all `UNIQUE accounts_role.name` |

### Correction to F07 — better and worse than reported

The audit states a redeploy "can revert edited project fields". Running its own
acceptance check on a disposable database shows the fixture **fails to load at
all**:

```
DeserializationError: Problem installing fixture 'fixtures/lna_data.json':
Error deserializing object: Project has no field named 'epc'
```

The fixture is stale against the model, and `build.sh:72` ends in
`|| echo "LNA data may already exist"`, so the failure is swallowed and the
deploy log reports success.

Two consequences, and they pull in opposite directions:

- **Production data is not being reverted today.** The risk is latent, not
  active. This lowers F07's urgency from "actively destroying data" to "loaded
  gun".
- **It is a loaded gun.** All 325 fixture objects carry explicit primary keys.
  Anyone who regenerates or repairs that fixture turns on silent overwriting of
  311 projects on the very next deploy. And the deploy has been reporting a
  successful initialisation that did not happen, for however long `epc` has
  been absent.

### Ownership note on F22

The 13 failing tests are caused by a change made in this project on 3 September:
migration `accounts/0032_create_original_roles` (#206) seeds `sales_rep`, and
three KPI test classes then call `Role.objects.create(name=Role.SALES_REP)`,
which collides with the unique constraint. The audit found the symptom; the
cause was introduced here. It is the first task in the plan.

---

## Sequencing principle

The audit's phase order — access and data, then write consistency, then
structure — is sound and is followed. Three refinements:

**F22 comes first, not alongside.** Every fix below lands blind while 13
assertions never execute and there is no CI. It is also the cheapest item in
the plan.

**Phase 1 is one piece of work, not five.** F01, F02, F03, F09 and F10 share a
single root cause: there is no shared object-access policy, so each entry point
invents its own. Fixing them one at a time reproduces the pattern that created
them. Build the policy once, route every entry point through it.

**Structural work is deferred.** Work already in flight (#220, #221, #222) is
F21, which is phase 3. It was the right answer to a different question, and the
audit reorders it. See *Work in flight* below.

---

## Phase 0 — restore the safety net and disarm what is latent

Small, low-risk, high-value. Nothing here changes business behaviour.

### 0.1 Make the suite green — F22
Three sites, `kpis/tests.py:189`, `:356`, `:427`, change `Role.objects.create`
to `get_or_create`. The tests are asserting against a database that now seeds
those roles.
**Acceptance:** full suite from an empty migrated database, zero errors; the
13 KPI assertions actually execute.
**Size:** S

### 0.2 Stop the deploy lying — F07
Remove `loaddata fixtures/lna_data.json` from `build.sh`. Remove the `|| echo`
that swallows initialisation failures. If a fresh environment needs seed data,
that is a one-time bootstrap command, not something every deploy re-runs.
**Acceptance:** edit a seeded project and status in a disposable database, run
the deployment initialisation twice, confirm the edits survive; a failing
initialisation must fail the build rather than print reassurance.
**Size:** S

### 0.3 Close the GET deletes — F05
`require_POST` on `company_document_delete`, `employee_document_delete`,
`vehicle_document_delete`; replace the delete anchors with CSRF-protected
forms.
**Acceptance:** GET and HEAD return 405 with no side effect; POST without a
CSRF token is rejected; authorized POST succeeds.
**Size:** S

### 0.4 Align the runtime — F08
`Dockerfile` declares Python 3.11 and `render.yaml` 3.11.0, while `runtime.txt`
says 3.12.0 and the installed Django requires ≥3.12. The container build cannot
succeed as declared.
**Acceptance:** build each deployment path from scratch, record the resolved
Python and Django versions, require `manage.py check` to pass.
**Size:** S

### 0.5 Turn the audit probes into tests
The audit reproduced 10 findings with probes. Each becomes a test that asserts
denial or safe behaviour, so the fixes below are demonstrable and stay fixed.
**Size:** M — and it is the item that makes every later phase verifiable.

---

## Phase 1 — access

### 1.1 A shared object-access policy — F01, F02, F03, F09, F10
One `policies.py` plus per-domain scoped selectors
(`visible_finance_projects`, `visible_proposals`, `visible_quotation_imports`),
and every read, mutation and export resolved through them. Specifically:

- **F01** private downloads routed through the owning model; R2 URLs signed
  only after authorization; public assets separated
- **F02** proposal exports resolved through `visible_proposals(user)`
- **F03** `project_schedule`, `project_cash_outflow`, `approve_margin` scoped
- **F09** module capability *and* object scope as separate mandatory checks on
  every child route, not just the list view
- **F10** `kpis.activity` enforced on every activity entry path, with an
  authorized user-ID scope passed into the builder

**Acceptance:** for each file-bearing and detail-bearing model, parameterised
tests over owner, allowed role, unrelated employee, foreign region, and
anonymous — including the canonical `/media/` URL and R2 signing. Detail and
every export format must give the same authorization answer.
**Size:** L. This is the largest single item in the plan and the one that most
reduces risk.

### 1.2 Sanitise rich text — F04
Server-side HTML/CSS allowlist on every rich-text write path; sanitise existing
content; stop relying on the editor.
**Acceptance:** script tags, event handlers, `javascript:` URLs and SVG
payloads rejected through forms, AJAX, templates and exports, while legitimate
Word/TinyMCE formatting survives.
**Size:** M

---

## Phase 2 — integrity of writes

| # | Finding | Work | Size |
|---|---|---|---|
| 2.1 | **F06** rollback loses files | capture the old key, delete after commit, outbox for durability, check shared references | M |
| 2.2 | **F11** approval replaced by unapproved M5 | POST-only transition, stage gate, approved snapshot (amount, currency, revision, actor, time) | M |
| 2.3 | **F12** posted totals trusted, partial saves | validate whole submission, derive totals server-side, atomic write, version check | M |
| 2.4 | **F13** quotation review unscoped and repeatable | shared visibility policy, locked single extracted→converted transition | M |
| 2.5 | **F14/F15** leave and timesheet races | lock the parent row before reading state; unique `(year, month)` after dedup | M |
| 2.6 | **F19** missing rate silently 1:1 | explicit conversion result with a missing-rate state; never a silently relabelled number | S |
| 2.7 | **F20** export formula injection | shared export helper forcing untrusted text to string cells | S |
| 2.8 | **F16** attendance is self-reported | treat as self-reported unless attestation is added; validate types, reject inactive users, serialise aggregation | M |

---

## Phase 3 — isolation and structure

| # | Finding | Work | Size |
|---|---|---|---|
| 3.1 | **F17** email on daemon threads | transactional outbox, bounded worker, send after commit, durable retry | M |
| 3.2 | **F18** slow integrations occupy both workers | move AI extraction and large document generation to durable jobs with deadlines | L |
| 3.3 | **F21** domain rules inside view modules | per-domain policies, selectors, services, calculations, renderers; import-boundary tests | L |
| 3.4 | **F23** production config fallbacks | fail fast on missing `DATABASE_URL` and storage config for non-blueprint starts | S |

---

## Work in flight

Four pull requests are open and unmerged. All predate the audit.

| PR | What | Recommendation |
|---|---|---|
| #220 | shared front-end CSRF/fetch helper | **merge** — no production behaviour change |
| #221 | PO pagination characterization tests | **merge** — tests only |
| #222 | PO PDF extracted from views, duplication and page-geometry fixes | **merge when ready to see it** — it shifts the supplier PDF's columns by ~2 mm |
| #212 | PMO department (milestones, delivery board, importer) | **hold** — it adds a new module with new views and new file handling while F01 is open; landing it now means auditing it twice |

#222 is stacked on #221 and needs retargeting to `dev` once #221 merges.

---

## How progress is judged

Not by findings closed. By these, in order:

1. The suite runs green from an empty database, in CI, on every push.
2. Every audit probe has become a test that expects denial.
3. A redeploy provably preserves edited records.
4. One policy decides object access, and a test enumerates module URLs against
   it.
5. No financial approval can change without an explicit, recorded transition.

A finding marked fixed without its acceptance check run is not fixed.
