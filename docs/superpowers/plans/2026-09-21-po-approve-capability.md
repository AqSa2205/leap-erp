# Approving a purchase order is now a permission

## Why

`po.approve` has been in the capability registry since the grid was built,
declared `enforced=False` — "defined, wiring pending". Nothing read it. Who
could sign a PO stage was decided entirely by `can_user_approve_stage()`, which
hardcoded roles:

```
SCM  → procurement manager
PM   → admin
COO  → admin
CEO  → super_admin only
```

So the permission grid showed an **Approve PO** toggle that did nothing, and
letting the Project Manager sign the PM stage meant editing a model.

## What changed

Approving now asks two questions, and both must pass.

**May this role sign anything?** — the `po.approve` capability, toggled per role
in the grid. That is the part that becomes an administrative decision.

**Which stage is theirs?** — the mapping in the model, with the Project Manager
added to the PM stage. This part deliberately stays in code: the whole reason a
PO has four stages is that one person should not hold the chain, and a single
switch that granted all four would quietly undo that. A role granted
`po.approve` with no stage of its own can sign nothing, and there is a test
saying so.

Super admin still passes both, as the standing override that stops work
stalling when somebody is away.

## Nobody loses approval

The capability was never read, so every role's row for it sits at **OFF** on a
live database. The moment the gate starts reading it, OFF means refused — so
deploying the wiring alone would have taken approval away from Admin and the
procurement manager and stopped every purchase order in flight at its current
stage.

Migration `0037_grant_po_approve` turns it ON for the roles that could already
sign. That is what makes this a wiring change rather than a change of who
approves; Project Manager is the one addition, which is the point.

The same migration turns on `po.access` and `po.nav` for Project Manager.
`DEFAULT_MODULE_ACCESS` only seeds rows that are **missing**, and that role's
rows already exist set OFF — so without the migration the grid would say they
may approve while the Purchase Orders section stayed invisible to them. There
is no shell on production, so anything not done by a migration cannot be done
at all.

## The three other places that had to follow

Being allowed to sign is not the same as being able to:

| | |
|---|---|
| `_visible_pos_for()` | a PO they cannot open is a PO they cannot sign — the approve endpoint scopes by this queryset, so a project manager gets the same regional view Admin and Manager already have |
| `is_designated_approver()` | otherwise the PO never appears in their "waiting on me" list and nobody knows it is theirs |
| `stage_recipients()` | otherwise the approval email still only goes to Admin |

Regional, not global: a project manager sees purchase orders they created plus
any whose project is in their region — the rule Admin and Manager already
follow, rather than a new one invented here.

## Verification

- **28 tests**, including a project manager signing the PM stage **at the
  endpoint**, not only in the model — hiding the Approve button is not a
  permission, the URL is still there.
- Both directions of the toggle: granting it lets a project manager sign, and
  revoking it takes the PM stage off an Admin who had it. A grid that only ever
  adds is decoration.
- The two seeding paths are tested **separately** - the defaults, which reach a
  brand-new database, and the migration, which is the only thing that reaches a
  running one. A first pass had both covered by the same tests, and mutation
  showed either could be deleted without a failure: in a test database both run,
  so each hid the other's absence. Production only has the second.
- **16 guards mutation-tested**, including the two that matter
  most: a capability that grants every stage, and a project manager quietly
  gaining the COO stage.

## Not done

The other granular PO capabilities — create, edit, delete, export — are still
declared and unwired; `_module()` now takes `enforced_actions` so each can be
turned on as it is wired, rather than the grid promising toggles that do
nothing. Wiring them means auditing the CRUD views' existing role checks, which
is its own piece of work.
