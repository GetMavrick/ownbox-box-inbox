# The box's two typefaces

| File | Face | Used for | Licence |
|---|---|---|---|
| `inter-latin-var.woff2` | **Inter** 4.x, variable: `wght` 100–900, `opsz` 14–32 | every word on the box | `OFL-Inter.txt` |
| `geist-mono-latin-var.woff2` | **Geist Mono**, variable: `wght` 100–900 | what a person types or copies: commands, addresses, file names | `OFL-GeistMono.txt` |

**Inter** is the face ownbox.io uses (`sites/ownbox/app/layout.tsx`), with the optical-size axis the
owner chose on 2026-09-15 (*"inspired by apple design"*), so the site and the box read as one product.
**Geist Mono** is the owner's pick, 2026-09-24, from three candidates rendered at 390px.

Both are the **latin subset** builds Google Fonts serves, unmodified, downloaded 2026-09-24:
73 KB and 23 KB. They are served by the box itself at `/ui/font/sans.woff2` and
`/ui/font/mono.woff2` (`core/dash/look.py`) and never from a CDN, for the reason
`marketing/customer_voice/fonts/README.md` gives: a box must draw its own text without asking a third
party, and must not tell one every time it is opened.

Both are under the **SIL Open Font License 1.1**, whose text must travel with the files; it does,
in the two `OFL-*.txt` files beside them, in every box.
