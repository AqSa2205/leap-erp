# PDF fonts

## `Amiri-Regular.ttf` (required for Arabic in the PO PDF header) — NOT committed

The Purchase Order PDF header renders the Arabic company name
(شركة لييب نتوركس أرابيا) using an Arabic-capable TrueType font placed here as:

    static/fonts/Amiri-Regular.ttf

The font is intentionally **not committed** to the repo — pick one with a
license that permits redistribution and drop the file at the path above:

- **Amiri** (recommended, SIL Open Font License): https://fonts.google.com/specimen/Amiri
  → download, unzip, copy `Amiri-Regular.ttf` here.
- Or any other Arabic-capable TTF (e.g. Noto Naskh Arabic, Cairo) renamed to
  `Amiri-Regular.ttf`, as long as its license allows redistribution.

Do **not** commit a system font (Arial, Tahoma, Dubai, …) — those are not
redistributable.

If the file is absent the PO PDF still renders — the Arabic line is simply
omitted (see `procurement.views._arabic_font`). After adding the font, run
`python manage.py collectstatic` so it ships in production.
