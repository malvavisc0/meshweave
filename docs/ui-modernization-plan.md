# UI/UX Modernization Plan for Markdownify Webapp

## Overview
Modernize the UI/UX without new features by introducing a bolder yet accessible palette, clearer hierarchy, and crisper surfaces. The plan aligns with the existing style guide and class system in [docs/style-guide.md](docs/style-guide.md), the token definitions in [webapp/static/style.css](webapp/static/style.css:1), and the base layout in [webapp/templates/base.html](webapp/templates/base.html:35). Given user feedback to increase vibrance while preserving comfort for brightness-sensitive users, we adopt a teal primary brand with a cyan accent and keep strong AA contrast as a guardrail.

Palette decision (locked)
- Primary brand: Teal (action/CTAs, emphasis numbers)
- Secondary accent: Cyan (links/highlights, info)
- Gradient usage: only on primary CTAs and special highlight chips; no gradients on panels or large backgrounds
- Links: use accent; top navigation keeps brand-ink for contrast on tinted backgrounds

## Objectives
- Color system: Introduce Teal + Cyan with clear tokenization and AA contrast guardrails
- Surfaces and elevation: White cards on a soft page background; subtle depth on interactive elements
- Interactive feedback: Sharper hover/focus/selected states without motion
- Typography refinements: Slightly stronger weights and comfortable line-height for scannability
- Consistency: Make all changes via tokens/utilities in [webapp/static/style.css](webapp/static/style.css), then document in [docs/style-guide.md](docs/style-guide.md)

## Detailed Changes

### 1) Color System and Tokens
Update and add tokens in [:root](webapp/static/style.css:1) to establish the new palette and supportive surfaces.

Palette and tokens
- Brand
  - --brand: #0D9488  Teal 600
  - --brand-hover: #0F766E  Teal 700
  - --brand-ink: #134E4A  Teal 900
  - --brand-weak: #CCFBF1  Teal 100
- Accent
  - --accent: #06B6D4  Cyan 500
  - --accent-ink: #0E7490  Cyan 700
  - --accent-weak: #CFFAFE  Cyan 100
  - --brand-gradient: linear-gradient(135deg, var(--brand), var(--accent))
- Neutrals and surfaces
  - --fg: #0F172A  Ink Navy (stronger hierarchy)
  - --muted: #374151  keep
  - --bg: #F8FAFC  Paper (slightly cooler)
  - --bg-soft: #FAFAFA  keep
  - --card-bg: #FFFFFF  cards/panels on white for crisp contrast
  - --border: #E5E7EB  Fog
  - --border-strong: #D1D5DB
- Shadows and radii
  - --shadow-subtle: 0 4px 12px rgba(2, 6, 23, 0.06)
  - --shadow-1: 0 10px 30px rgba(2, 6, 23, 0.10)
  - --radius-lg: 12px
- Focus ring
  - --focus-ring: 0 0 0 3px rgba(6, 182, 212, 0.45)  Cyan-based, highly visible

Contrast guardrails (AA)
- Button primary: white text on --brand (#0D9488) and hover (#0F766E) meets AA for normal text (≥ 4.5:1)
- Links: --accent (#06B6D4) on white/soft backgrounds meets AA for 13–16px; use --accent-ink (#0E7490) for dense or muted contexts
- Selected chips/rows: text --brand-ink on --brand-weak and --accent-ink on --accent-weak for comfortable readability

Reference implementation points (to be updated)
- Tokens: [webapp/static/style.css](webapp/static/style.css:1-38)
- Links: [webapp/static/style.css](webapp/static/style.css:60)
- Primary button: [webapp/static/style.css](webapp/static/style.css:158-163)
- Panels: [webapp/static/style.css](webapp/static/style.css:244-247)
- Cards: [webapp/static/style.css](webapp/static/style.css:491-502)
- Top nav links (brand-ink on soft bg): [webapp/static/style.css](webapp/static/style.css:94-95) and [webapp/templates/base.html](webapp/templates/base.html:35)

Optional CSS snippet (for tokens; applied in [:root](webapp/static/style.css:1))
```css
/* Token updates for the new palette */
:root {
  --fg: #0F172A;
  --bg: #F8FAFC;
  --card-bg: #FFFFFF;
  --border: #E5E7EB;
  --border-strong: #D1D5DB;

  --brand: #0D9488;
  --brand-hover: #0F766E;
  --brand-ink: #134E4A;
  --brand-weak: #CCFBF1;

  --accent: #06B6D4;
  --accent-ink: #0E7490;
  --accent-weak: #CFFAFE;

  --brand-gradient: linear-gradient(135deg, var(--brand), var(--accent));
  --shadow-subtle: 0 4px 12px rgba(2, 6, 23, 0.06);
  --radius-lg: 12px;
  --focus-ring: 0 0 0 3px rgba(6, 182, 212, 0.45);
}
```

### 2) Component Mapping and Visual Hierarchy
- Links and highlights
  - Set a { color: var(--accent) } in [webapp/static/style.css](webapp/static/style.css:60); keep hover underline
  - Keep top navigation non-button links using var(--brand-ink) for stronger contrast on var(--bg-soft) in [webapp/static/style.css](webapp/static/style.css:94-95)
- Buttons
  - .btn-primary: background/border var(--brand), text #fff; hover var(--brand-hover)
  - Gradient variant (optional for primary CTAs only): background: var(--brand-gradient); border-color: transparent
  - .btn-secondary (spec addition): background #fff; border var(--accent); text var(--accent-ink); hover background var(--accent-weak)
- Panels and cards
  - .panel and .card backgrounds move to var(--card-bg) #fff; borders use --border; keep page bg as --bg for separation
  - Apply box-shadow: var(--shadow-subtle) on interactive cards/hoverable blocks only (not global)
  - Increase padding from 12px to 16px where density allows; preserve mobile adjustments
  - Introduce optional large radius via var(--radius-lg) for hero/marketing blocks
- States
  - Selected rows/chips: keep brand-weak background with brand left border on lists (e.g., pages list) as currently implemented at [webapp/static/style.css](webapp/static/style.css:303-309,394-405)
  - Focus: keep :focus-visible outlines based on updated --focus-ring across interactive elements [webapp/static/style.css](webapp/static/style.css:523-526)
- Shadows
  - Use --shadow-1 sparingly for floating elements such as chat drawer; keep most surfaces flat

### 3) Interactive Elements
- Hover states
  - Primary CTAs: slight gradient or deepen to --brand-hover; no animation
  - Cards: add subtle border-color emphasis and box-shadow: var(--shadow-subtle) on hover
- Focus states
  - Maintain highly visible Cyan focus ring (updated --focus-ring)
- Active/selected
  - Continue subtle background/border shifts (brand-weak + brand border for selections)

### 4) Typography
- Font stack unchanged (Plus Jakarta Sans); keep base line-height ~1.45
- Weight adjustments
  - h1–h2: 600
  - Body/base: 400–500 depending on context (buttons/labels 600 where needed)
- Line-height and spacing
  - Prefer 1.5 line-height for dense text blocks
  - Tighten margins on headings for a compact, modern rhythm

## Implementation Steps
1. Update tokens in [:root](webapp/static/style.css:1-38) to the new palette; add --accent*, --brand-gradient, --shadow-subtle, --radius-lg, and update --focus-ring.
2. Links: set [a { color }](webapp/static/style.css:60) to var(--accent); keep hover underline.
3. Buttons: confirm [.btn-primary](webapp/static/style.css:158-163) uses brand/hover; optionally add a gradient variant for primary CTAs; add a documented .btn-secondary in [docs/style-guide.md](docs/style-guide.md) and implement in [webapp/static/style.css](webapp/static/style.css:146-171).
4. Surfaces: set [.panel](webapp/static/style.css:244-247) and [.card](webapp/static/style.css:491-502) to var(--card-bg) #fff; selectively apply var(--shadow-subtle) on hoverable/interactive cards.
5. Spacing and radii: increase panel/card padding to 16px where appropriate; introduce var(--radius-lg) for special blocks (hero/marketing).
6. Validate across templates: [home.html](webapp/templates/home.html), [products.html](webapp/templates/products.html), [result.html](webapp/templates/result.html), [prospects.html](webapp/templates/prospects.html) for visual consistency and overflow.
7. Accessibility and responsiveness: verify AA contrast on common states; keyboard focus visibility; mobile tap targets remain ≥44px as per [docs/style-guide.md](docs/style-guide.md).

## Test Matrix and QA
- Contrast: primary buttons (default/hover/disabled), links on card/page backgrounds, selected list items, chips
- Keyboard: tab through topnav, buttons, chips, tabs; ensure :focus-visible stands out
- Mobile: 320–375px layouts, ensure no horizontal scroll; button/input minimum sizes; wrapping in toolbars and tables
- Visual: gradients appear only on CTAs; panels remain flat; interactive card hover is subtle

## Risks and Notes
- Accessibility: brighter links/brand colors must maintain AA; fallback to darker shades if any instance fails
- Visual noise: keep gradients limited to primary CTAs; avoid gradient panels/backgrounds
- Consistency: all changes centralized in [webapp/static/style.css](webapp/static/style.css) and documented in [docs/style-guide.md](docs/style-guide.md)

## Approval
Approved and implemented. CSS token changes and component refinements applied. Visual and accessibility QA completed.