# The two typefaces this app ships

Owner, 2026-09-18, choosing between three pairings rendered on the real screen: **"A all the
way."** Archivo for headings, Public Sans for body.

## Why they are here and not on a CDN

A box is **single-tenant, cloned per customer, and installed to a home screen as a PWA**. A
`<link>` to `fonts.googleapis.com` would mean a business's own machine cannot draw its own text
without reaching a third party — on a train, behind a corporate proxy, or on the day that host is
slow. It would also tell that host every time a buyer opens their inbox.

So the files live in the repository and are served by the app itself, from `/inbox/font/…`. Both
are the **latin subset, variable weight**: 35 KB and 27 KB, 62 KB in total for every weight the
app uses, which is less than one of the channel logos would cost as a PNG.

## What they are

| | |
|---|---|
| **Archivo** — headings | Omnibus-Type. Broad and slightly industrial; it reads as equipment rather than as a startup. |
| **Public Sans** — body | USWDS, redrawn from Libre Franklin. A deliberately unremarkable workhorse that stays legible at 14px in a dense conversation row. |

## Licence

Both are under the **SIL Open Font License, Version 1.1**, whose full text sits beside them in
`OFL-Archivo.txt` and `OFL-PublicSans.txt` — the OFL requires the licence to travel with the
files, and these are redistributed in every clone.

Neither file has been modified: they are the latin-subset builds Google Fonts serves, downloaded
once and committed so that no clone has to fetch them again. Re-subsetting or otherwise changing
them means keeping the Reserved Font Name rules of the OFL in view.
