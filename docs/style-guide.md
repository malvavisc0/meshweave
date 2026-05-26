# MeshWeave Web UI Style Guide

> Authoritative reference for the MeshWeave design system. All UI work must conform to these rules.
> Source of truth: [`webapp/static/style.css`](../webapp/static/style.css)

---

## Design Philosophy

MeshWeave presents as **sharp, disciplined, editorial but modern**: monochrome-first surfaces, one restrained green accent (`#00A36C`), subtle soft edges (3px radius), clear typographic hierarchy using a single typeface family (IBM Plex Sans), and quiet utility over decorative flourish.

Every page must feel like part of the same operational product system — not a collection of individually styled sections. Visual decisions must read as intentional and authored by a designer, never synthetic, glossy, or over-ornamented. The product scores websites on machine-readability. The UI must itself embody machine-readable precision.

**The single binding rule:** if a visual element could appear on a generic SaaS dashboard template or in a ChatGPT-generated UI mock, it must be removed or replaced.

---

## Color System

### Primary Accent
| Token | Value | Usage |
|---|---|---|
| `--color-primary` | `#00A36C` | Buttons, active states, focus rings, primary CTAs |
| `--color-primary-hover` | `#008F5D` | Button hover state |
| `--color-primary-pressed` | `#007A4E` | Button active/pressed state |
| `--color-on-primary` | `#FFFFFF` | Text on primary-colored backgrounds |

### Text / Ink
| Token | Value | Usage |
|---|---|---|
| `--color-ink` | `#0d0e0f` | Primary body text, headings |
| `--color-ink-muted` | `#525252` | Secondary text, labels, metadata |
| `--color-ink-subtle` | `#8C8C8C` | Tertiary text, placeholders, disabled states |

### Surfaces (Light)
| Token | Value | Usage |
|---|---|---|
| `--color-canvas` | `#FAFAF8` | Page background, card backgrounds (warm off-white, reduces blue light) |
| `--color-surface-1` | `#F4F4F4` | Elevated surfaces, input backgrounds, alternating rows |
| `--color-surface-2` | `#E0E0E0` | Progress bar tracks, disabled inputs |

### Borders
| Token | Value | Usage |
|---|---|---|
| `--color-hairline` | `#E0E0E0` | Default borders, card outlines, dividers |
| `--color-hairline-strong` | `#0d0e0f` | Emphasis borders, active states |

### Inverse (Dark Zones — navbar, footer, hero)
| Token | Value | Usage |
|---|---|---|
| `--color-inverse-canvas` | `#0d0e0f` | Navbar background, footer background, hero background |
| `--color-inverse-surface-1` | `#262626` | Elevated surfaces within dark zones |
| `--color-inverse-ink` | `#FFFFFF` | Primary text in dark zones |
| `--color-inverse-ink-muted` | `#C6C6C6` | Secondary text in dark zones |

### Dark Zone Specific
| Token | Value | Usage |
|---|---|---|
| `--color-dark-bg` | `#0d0e0f` | Alias for inverse-canvas |
| `--color-dark-surface` | `#262626` | Alias for inverse-surface-1 |
| `--color-dark-text` | `#FFFFFF` | Alias for inverse-ink |
| `--color-dark-text-muted` | `#9DA5AD` | Muted text in dark zones (navbar links, hero badges) |
| `--color-dark-border` | `#1E1E1E` | Borders in dark zones (neutral, no blue undertone) |

### Semantic (Status Only)
| Token | Value | Usage |
|---|---|---|
| `--color-semantic-success` | `#24A148` | Pass states, positive trends, good scores |
| `--color-semantic-warning` | `#F1C21B` | Warning states, moderate scores |
| `--color-semantic-error` | `#DA1E28` | Fail states, negative trends, error messages |
| `--color-semantic-info` | `#00A36C` | Same as primary — informational accents |

### Anti-Patterns
- **Never** use Material Design palette colors (`#4caf50`, `#ff9800`, `#f44336`) — use semantic tokens instead
- **Never** use hardcoded `#FFFFFF` or `#fff` for backgrounds — use `--color-inverse-ink` (text) or `--color-canvas` (surfaces). Pure white is reserved for text-on-dark contexts only.
- **Never** use hardcoded `#131313` — use `--color-ink` or `--color-inverse-canvas`
- **Never** introduce new hues outside the defined palette

---

## Typography

### Typeface
- **Primary:** IBM Plex Sans (weights: 300, 400, 600)
- **Monospace:** IBM Plex Sans (same family, monospace variant) — used for data values, scores, labels
- **Source:** Self-hosted WOFF2 files (no external CDN)

### Type Scale
| Token | Weight | Size | Line-Height | Usage |
|---|---|---|---|---|
| `--font-display-xl` | 600 | 60px | 1.17 | Landing hero title only |
| `--font-display-lg` | 600 | 42px | 1.20 | Page headers (h1), section titles |
| `--font-headline` | 600 | 32px | 1.25 | h2, Dashboard title |
| `--font-card-title` | 600 | 22px | 1.33 | h3, card titles |
| `--font-subhead` | 500 | 22px | 1.40 | Hero subtitle, section subtitles |
| `--font-body-lg` | 400 | 18px | 1.50 | Supporting copy, intro paragraphs |
| `--font-body` | 400 | 14px | 1.50 | Body text, default |
| `--font-body-sm` | 400 | 14px | 1.29 | Captions, meta, small text |
| `--font-body-emphasis` | 600 | 14px | 1.29 | Emphasized body, score values |
| `--font-caption` | 400 | 12px | 1.33 | Data labels, stat card keys |
| `--font-label` | 400 | 12px | 1.33 | Uppercase kickers, form labels |
| `--font-button` | 400 | 14px | 1.29 | Button text |

### Rules
- All headings use **600 weight**
- Body text uses **400 weight**
- Data values (scores, stats) use **monospace family** with **600 weight**
- Labels/kickers use **uppercase** with **0.05em letter-spacing**
- Never mix typeface families within a single component

---

## Spacing

All spacing uses a **4px grid**. Use tokens, never hardcoded values.

| Token | Value | Usage |
|---|---|---|
| `--space-xxs` | 4px | Tight gaps (icon + text, badge padding) |
| `--space-xs` | 8px | Small gaps (form fields, card internal) |
| `--space-sm` | 12px | Standard gaps (card padding, section internal) |
| `--space-md` | 16px | Medium gaps (between cards, panel padding) |
| `--space-lg` | 24px | Large gaps (section separation) |
| `--space-xl` | 32px | XL gaps (hero bottom padding) |
| `--space-xxl` | 48px | XXL gaps (footer padding, empty state) |
| `--space-section` | 96px | Full section separation |

---

## Borders & Radius

### Border Radius
| Token | Value | Usage |
|---|---|---|
| `--radius-none` | 3px | Structural containers (cards, panels, inputs) |
| `--radius-xs` | 3px | Small elements (bar tracks, progress fills) |
| `--radius-sm` | 3px | Badges, chips, avatar initials |

**All elements use 3px radius.** No exceptions. This produces a subtle softening that eliminates the harsh "cut" feeling of 0px while maintaining the disciplined aesthetic.

### Border Style
- All borders are **1px solid** using hairline tokens
- No double borders, no outset/inset, no box shadows
- Focus rings use **2px solid var(--color-primary)** with `outline` (not border)

---

## Elevation

MeshWeave uses **no shadows**. Depth is communicated through:
1. **Surface color changes** (canvas → surface-1 → surface-2)
2. **Hairline borders** (1px solid)
3. **Focus rings** (2px solid primary)

---

## Components

### Buttons
- Primary: `--color-primary` bg, `--color-on-primary` text, 12px 16px padding
- Secondary/Navy: transparent bg, hairline border, `--color-dark-text-muted` text
- Danger: transparent bg, `--color-semantic-error` border and text
- All buttons use `--radius-none` (3px)
- Hover: background color shift, no scale/transform

### Cards
- Background: `--color-canvas` or `--color-surface-1`
- Border: 1px solid `--color-hairline`
- Padding: 24px (`--component-card-padding`)
- Radius: `--radius-none` (3px)
- No shadows, no hover lift

### Inputs
- Background: `--color-surface-1`
- Border: 1px solid `--color-hairline`
- Focus: 2px solid `--color-primary`
- Padding: 11px 16px
- Radius: `--radius-none` (3px)

### Badges / Chips
- Background: `--color-surface-1` (light) or `--color-dark-surface` (dark zones)
- Border: 1px solid `--color-hairline` or `--color-dark-border`
- Padding: 0 12px, min-height 32px
- Radius: `--radius-sm` (3px)
- Font: `--font-body-sm`, letter-spacing 0.04em

### Score Bars
- Track: `--color-surface-2`, height 8px, radius `--radius-xs` (3px)
- Fill: `--color-primary`, radius inherits from track
- No per-metric color variation — all scores use the same green fill

### Navbar (Topnav)
- Background: `--color-dark-bg` (#0d0e0f)
- Height: 56px
- Border-bottom: 1px solid `--color-dark-border` (#1E1E1E)
- Links: `--color-dark-text-muted` (#9DA5AD), 600 weight when active
- Active indicator: 2px bottom border in `--color-primary`
- Account display: 32px avatar badge + truncated email (`username@…`)

### Hero
- Background: `--color-dark-bg` (#0d0e0f) with floating constellation particles
- Particles: 4 green dots (`--color-primary`) with unique drift animations and subtle pulse/glow
- Connection lines: faint diagonal gradients at 1.5-2% opacity, 200px spacing
- No gradients on content areas, no pseudoelement glows on content
- Title: `--font-display-xl` (60px), `--color-dark-text`
- Supporting text: `--font-subhead` (22px), `--color-dark-text`
- Audience badges: background `#1A1A1A`, border `--color-dark-border`, text `--color-dark-text-muted`

---

## Layout Patterns

### Page Structure
```
topnav (56px, dark)
  └── main
        ├── landing-hero (dark, with floating constellation)
        ├── landing-section (light surface bands)
        ├── cta-banner (dark)
        └── site-footer (dark)
```

### Section Separation
- Use **surface color alternation** (canvas → surface-1 → canvas) for section bands
- Never use decorative dividers, shadows, or gradients for separation
- Section titles use `--font-display-lg` (42px) on landing pages, `--font-headline` (32px) on utility pages

### Grid
- Max content width: 1280px
- Container padding: `clamp(16px, 4vw, 32px)`
- Card grids: `repeat(auto-fit, minmax(300px, 1fr))` with `--space-md` gap

---

## Anti-Patterns (Never Do These)

1. **No gradients** on component backgrounds (hero constellation connection lines are the only exception)
2. **No animations** except standard 0.15s transitions on color/border/background, and hero constellation particle drift (20-28s, subtle)
3. **No box-shadow or text-shadow** anywhere
4. **No pill shapes** (999px radius) — all elements use 3px
5. **No AI-narrator voice** in copy — use data-forward, precision language
6. **No external CDN dependencies** — all assets self-hosted
7. **No inline `style=""` attributes** — use CSS utility classes
8. **No duplicate class definitions** — use BEM modifiers (`.class--variant`)
9. **No hardcoded color values** — always use design tokens
10. **No fear-based marketing copy** — use outcome-measured, performance-focused language

---

## Copy Guidelines

### Tone
- **Authoritative, not conversational** — the product is an engineering tool
- **Precision over persuasion** — lead with data, not emotion
- **Active voice** — "Measure how AI systems read your site" not "Your site can be measured"

### Prohibited Phrases
- "Your competitors are showing up…" (fear-based)
- "Here's what AI agents see…" (AI-narrator voice)
- "What AI gets right/wrong" (casual, AI-as-character)
- "Every day you're invisible…" (urgency-fear framing)

### Approved Alternatives
- "Measure how AI systems read, understand, and cite your website."
- "Automated site review findings"
- "Confirmed signals" / "Missing or ambiguous signals"
- "Track AI visibility over time. Catch regressions before your competitors do."

---

## File Structure

```
webapp/
  static/
    style.css          # Single stylesheet, all tokens + components
    js/                # Self-hosted JS libraries + app scripts
  templates/
    base.html          # Base layout, topnav, footer
    home.html          # Landing page
    all.html           # Browse page
    dashboard.html     # Dashboard page
    result.html        # Analysis result page
    partials/          # Reusable card components
```

---

## Maintenance Rules

1. **One source of truth:** `style.css` contains all tokens and component styles
2. **No dead code:** every selector must be traceable to a template usage
3. **No duplicate definitions:** merge with BEM modifiers or delete
4. **Token-first:** never hardcode values that have token equivalents
5. **Test in all zones:** verify light and dark zone rendering for every new component
