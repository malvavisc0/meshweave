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
  - `--radius-lg`: 12px
  - `--container`: 1100px (max content width for wide pages)
- Colors
- Foreground/background
  - `--fg`: #0F172A
  - `--muted`: #374151
  - `--bg`: #F8FAFC
  - `--bg-soft`: #FAFAFA
  - `--card-bg`: #FFFFFF
- Borders
    - `--border`: #E5E7EB
    - `--border-strong`: #D1D5DB
  - Brand (Teal)
    - `--brand`: #0D9488
    - `--brand-ink`: #134E4A
    - `--brand-weak`: #CCFBF1
    - `--brand-hover`: #0F766E
  - Accent (Cyan)
    - `--accent`: #06B6D4
    - `--accent-ink`: #0E7490
    - `--accent-weak`: #CFFAFE
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
  - Info (aligned with accent)
    - `--info-weak`: #CFFAFE
    - `--info`: #0E7490
  - Background pattern
      - `--bg-grid-size`: 24px
      - `--bg-grid-color`: rgba(200,200,200,0.15)
- Shadows
  - `--shadow-1`: 0 10px 30px rgba(2,6,23,0.10)
  - `--shadow-subtle`: 0 4px 12px rgba(2,6,23,0.06)
- Focus
  - `--focus-ring`: 0 0 0 3px rgba(6,182,212,0.45)
- Gradients
  - `--brand-gradient`: linear-gradient(135deg, var(--brand), var(--accent))

2) Base Elements
- Body uses the Plus Jakarta Sans font stack (`'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`), line-height 1.45, tinted neutral background var(--bg) with a subtle grid pattern, color var(--fg); edge-to-edge layout (no horizontal body padding). To disable the pattern on a specific page, add class "no-bg-pattern" to the body element.
- Links use accent color (`--accent`); hover underlines. Top navigation links on soft backgrounds use `--brand-ink` for contrast.
- Code tags have subtle gray background and small padding
- Headings
  - h1: 20px; h2: 16px
  - Use `.hero-title` for hero headlines: font-size: clamp(1.75rem, 6vw, 2.5rem); with margin
- Container
  - `.container`: `max-width: var(--container); margin: 0 auto; padding-inline: clamp(12px, 4vw, 24px);` (horizontal gutters only; no vertical padding)

3) Layout and Navigation
- Top navigation
  - `nav.topnav`: Flex layout with border-bottom and tinted background var(--bg-soft); padding 8px 12px (matches footer horizontal spacing)
  - `.nav-left`, `.nav-right`: Flex containers with gaps
  - Wraps on narrow screens (`flex-wrap: wrap`) to prevent overflow of long emails/sign-in text
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
- Toolbars
  - `.actions`: flexible action clusters (display:flex; gap; wrap)
  - `.toolbar`: generic inline toolbar (display:flex; gap; align-items:center; flex-wrap:wrap)
- Grids
  - `.form-grid`: responsive form grid (grid: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--gap))
- Width
  - `.w-full`: force width: 100% (inputs/controls)
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
  - `.btn-primary`: brand background, white text; hover deepens to `--brand-hover`
  - `.btn-primary-gradient`: brand→accent gradient CTA; white text. Use sparingly for primary calls-to-action only.
  - `.btn-secondary`: white background, accent border, accent-ink text; hover background `--accent-weak`
  - `.btn-danger-soft`: red-weak background, red-ish text
  - `.btn-google`: Google Sign-In (white surface, Google gray text, subtle hover; border #dadce0; decorative Google-colored square via `::before`)
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
- `.table-wrap`: wrapper that enables horizontal scroll on small screens (`overflow-x: auto; -webkit-overflow-scrolling: touch`)
- Long content handling: links inside table cells should wrap (`.table td a { overflow-wrap: anywhere; word-break: break-word; }`)
- Usage: Use for dashboard jobs, emails list; keep bulk actions in `.actions`

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
- Google Sign-In (OAuth)
  - `<a class="btn btn-google" href="/login?provider=google&next=/">Sign in with Google</a>`
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
- Softened color palette: updated background, border, and muted colors to reduce brightness and improve accessibility for users sensitive to bright screens (e.g., replaced pure whites with off-whites, adjusted grid pattern opacity)

Review and QA
- When creating new pages/components:
  - Reuse these classes; do not introduce custom inline styling
  - If a new variant is required, add it in `style.css` and document it here
  - Validate visual at desktop and mobile (768px and 640px)
  - Test keyboard focus and aria-live regions
  - Prefer specific metric copy (counts/times) over vague language

## 2025-09 Mobile, Navigation, and A11y Updates

This addendum documents the latest changes and codifies guidance so new work aligns with the current UI. It complements sections 2, 3, 5, 9, 16, 18, 19, 23, and 24 above. The canonical implementation resides in [webapp/static/style.css](webapp/static/style.css) and templates in [webapp/templates/base.html](webapp/templates/base.html).

1) Navigation semantics and active states
- Primary nav must declare semantic roles and labels:
  - In HTML: `<nav class="topnav" role="navigation" aria-label="Primary">`
- Active page indication uses aria-current and is styled:
  - Links: `.topnav a[aria-current="page"]:not(.btn) { color: var(--brand-ink); text-decoration: underline; font-weight: 700; }`
  - Button-like anchors: `.topnav .btn[aria-current="page"] { background: var(--brand-weak); border-color: var(--brand); color: var(--brand-ink); }`
- Do not use visual separators like " | "; rely on flex gaps.
- Small screens (<=480px):
  - Stack `.topnav` into a column and wrap `.nav-left`/`.nav-right`
  - Truncate long signed-in labels with ellipsis
  - Make primary CTA buttons full-width in the nav when reasonable

2) Skip link and main landmark
- Provide a keyboard-visible skip link for direct access to content:
  - `<a class="skip-link" href="#main">Skip to content</a>`
  - `<main id="main" tabindex="-1" role="main">…</main>`
- Style `.skip-link` to be off-screen by default and visible on focus.

3) Mobile tap targets and form ergonomics
- At <=480px:
  - `button, .btn` min-height: 44px (increase padding as needed)
  - `.btn-sm` min-height: 36px
  - `input/select/textarea` min-height: 40px; font-size: 16px to avoid iOS zoom on focus
- Continue to prefer `.w-full` for inputs in mobile stacks and avoid inline widths.

4) Tables on small screens
- Use `.table-wrap` for horizontal scroll when truly necessary. Prefer wrapping content to avoid overflow:
  - `.table td { word-break: break-word; overflow-wrap: anywhere; }`
- At <=480px:
  - Slightly reduce cell padding for density
  - Allow action buttons inside cells to wrap (stack) with small vertical spacing
- Keep bulk actions in `.actions` bars which already wrap and have gaps.

5) Mobile tabs and default visibility
- Use `.mobile-tabs` with `.mobile-tab` buttons and `aria-selected` to indicate the active tab; each `.mobile-tab` must specify `data-target="#section-id"`.
- Sections are `.mobile-section`; the active section must have `.active`. JS ensures:
  - One section is visible by default on load, preferring a tab with `aria-selected="true"`, else the first tab
  - Switching tabs toggles `aria-selected` and shows the appropriate `.mobile-section`
- CSS ensures the active section displays:
  - `.mobile-section.active { display: block !important; }` on mobile breakpoints

6) Chat area and drawers on mobile
- Chat content area height is viewport-responsive to reduce wasted space:
  - `.chat-area { height: clamp(220px, 45vh, 450px); }` on <=768px
- Chat drawers respect the viewport and iOS safe areas on small devices:
  - On <=480px: `.chat-drawer { width: calc(100% - 24px); height: clamp(300px, 60vh, 420px); left: 12px; right: 12px; bottom: 12px; }`
  - Safe area: when supported, `bottom: max(12px, env(safe-area-inset-bottom))`

7) Focus visibility and accessibility
- Global keyboard focus visibility is standardized via `:focus-visible` using the accent color, ensuring actionable elements (links, buttons, pills) are obviously focused.
- Continue to:
  - Use `aria-live="polite"` for progress and validation feedback
  - Provide roles for toolbars/tablists/tables
  - Keep tap targets at least 44px on mobile
  - Prefer clear, specific microcopy (“47 emails found”) over generic text

8) iOS safe-area support
- Body and footer accommodate the home indicator area:
  - `body` adds extra bottom padding using `env(safe-area-inset-bottom)` when supported
  - `.site-footer` adds matching bottom padding on iOS to avoid being occluded

9) Patterns and implementation references
- Implemented in:
  - Navigation semantics, active link styling, and skip link: [webapp/templates/base.html](webapp/templates/base.html)
  - Mobile ergonomics, focus ring, tables wrapping, chat sizing, safe-area: [webapp/static/style.css](webapp/static/style.css)
  - Mobile tabs default activation logic: [webapp/static/js/result-analysis.js](webapp/static/js/result-analysis.js)
- Do not reintroduce page-specific inline styles for navigation, tab visibility, or chat sizing. Extend global classes/utilities if needed.

10) Change log (2025-09)
- Navigation: aria-current styling for active items; skip link and main landmark added
- Mobile: standardized tap targets and form control heights; truncated long signed-in labels; full-width nav CTAs where appropriate
- Tables: cell content wrapping rule added; action button wrapping guidance
- Mobile tabs: default activation on load; enforced visibility of the active section on mobile
- Chat: viewport-responsive heights; drawer constrained to viewport width/height; safe-area support for iOS
- Focus: global `:focus-visible` ring standardized across interactive controls

## 2025-10 Home headline, discovery card, and palette updates

This addendum documents the October 2025 updates to the Home page community metrics presentation, discovery card pattern, and the adoption of the Teal + Cyan palette so future work aligns with the current UI. Canonical implementation in [webapp/static/style.css](webapp/static/style.css:1) and templates in [webapp/templates/base.html](webapp/templates/base.html:35). Palette rationale and mapping are detailed in [docs/ui-modernization-plan.md](docs/ui-modernization-plan.md).

0) Palette and token updates (Teal + Cyan)
- Primary brand (Teal) for actions and emphasis:
  - `--brand` #0D9488, `--brand-hover` #0F766E, `--brand-ink` #134E4A, `--brand-weak` #CCFBF1
- Secondary accent (Cyan) for links and informational highlights:
  - `--accent` #06B6D4, `--accent-ink` #0E7490, `--accent-weak` #CFFAFE
- Neutrals and surfaces:
  - `--fg` #0F172A, `--bg` #F8FAFC, `--card-bg` #FFFFFF, borders updated to `--border` #E5E7EB and `--border-strong` #D1D5DB
- New helpers:
  - `--shadow-subtle` 0 4px 12px rgba(2,6,23,0.06), `--radius-lg` 12px, `--brand-gradient` linear-gradient(135deg, var(--brand), var(--accent))
  - Focus ring standardized: `--focus-ring` 0 0 0 3px rgba(6,182,212,0.45) (Cyan-based)
- Usage policy:
  - Links use accent color; top navigation links on soft backgrounds use brand-ink for contrast
  - Panels/cards are white surfaces on a soft page background; use subtle elevation only on interactive hover
  - Optional gradient variant is reserved for primary CTAs: `.btn-primary-gradient`

1) Community metrics as a marketing headline (Home)
- The previous “quick-stats” grid on Home is replaced by a single, scannable headline that emphasizes key numbers inline.
- Canonical implementation:
  - Template: [webapp/templates/home.html](webapp/templates/home.html:51)
  - Styles: [webapp/static/style.css](webapp/static/style.css:675)
- Copy and grammar:
  - “From X Analyses we surfaced Y Potential Leads across Z Pages — and counting”
  - Use the thousands filter for all numbers.
- Markup example:
  ```
  <section class="panel mt-2 marketing-headline" role="region" aria-label="Community momentum">
    <p class="m0" aria-live="polite">
      From <span class="emph-num">{{ community_metrics.analyses_total | thousands }}</span> <span class="emph-label">Analyses</span> we surfaced
      <span class="emph-num">{{ community_metrics.emails_total | thousands }}</span> <span class="emph-label">Potential Leads</span>
      across <span class="emph-num">{{ community_metrics.pages_total | thousands }}</span> <span class="emph-label">Pages</span> — and counting
    </p>
  </section>
  ```
- Visual guidance:
  - .emph-num: brand color and bold weight for numbers
  - .emph-label: muted, compact label following each number
  - Keep the region as a .panel for consistency with other blocks.
- Accessibility:
  - Use aria-live="polite" within the headline paragraph so values can update unobtrusively.

2) Discovery cards (Recent Analyses) pattern
- The discovery card macro now uses a robust title fallback and shows a cleaner subtitle line.
- Canonical macro: [analysis_card()](webapp/templates/partials/analysis_card.html:1)
- Title fallback logic:
  - display_title = item.title or item.domain or item.canonical_url
  - Use display_title for the clickable link text, link title attribute, and aria-label.
- Subtitle:
  - A muted subtitle row under the title shows the domain (and may include path when needed) via .card-subtitle.
- Meta:
  - Use thousands separators for emails and pages.
  - Updated time uses a time element with datetime and title attributes.
- NEW badge:
  - Show a small “NEW” badge when item.is_new is true to highlight very recent items.
- Snippet (structure-only reference):
  ```
  <li class="card ...">
    <div class="card-title">
      <img ...>
      <a href="/analysis/{{ item.key }}" aria-label="Open analysis for {{ display_title }}" title="{{ display_title }}">{{ display_title }}</a>
      {% if item.is_new %}<span class="badge ml-1" title="Recently updated">NEW</span>{% endif %}
    </div>

    <div class="card-subtitle small">
      <span class="domain">{{ item.domain or item.canonical_url }}</span>
    </div>

    <div class="card-meta small">
      {{ (item.email_count|default(0)) | thousands }} emails •
      {{ (item.page_count|default(0)) | thousands }} pages •
      Updated <time datetime="{{ item.updated_iso or item.updated_at }}" title="{{ item.updated_iso or item.updated_at }}">
        {{ item.updated_relative or item.updated_at }}
      </time>
    </div>
  </li>
  ```

3) Terminology and copy consistency
- Prefer “Potential Leads” (public/marketing copy) over raw “Emails” when summarizing.
- Canonical headline sentence (Home): “From X Analyses we surfaced Y Potential Leads across Z Pages — and counting”.

4) Deprecations and placement
- The .quick-stats grid is no longer used on Home for community totals and is reserved for dashboard/results contexts.
  - See base styles for stat cards: [webapp/static/style.css](webapp/static/style.css:317)

5) A11y and ergonomics checklist
- Headline: keep aria-live="polite" on the paragraph; numbers update unobtrusively.
- Links: aria-label derived from the same computed display title shown visually.
- Maintain 44px tap targets on mobile for action buttons within cards (use .btn and .btn-sm appropriately).

6) Contrast guardrails (AA)
- Primary buttons: white text on `--brand` and `--brand-hover` passes AA for normal text.
- Links: `--accent` on white/soft backgrounds meets AA at 13–16px; use `--accent-ink` in dense contexts or on tinted surfaces.
- Selected chips/rows: text `--brand-ink` on `--brand-weak` and `--accent-ink` on `--accent-weak` for comfortable readability.
