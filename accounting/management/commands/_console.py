"""Make management-command output survive non-ASCII data.

Zoho holds this company's names in Arabic — "شركة لييب نتوركس أرابيا" is the
registered organisation name, and account names carry Arabic too. A Windows
console defaults to cp1252, which cannot encode any of it, and Django's
OutputWrapper writes straight through to sys.stdout. The result is a
UnicodeEncodeError raised *after* the work being reported on has already
succeeded, so a completed sync reads as a crash:

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 45-48

Reporting must not be able to fail the thing it reports on. Commands that print
anything sourced from Zoho call use_utf8_console() first.
"""
import sys


def use_utf8_console():
    """Switch stdout/stderr to UTF-8, replacing anything still unencodable.

    Reconfigures the streams in place, so Django's OutputWrapper — which holds
    a reference to the same objects — picks it up without being rebuilt.

    errors='replace' is the part that actually guarantees the property we want:
    UTF-8 covers Arabic, but a redirected or otherwise pinned stream may refuse
    the encoding change, and a mangled character in a progress line is always a
    better outcome than losing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue                      # not a TextIOWrapper (captured in tests)
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError, AttributeError):
            try:
                reconfigure(errors='replace')
            except Exception:
                pass                      # nothing further to try; leave it alone
