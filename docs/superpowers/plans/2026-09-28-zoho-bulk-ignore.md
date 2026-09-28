# Ignoring a list of Zoho accounts in one go

## Why

Finance keeps the "don't map these" list outside the ERP — employee current
accounts, personal and OPEX accounts, Zoho Books' own default expense
categories, excise-tax accounts. The list that prompted this was 57 names.

The mapping worklist ignores one row at a time, 50 to a page. Fifty-seven by
hand means hunting each row by eye across several pages, and one mis-tick is
never noticed: the wrong row drops off the worklist and the right one stays,
looking like work nobody has got to yet.

## The hard part is the names, not the write

A list typed by hand does not match the system. From the real list:

| pasted | Zoho has |
|---|---|
| `accured sagia fees` | Accrued SAGIA Fees |
| `STCPAY Bussiness` | STCPAY Business |
| `Sharib ABbbas` | Sharib Abbas |
| `M Zeeshan Shezad` | M Zeeshan Shahzad |
| `partners equity Mr ALi Current Account` | Partner's Equity- Mr Ali Current Account |

It is tempting to auto-correct those. **`M Shahab anwar` is why we don't.** Zoho
has no account of that name — it has `M Shahab Anwar OPEX` and
`M Shahab Anwar Personal`, and nothing else. A resolver confident enough to fix
the other fourteen would have picked one of those two, silently, and had a
50% chance of ignoring the wrong one.

So this follows the line `accounting/mapping.py` already draws for the same
reason: **an exact name match is safe to act on, everything else is shown to a
person.** Exact matches arrive pre-ticked; near misses arrive listed with their
candidates and **unticked**.

On the real 57: **43 pre-ticked, 14 offered**, and in all 14 the intended
account is the first candidate — so it is fourteen ticks rather than
fifty-seven searches.

## Two states are reported, not acted on

| | |
|---|---|
| `ALREADY_MAPPED` | the row points at an ERP account. Ignoring it would throw that mapping away, which a list of names did not ask for. |
| `ALREADY_IGNORED` | nothing to do. Counted so the summary adds up to the list that was pasted. |

Every pasted name appears in exactly one group, including the ones that match
nothing — a name that quietly vanished from the preview is the worst outcome
here, because it reads as dealt with. There is a test for precisely that.

## Shape

`accounting/bulk_ignore.py` is pure — `parse_names`, `resolve`, `group`,
`ignorable_pks` — so the preview and a test can both call it, and the tests use
plain objects with three attributes rather than database fixtures.

Two views, preview then apply, like the chart import beside it. The preview
writes nothing. **Apply re-checks every row rather than trusting the preview**:
a row that has been mapped since the page was rendered is left alone, because a
stale page is exactly when that happens. It keeps the invariant the per-row save
keeps — ignored means unmapped, never both — and stamps each note with who did
it and when, so the Ignored filter is a record rather than a disappearance.

## Verification

- **31 tests.** The resolver against the real shapes: typos, the OPEX/Personal
  pair a typo sits between, the same name twice in Zoho, a name never synced,
  double spaces in the pasted list. The views for the two things a bulk write
  must not do — act on a near miss, or discard a mapping.
- **13 guards mutation-tested, all killed**, including auto-resolving a near
  miss, ignoring a mapped row, dropping a group from the preview, and skipping
  the audit note.
- No migrations. `is_ignored` already existed on `ZohoAccountMap`; this is the
  first way to set it for more than one row at a time.
- Gated by `_can_view_accounting` — finance and super admin — the same gate as
  the rest of the chart and the per-row save.

## Note on the chart import

The list that prompted this was first read as a set of accounts to drop from
`ERP final_Babar.xls`. It is not: **0 of the 57 match an ERP chart account** and
43 match a Zoho one. The imported chart needs no change. Worth recording
because the two screens use the same word for different things — the chart
importer's **deactivate missing** retires an ERP account, while **ignore** here
only says a Zoho account has no home in the chart.
