# Add Emails (Commercial Pipeline)

Lets Sales/Admin/Super Admin users browse a live monitored mailbox from
inside a pipeline entry and attach one email — its documents become real
`Document` rows on the project, and the email itself is kept as a
`PipelineEmail` for reference. Nothing is ever written to the mailbox
itself; this is read-only against Microsoft Graph.

## Where it lives

- **"Add Emails"** button on an existing project's detail page →
  `projects/link-email/` (`link_pipeline_email`).
- **"Add Emails"** button on the *create* form (before the project exists)
  → `projects/link-email/new/` (`link_pipeline_email_new`) — picking an
  email there just carries it back to the create form; it's only turned
  into a real `PipelineEmail` once the project is actually saved.

Restricted to Sales, Admin, and Super Admin (`_can_use_pipeline_email_feature`
in `projects/views.py`). Everyone else can still see an already-linked
email / the documents it created on the project page — they just can't
pick, attach, or delink one.

## Settings (erp_leap/settings.py / .env)

| setting | required | meaning |
|---|---|---|
| `MS_TENANT_ID` | yes | Azure AD tenant of the app registration |
| `MS_CLIENT_ID` | yes | that app registration's client id |
| `MS_CLIENT_SECRET` | yes | that app registration's client secret |
| `PIPELINE_EMAIL_MAILBOX` | only if no `MonitoredMailbox` rows exist | legacy single-mailbox fallback (see below) |

**Azure AD permission needed:** `Mail.Read`, under **Application permissions**
(not Delegated — there's no signed-in user in this flow, the app itself
reads the mailbox), with **admin consent granted**. This is an app-only
grant: it isn't scoped to one mailbox by default, so once granted the app
registration can technically read *any* mailbox in the tenant, not just the
ones configured below — narrowing that (an Exchange **Application Access
Policy**) is a separate, optional hardening step, not required for this
feature to work.

If `MS_TENANT_ID`/`MS_CLIENT_ID`/`MS_CLIENT_SECRET` aren't set, or the
Graph permission isn't consented, every inbox load fails gracefully with
an on-page error (never a 500) — but the feature is unusable until they
are.

## Which mailbox — and adding more than one

**"Add Emails" was never about each person's own inbox.** It reads from
one or more *shared* mailboxes configured centrally (e.g. a
`quotes@leap-arabia.com`-style address vendors send documents to) —
anyone with access to the feature can browse and attach from whichever
mailbox is selected, it isn't "my email."

Two ways a mailbox gets configured:

1. **Legacy / simplest — one mailbox for everyone.** Set
   `PIPELINE_EMAIL_MAILBOX` in the environment. This is used automatically
   as long as no `MonitoredMailbox` rows exist. Zero extra setup.
2. **Multiple mailboxes** (e.g. one per department). In Django admin,
   under **Projects → Monitored mailboxes**, add one row per mailbox:
   a short **label** (e.g. "Sales - Vendor Quotes") and its **email
   address**. As soon as one active row exists, it (and any others)
   replaces the legacy setting entirely — `PIPELINE_EMAIL_MAILBOX` is
   then ignored.
   - **One mailbox configured:** nothing changes for the user — "Add
     Emails" just reads that one, same as before.
   - **More than one active:** a mailbox picker (dropdown) appears at
     the top of "Add Emails," and the user picks which one to browse
     before seeing its inbox.
   - Untick **Active** on a row to stop it appearing in the picker
     without deleting its history — every email already attached from it
     keeps a record of which mailbox it came from
     (`PipelineEmail.source_mailbox`), so nothing already attached is
     affected.
   - No new Azure AD consent is needed to add another mailbox — the
     `Mail.Read` grant above already covers any mailbox in the tenant;
     adding a row here is purely "which addresses show up in the
     picker," not a permissions change.

Every mailbox address that ever reaches Microsoft Graph from this feature
is validated against the active `MonitoredMailbox` list (or the legacy
setting) server-side — a user can never browse or fetch from an arbitrary
mailbox by editing a URL or form field (`_resolve_mailbox()` in
`projects/views.py`).

## For Sales/HR: how to actually use it

1. Open a pipeline entry (or start creating a new one) and click **Add
   Emails**.
2. If more than one mailbox is configured, pick one from the dropdown at
   the top — the inbox list below updates to show that mailbox's recent
   emails with attachments.
3. Click a document's name to preview/download it on its own, before
   deciding to attach anything.
4. Pick the right document type for each attachment from its dropdown,
   then click **Attach** (or **Use This Email** on the create form).
5. The email's attachments now show up under **Project Documents**, and
   the email itself is shown as the pipeline entry's linked email.
   Attaching a different email later replaces which one is "linked," but
   never removes documents already added from a previous one.

If "Add Emails" shows an error instead of an inbox, that's a setup problem
(see Settings above), not something fixable from the UI — check with
whoever administers the ERP's Microsoft Graph configuration.
