"""Resolving a pasted list of Zoho account names to rows to mark ignored.

Finance keeps the list of "don't map these" in a spreadsheet or an email -
employee current accounts, personal accounts, Zoho's own default expense
categories - and the mapping worklist ignores one row at a time, fifty to a
page. Marking fifty-seven by hand means hunting each row by eye, which is both
tedious and the kind of task where one mis-tick is never noticed.

The hard part is not the bulk write, it is the names. A list typed by hand does
not match the system: `accured sagia fees` for `Accrued SAGIA Fees`,
`STCPAY Bussiness` for `STCPAY Business`, `Sharib ABbbas` for `Sharib Abbas`.

This module follows the line `mapping.py` already draws, and for the same
reason: an exact name match is safe to act on, and everything else is shown to
a person. It deliberately does NOT auto-correct a near miss. `M Shahab anwar`
looks like one typo, but the chart holds `M Shahab Anwar OPEX` and
`M Shahab Anwar Personal` and nothing else - a resolver confident enough to
pick one would have picked the wrong one. Ignoring the wrong account is quiet:
the row drops off the worklist and the real one stays, looking like work
nobody has got to yet.

Two states are reported rather than acted on, because both mean the list and
the system disagree about something worth knowing:

  ALREADY_MAPPED  the row points at an ERP account. Ignoring it would mean
                  throwing that mapping away, which is not what a list of
                  names asked for.
  ALREADY_IGNORED nothing to do. Counted so the summary adds up to the list
                  that was pasted.
"""
import difflib

from .mapping import MAX_CANDIDATES, SIMILARITY_FLOOR, normalise

# Outcome per pasted name.
MATCHED = 'matched'                   # one unmapped row, exact name - tick it
ALREADY_IGNORED = 'already_ignored'   # no-op
ALREADY_MAPPED = 'already_mapped'     # refused: would discard a mapping
SIMILAR = 'similar'                   # close names offered, none ticked
DUPLICATE = 'duplicate'               # the name matches several rows exactly
NONE = 'none'                         # nothing close enough to show

# The order the preview lists them: what will happen, then what needs a
# decision, then what the list got wrong.
GROUP_ORDER = (MATCHED, SIMILAR, DUPLICATE, ALREADY_MAPPED, ALREADY_IGNORED,
               NONE)

GROUP_LABELS = {
    MATCHED: 'Will be ignored',
    SIMILAR: 'Close, but not exact — pick the right one',
    DUPLICATE: 'Matches more than one account — pick which',
    ALREADY_MAPPED: 'Already mapped to an ERP account — left alone',
    ALREADY_IGNORED: 'Already ignored — nothing to do',
    NONE: 'No account of that name',
}


def parse_names(raw):
    """One name per line, blanks dropped, duplicates collapsed.

    Order is kept so the preview reads in the order somebody pasted, which is
    how they check it against the list they pasted from. A repeated name is
    collapsed rather than reported: a list with the same account twice is not
    an error worth a line of output.
    """
    seen, out = set(), []
    for line in (raw or '').splitlines():
        name = ' '.join(line.split())
        if not name:
            continue
        key = normalise(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


class Resolution:
    """One pasted name, and what the worklist has to say about it."""

    __slots__ = ('name', 'outcome', 'rows')

    def __init__(self, name, outcome, rows=()):
        self.name = name
        self.outcome = outcome
        self.rows = list(rows)

    @property
    def row(self):
        """The single row, where the outcome names exactly one."""
        return self.rows[0] if len(self.rows) == 1 else None

    @property
    def is_actionable(self):
        """Whether this line offers a person something to tick."""
        return self.outcome in (MATCHED, SIMILAR, DUPLICATE)

    def __repr__(self):                                   # pragma: no cover
        return f'<Resolution {self.name!r} {self.outcome}>'


def resolve(names, rows):
    """Classify each pasted name against the Zoho worklist.

    `rows` is any iterable of ZohoAccountMap. Pure: reads the rows, writes
    nothing, so the preview and the apply step can both call it and a test can
    call it with plain objects.
    """
    by_name = {}
    for row in rows:
        by_name.setdefault(normalise(row.zoho_account_name), []).append(row)
    keys = list(by_name)

    out = []
    for name in names:
        key = normalise(name)
        exact = by_name.get(key)

        if exact and len(exact) > 1:
            out.append(Resolution(name, DUPLICATE, exact))
            continue

        if exact:
            row = exact[0]
            if row.account_id is not None:
                out.append(Resolution(name, ALREADY_MAPPED, exact))
            elif row.is_ignored:
                out.append(Resolution(name, ALREADY_IGNORED, exact))
            else:
                out.append(Resolution(name, MATCHED, exact))
            continue

        close = difflib.get_close_matches(
            key, keys, n=MAX_CANDIDATES, cutoff=SIMILARITY_FLOOR)
        if close:
            candidates = [r for k in close for r in by_name[k]]
            out.append(Resolution(name, SIMILAR, candidates))
        else:
            out.append(Resolution(name, NONE))
    return out


def group(resolutions):
    """{outcome: [Resolution, ...]} in GROUP_ORDER, empty groups omitted."""
    buckets = {}
    for res in resolutions:
        buckets.setdefault(res.outcome, []).append(res)
    return {k: buckets[k] for k in GROUP_ORDER if k in buckets}


def ignorable_pks(resolutions):
    """Rows the preview will tick for the user: the exact, unmapped matches.

    A SIMILAR or DUPLICATE line offers candidates but ticks none - choosing
    between them is the part that must not happen automatically.
    """
    return [res.row.pk for res in resolutions if res.outcome == MATCHED]
