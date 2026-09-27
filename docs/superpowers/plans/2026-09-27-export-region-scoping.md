# The procurement exports ignored region scoping

## What was wrong

Delivery notes, inventory reports and FRC/PPE reports each had a scoped list
and a scoped detail view — and an **unscoped export**:

```python
@login_required
def dn_export_excel(request, pk):
    dn = get_object_or_404(DeliveryNote, pk=pk)      # no scoping at all
```

Six endpoints in that shape: `dn_export_excel`, `dn_export_pdf`,
`inventory_export_excel`, `inventory_export_pdf`, `frc_export_excel`,
`frc_export_pdf`.

So the detail page refused a record from another region with a 404, while the
export button beside it handed the whole thing to **any authenticated
account**. Measured on a delivery note belonging to a Western-region project,
with the viewer in Eastern:

```
PM        detail=404  export=200  bytes=5726
sales rep detail=404  export=200  bytes=5726
```

Walking the pk was enough. Pre-existing, live on production, and found while
reviewing an unrelated PR that widened who reaches these pages.

## Why it happened, and what changed

The region rule was written out **four times** — once per list mixin, plus
`_visible_pos_for` for purchase orders. Purchase-order exports were safe
precisely because `_visible_pos_for` exists and they use it; the other three
record types had no such helper, so the exports had nothing to call and
silently did without.

The rule now lives in one place, `_scoped_by_region(qs, user)`, with three thin
accessors (`_visible_dns_for`, `_visible_inventory_for`, `_visible_frc_for`).
The three list mixins delegate to them, and the six exports fetch through them:

```python
dn = get_object_or_404(_visible_dns_for(request.user), pk=pk)
```

Nothing about who may see what changed — super admin and procurement see every
region, admin and manager see their own region plus what they created, everyone
else sees what they created. What changed is that the exports now ask the same
question the page does.

## How it is tested

The tests assert an **agreement between the two responses** rather than a list
of expected status codes, because the defect was never "the export returns the
wrong number" — it was "the export answers a question the detail page had
already refused". `test_every_viewer_gets_the_same_answer_from_both` walks six
viewer kinds across all three record types and asserts `detail == 200` if and
only if both exports are 200.

Alongside that: the leak itself from three roles, the legitimate cases that
must keep working (procurement, super admin, a manager **in** the record's
region, and the record's own creator), anonymous visitors going to login rather
than being handed a file, and a missing pk still 404ing for somebody allowed
everything — so the fix cannot turn "not found" into "not permitted" and hide
real 404s.

**Eight mutants, all killed**, including one export left unfixed while its
sibling is fixed, and the rule both widened and narrowed. One survived the
first pass: removing the anonymous short-circuit, which no URL can reach
because every caller is behind `login_required`. It is now pinned at the
function level instead — `_visible_pos_for`, the helper these mirror, **is**
called with an anonymous user, because the sidebar badge runs from a context
processor on every page including the login screen.

## Still open

`procurement_dashboard`, `_scoped_items_for_summary` and
`_can_see_all_projects` each keep their own copy of a similar rule. They are
not security holes — they narrow rather than widen — but they are why a Project
Manager sees an empty dashboard and an empty Summary on PR #257. Raised there
rather than fixed here, since that PR is what makes those pages reachable.
