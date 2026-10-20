# Vendored design system

| File | Purpose |
|------|---------|
| `tokens.css` | Colour, type, spacing, radius and glass tokens (light + dark) |
| `components.css` | `mf-*` primitives (buttons, fields, cards, clusters) |
| `mf-design.css` | Import stub + legacy aliases used by `jobkit-cv-print.css` |
| `hub-shell.css` | Page shell: backdrop blobs, header, main column |
| `docx.bundle.mjs` | Browser build of `docx` (`npm run build:docx`) |
| `pdf.min.mjs`, `pdf.worker.min.mjs`, `mammoth.browser.min.js` | PDF / Word text extraction in the browser |

Job-specific layout lives in `../jobkit-tracker.css`; the setup wizard and sign-in page add `../setup.css`.

Theme persistence uses `localStorage` key **`hub-theme`** (see `jobkit-theme.js`).
