# Glass design system (summary)

Minimal, Apple-like UI. Implemented in `tokens.css` + `components.css` (`mf-*` classes).

## Tokens

See `tokens.css` for `--mf-bg`, `--mf-accent`, glass material (`--mf-glass-*`), typography scale, spacing, and radius.

Light and dark sets apply via `html[data-theme="light"|"dark"]` and `prefers-color-scheme` when no explicit theme is set.

## Primitives

- **`.mf-page`**: full-height shell
- **`.mf-glass`**: frosted cards and nav clusters
- **`.hub-backdrop`**: soft colour blobs behind content
- **`.hub-header`**: bold title, muted subtitle, glass nav pill
- **`.mf-btn`** / **`.mf-btn--primary`** / **`.mf-btn--ghost`**
- **`.mf-field`**, **`.mf-card`**, **`.mf-cluster`**, **`.mf-badge`**

Theme persistence uses `localStorage` key **`hub-theme`**.
