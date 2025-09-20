# Web UI Style Guide

Defines shared design tokens, utilities, and component classes across the Markdownify web app. Use these classes instead of bespoke CSS. If a new variant is required, add a reusable class in `webapp/static/style.css` and document it here before usage.

Location of stylesheet:
- Path: `webapp/static/style.css`
- Included via: `<link rel="stylesheet" href="/static/style.css">` in `templates/base.html`

1) Design Tokens (CSS variables on :root)
- Sizing & spacing
  - `--gap`: 12px
  - `--radius`: 8px
  - `--radius-sm`: 6px
  - `--container`: 1100px (max content width for wide pages)
- Colors
- Foreground/background
  - `--fg`: #1F2937
  - `--muted`: #64748B
  - `--bg`: #F9FBFA
  - `--bg-soft`: #FFFFFF
  - `--card-bg`: #fff
  - Borders
    - `--border`: #E7EDE9
    - `--border-strong`: #D2DCD6
  - Brand
    - `--brand`: #06C167
    - `--brand-ink`: #066A3E
    - `--brand-weak`: #E9F9F0
    - `--brand-hover`: #05A356
  - Status
    - `--green-weak`: #ecfdf5
    - `--green`: #0a7f42
    - `--red-weak`: #fef2f2
    - `--red`: #b91c1c
    - `--orange-weak`: #fff7ed
    - `--orange`: #b45309
    - `--gray`: #64748B
    - `--warning`: #f59e0b
    - `--warning-weak`: #fffbeb
  - Info
    - `--info-weak`: #E0F7FA
    - `--info`: #0F9BA8
  - Background pattern
    - `--bg-grid-size`: 24px
    - `--bg-grid-color`: rgba(231,237,233,0.25)
- Shadows
  - `--shadow-1`: 0 10px 30px rgba(0,0,0,0.12)
  - `--shadow-2`: 0 6px 20px rgba(0,0,0,0.10)
- Focus
  - `--focus-ring`: 0 0 0 3px rgba(15,155,168,0.45)

2) Base Elements
- Body uses the Plus Jakarta Sans font stack (`'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`), line-height 1.45, tinted neutral background var(--bg) with a subtle grid pattern, color var(--fg); edge-to-edge layout (no horizontal body padding). To disable the pattern on a specific page, add class "no-bg-pattern" to the body element.
- Links are brand-colored; hover underlines
- Code tags have subtle gray background and small padding
- Headings
  - h1: 20px; h2: 16px
  - Use `.hero-title` for hero headlines: 2.5em with margin
- Container
  - `.container`: `max-width: var(--container); margin: 0 auto; padding-inline: clamp(12px, 4vw, 24px);` (horizontal gutters only; no vertical padding)

3) Layout and Navigation
- Top navigation
  - `nav.topnav`: Flex layout with border-bottom and tinted background var(--bg-soft); padding 8px 12px (matches footer horizontal spacing)
  - `.nav-left`, `.nav-right`: Flex containers with gaps
- Sticky footer
  - Body is a flex column with `min-height: 100vh`; footer uses `margin-top: auto` so it stays at the bottom when content is short and scrolls with content when long
- Bottom separation best practice
  - Global bottom spacing applied via `main { padding-bottom: calc(var(--gap) * 2); }` to ensure comfortable separation between the last content section and the footer across all pages. Do not add spacing on the footer; prefer page/content padding. Avoid double-spacing by not adding extra `margin-bottom` to the last section unless needed for specific contexts.
- Content layout helpers
  - `.layout`: 2-column grid (2fr 1fr). Stacks on <=768px
  - `.layout-pages`: narrow list + wide pane (2fr 3fr) for results page

4) Utilities
- Typography
  - `.small`: 12px text and muted color
  - `.muted`: color var(--muted)
- Spacing
  - `.m0`, `.mt-1`, `.mt-2`, `.mt-3`, `.mb-1`, `.mb-2`, `.mb-3`, `.ml-1`, `.ml-2`, `.mr-1`, `.mr-2`
- Display
  - `.hidden`, `.inline`, `.inline-block`, `.block`
- Flex spacer
  - `.spacer`: takes remaining space in a flex row (e.g., in `.actions`)
- A11y helpers
  - `.visually-hidden`: screen-reader only text
- Validation text
  - `.validation-success`: success colored text for inline feedback
  - `.validation-error`: error colored text for inline feedback
- Pills
  - `.pill`: rounded subtle label (used in discovery cards)
  - `.pill-active`: active/selected pill state (brand-weak background)

5) Buttons
- Base
  - `button, .btn`: 13px, rounded, bordered, pointer cursor
- Variants
  - `.btn-primary`: brand background, white text
  - `.btn-secondary`: white background, brand border, brand text
  - `.btn-danger-soft`: red-weak background, red-ish text
  - `.btn-sm`: compact button
  - `.btn-icon`: minimal padding icon-style
- Usage
  - For anchors acting as buttons, add `class="btn"` (+ variants)
  - Avoid inline padding/margins on buttons; use utilities

6) Inputs and Forms
- Inputs (text, url, email, number), `select`, `textarea`, `.input`, `.select` share consistent look
- Labels
  - Prefer `.block` on labels for stacked forms
- Inline forms (nav, action bars)
  - Use `class="inline"` or `"inline-block"` on form elements
- Validation feedback (Home)
  - `<div id="domain-feedback" class="small validation-success|validation-error" aria-live="polite">...</div>`

7) Alerts and Notices
- Legacy notices: `.notice`, `.notice-ok`, `.notice-err`
- Standard alerts: `.alert`, `.alert-ok`, `.alert-err`, `.alert-info`
- Use alerts for transient messages and status updates

8) Panels and Cards
- `.panel`: Card-like container with border, radius, padding
- `.subtitle`: Small muted text for subheadings inside panels
- Discovery cards (All)
  - `.card-list`: vertical list layout with gaps
  - `.card`: card item; use `.card-meta` for small metadata rows under titles

9) Tables
- `.table`: Full width, collapsed borders, uniform cell padding and 13px font
- Use for dashboard jobs, emails list; keep bulk actions in `.actions`

10) Badges and Status Chips
- Badge group
  - `.badges` wrapper with `.badge` items
  - Status variants: `.status-pending`, `.status-running`, `.status-succeeded`, `.status-failed`
- Status chips (leads/emails)
  - `.status-chip` with variants: `.status-valid`, `.status-unknown`, `.status-suspicious`, `.status-disposable`, `.status-invalid`

11) Tabs
- `.tabs` wrapper with `.tab` buttons
- Selected tab: `.tab[aria-selected="true"]` uses brand-weak background
- Use `.tab-content` plus `.active` to toggle panels

12) Lists
- `.domain-list` for simple name/value rows
- Pages list (Results)
  - `.pages-list` with list items supporting:
    - `.active` (currently previewed — brand-weak background, left border)
    - `.selected` (included for actions — same highlight pattern)

13) Quick Stats
- `.quick-stats`: grid for stat cards
- `.stat-card` with `.stat-k` and `.stat-v` for label/value

14) Progress Bar
- `.progress` (container) + `.progress-bar` (inner) for running states (crawling)
- Show visited/total, ETA, and budget counters in a `.panel` with `.small` text

15) Chips
- `.chips` container, `.chip` elements for pill-like clickable filters
- Toggle/pressed:
  - `.chip.selected` OR `[aria-pressed="true"]` uses brand-weak background + border accent

16) Mobile Tabs and Sections
- `.mobile-tabs` (hidden on desktop)
- `.mobile-tab` for tab buttons (use `aria-selected`)
- `.mobile-section` sections; toggle visibility with `.hidden` or `.active`
- Use JS to toggle `aria-selected` and visibility

17) Grid Helpers
- `.summary-grid`: 2 columns with gap
- `.summary-item`: 13px font-size body content
- `.col-span-all`: grid-column 1 / -1 for wide sections

18) Responsive
- <= 768px
  - `.layout`, `.layout-pages` collapse to block
  - `.quick-stats` reduces to 3 columns
  - `.mobile-tabs` displayed
- <= 640px (home helpers)
  - Stack domain input and primary button to full width
- General
  - Prefer single-column hero and short lists above the fold

19) Usage Conventions
- Avoid inline style except for honeypots/guarded legacy blocks
- Prefer semantic HTML with ARIA attributes (aria-live for progress, validation; roles for toolbars, tablists, tables)
- JS toggles should add/remove `.hidden` or switch `aria-selected`
- Buttons and anchors used as actions should consistently use `.btn` variants
- Keep tables, badges, tabs, cards, alerts consistent via classes (no inline colors/borders)

20) Component Mapping Examples
- Alert info
  - `<div class="alert alert-info" role="status" aria-live="polite">...</div>`
- Primary action
  - `<button class="btn btn-primary">Analyze</button>`
- Inline form in toolbar
  - `<form method="post" class="inline">...</form>`
- Panel section
  - `<section class="panel">...</section>`
- Badge group
  - `<span class="badges"><span class="badge">Public</span> <span class="badge status-running">Running</span></span>`
- Progress bar
  - `<div class="progress"><div class="progress-bar" style="width: 45%"></div></div>` (prefer JS-driven width)

21) New Components for Mockups
- Hero (Home)
  - Structure:
    - `<section class="panel hero">`
      - `<h1 class="hero-title">Headline</h1>`
      - `<ul class="benefits small">…</ul>`
      - `<div class="social-proof small">…</div>`
  - Guidance: Benefits are concise, scannable; maintain 44px tap targets on mobile
- Validation Feedback (Home input)
  - `<div id="domain-feedback" class="small validation-success|validation-error" aria-live="polite">…</div>`
- Strategic Gate (Results)
  - Preview panel with lock and CTA:
    - `<section class="panel gate-locked">`
      - `<div class="gate-preview">sample content</div>`
      - `<div class="actions cta-row"><a class="btn btn-primary">Unlock…</a></div>`
  - Visual: Subtle lock motif; brand-weak accents
- Discovery Card (All)
  - `<li class="card">`
    - `<div class="card-title">Site/Title</div>`
    - `<div class="card-meta small">pages • emails • updated</div>`
    - `<div class="pill-row"><span class="pill">Strengths</span> <span class="pill">Gaps</span></div>`
    - `<div class="actions"><a class="btn btn-sm">View</a> <a class="btn btn-sm">Quick Summary</a></div>`
- Filters as Chips (All)
  - `<div class="chips" role="toolbar">`
    - `<button class="chip" aria-pressed="false">Blog</button> …`
  - Toggle selected state: `.chip.selected` or `aria-pressed="true"`
- Generation & Chat Blocks (AI Assistance)
  - `.content-generation` (panel with `.config-grid`)
  - `.generation-options` with `.structured-actions` and `.free-chat`
  - `.generation-results` with `.results-header` and `.results-actions`

22) Patterns & Behaviors
- Progress & Polling
  - Results “running” state uses `.progress` panel + counters; poll every 2–3s
  - Always set `aria-live="polite"` on progress region
- Value-First Gating
  - Show counts (“47 emails”) and first 3 preview items
  - CTA block uses `.actions` with `.spacer` and `.btn-primary`
- Bulk Tables & Toolbars
  - Bulk action bars use `.actions`; group buttons; `.btn-sm` acceptable for density
  - No inline row colors; use status chips/badges exclusively
- Discovery Gallery
  - Use `.card-list` of `.card` items; avoid dense tables in public browsing
  - Each card shows title, meta, strengths/gaps pills, primary action
- Pages Selection
  - `.pages-list li` supports `.active` (preview) and `.selected` (include)
  - `.selection-summary` shows counts (bold brand text)

23) Accessibility & Content Guidelines
- Focus visible: ensure keyboard focus outlines (use `--focus-ring`)
- `aria-live` for validation/progress; roles for toolbars/tables/tablists
- Touch targets: minimum 44px on mobile
- Voice input affordance: microphone icon is decorative; keep explicit labels
- Copy: use specific numbers (“47 emails”), concise CTAs (“Sign up free”)
- Empty states: show small helper text in panels

24) Breakpoints & Layout Guidelines
- Mobile-first: design for 320–375px first; avoid horizontal scroll
- Containerized pages (`.container`) for wider pages (All, Results)
- Avoid more than 2 columns of content on tablet

25) Mockups → Class Mapping (quick reference)
- Home (Anonymous): `.hero-title`, benefits list (`.small`), `#site-form` (`.inline`), `.validation-success|error`, `.panel`, `.btn-primary`, `.domain-list` for “Latest”
- Submission/Progress: `.panel` status, `.progress`, `.progress-bar`, small counters, `aria-live`
- Results (Gating): `.panel`, `.quick-stats` (`.stat-card`), `.gate-locked` with CTA, `.tabs/.tab`, `.pages-list`, `.selection-summary`
- All (Public): `.container`, `.chips/.chip.selected`, `.card-list/.card`, `.pill`, `.actions`
- Dashboard (My): `.table` + `.actions`; status `.badge` / `.status-chip`
- AI Assistance: `.content-generation`, `.config-grid`, `.generation-options`, `.generation-results`, `.chat-area`

Change Log Reference
- Central styles extracted to `/static/style.css` and linked in `base.html`
- Inline styles removed across home, result, all, and my pages; standardized classes applied
- Base navigation buttons converted to consistent `.btn` styles
- Utilities added: spacing, display, layout helpers
- New grid helpers for summary metrics on result page
- Added tokens/utilities/patterns for mockups: container, bg-soft, brand-hover, warning colors, focus ring, shadow-2, discovery cards, filter chips selected state, validation feedback text, hero, gate-locked
- A11y: focus visibility, aria-live usage, touch target guidance

Review and QA
- When creating new pages/components:
  - Reuse these classes; do not introduce custom inline styling
  - If a new variant is required, add it in `style.css` and document it here
  - Validate visual at desktop and mobile (768px and 640px)
  - Test keyboard focus and aria-live regions
  - Prefer specific metric copy (counts/times) over vague language
