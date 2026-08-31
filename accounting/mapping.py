"""Suggesting which ERP account a Zoho account belongs in.

Zoho Books for this organisation has no account codes at all — the feature was
never switched on — so the code-match automap in `sync_zoho_accounts` proposes
nothing and all 322 accounts arrive unmapped. Names are the only signal left.

Matching on names is exactly where a mapping tool earns its keep or does real
damage, because the chart is full of near-identical pairs: `Adil Abbas` and
`Adil Abbas OPEX` are different accounts, and `Medical Expenses` exists twice in
the ERP chart under different codes (4100013 administrative, 5000009 project).
A confident wrong answer here misposts money silently and is not discovered
until a reconciliation months later.

So this module draws a hard line between two kinds of answer:

  CERTAIN   the normalised name matches exactly one postable ERP account.
            Safe to apply in bulk, because there is nothing to choose between.

  EVERYTHING ELSE  offered to a person as candidates, never applied
            automatically — including the case where the name matches several
            ERP accounts, which looks like the strongest possible signal and is
            in fact the most dangerous one.

The asymmetry is deliberate. Leaving a row unmapped costs somebody a minute;
mapping it wrongly costs a restatement.
"""
import difflib

# Enough of a match to be worth showing a human, not enough to act on. Tuned to
# surface `Accounts Receivable` against `Accounts Receivable - Trade` while
# leaving genuinely unrelated names out of the list.
SIMILARITY_FLOOR = 0.72
MAX_CANDIDATES = 4

CERTAIN = 'certain'          # unique exact name match — bulk-appliable
AMBIGUOUS = 'ambiguous'      # exact match, but to more than one ERP account
SIMILAR = 'similar'          # close, needs a person
NONE = 'none'


def normalise(name):
    """Fold the differences that are never meaningful in an account name.

    Case and internal whitespace only. Deliberately NOT stripping punctuation or
    words like 'OPEX' — those are the very thing that distinguishes one real
    account from another here.
    """
    return ' '.join((name or '').split()).lower()


class Suggestion:
    """What we think a Zoho row maps to, and how much that is worth."""

    __slots__ = ('kind', 'account', 'candidates', 'reason')

    def __init__(self, kind, account=None, candidates=(), reason=''):
        self.kind = kind
        self.account = account
        self.candidates = list(candidates)
        self.reason = reason

    @property
    def is_certain(self):
        return self.kind == CERTAIN

    @property
    def is_actionable(self):
        """Whether a human has anything to click. Distinct from is_certain:
        an ambiguous row is very actionable, just not automatically."""
        return bool(self.account or self.candidates)

    def __repr__(self):                                   # pragma: no cover
        return f'<Suggestion {self.kind} {self.account!r}>'


def index_accounts(accounts):
    """Group postable accounts by normalised name.

    Returned rather than rebuilt per row: the caller usually has hundreds of
    rows to suggest for and the chart does not change underneath them.
    """
    index = {}
    for account in accounts:
        index.setdefault(normalise(account.name), []).append(account)
    return index


def suggest(zoho_name, index):
    """Suggest an ERP account for one Zoho account name."""
    key = normalise(zoho_name)
    if not key:
        return Suggestion(NONE)

    exact = index.get(key, [])

    if len(exact) == 1:
        return Suggestion(CERTAIN, account=exact[0], candidates=exact,
                          reason='Name matches this account exactly, and only this one.')

    if len(exact) > 1:
        # The trap. An exact match to several accounts reads as the strongest
        # signal available and is the one case where guessing is least
        # excusable — the ERP chart deliberately carries the same name under
        # different codes (administrative vs project), and only a person knows
        # which side a given Zoho account belongs on.
        return Suggestion(
            AMBIGUOUS, candidates=exact,
            reason=f'Matches {len(exact)} ERP accounts with this name — pick the right one.')

    close = difflib.get_close_matches(key, index.keys(), n=MAX_CANDIDATES,
                                      cutoff=SIMILARITY_FLOOR)
    candidates = [account for name in close for account in index[name]]
    if candidates:
        return Suggestion(SIMILAR, candidates=candidates[:MAX_CANDIDATES],
                          reason='Nothing matches exactly; these are the closest names.')

    return Suggestion(NONE, reason='No similar account name in the ERP chart.')


def suggest_all(rows, index):
    """Suggestions for many rows, keyed by primary key."""
    return {row.pk: suggest(row.zoho_account_name, index) for row in rows}


def certain_matches(rows, index):
    """The subset safe to apply without asking: unique exact name matches.

    Callers must still restrict `rows` to genuinely unmapped ones — this
    function answers "what does the name say", not "what may be overwritten".
    """
    matches = {}
    for row in rows:
        suggestion = suggest(row.zoho_account_name, index)
        if suggestion.is_certain:
            matches[row.pk] = suggestion.account
    return matches
