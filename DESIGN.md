---
name: Castor
description: A Kanban board that runs the work, in a soft Material 3 world anyone can recolour.
colors:
  primary: "#545a92"
  on-primary: "#ffffff"
  primary-container: "#e0e0ff"
  on-primary-container: "#3d4279"
  secondary: "#5c5d72"
  secondary-container: "#e1e0f9"
  on-secondary-container: "#444559"
  tertiary: "#78536b"
  tertiary-container: "#ffd7ef"
  on-tertiary-container: "#5e3c53"
  error: "#ba1a1a"
  error-container: "#ffdad6"
  on-error-container: "#93000a"
  surface: "#fbf8ff"
  surface-container-lowest: "#ffffff"
  surface-container-low: "#f5f2fa"
  surface-container: "#efedf4"
  surface-container-high: "#eae7ef"
  surface-container-highest: "#e4e1e9"
  on-surface: "#1b1b21"
  on-surface-variant: "#46464f"
  outline: "#777680"
  outline-variant: "#c7c5d0"
  custom-blue: "#005ac4"
  custom-amber: "#845400"
  custom-violet: "#7f34ca"
  custom-green: "#006e26"
typography:
  headline-small:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 400
    lineHeight: "2rem"
  title-large:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 500
    lineHeight: "1.75rem"
  title-medium:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: "1.5rem"
    letterSpacing: "0.009rem"
  title-small:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: "1.25rem"
  body-medium:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
    letterSpacing: "0.016rem"
  body-small:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: "1rem"
    letterSpacing: "0.025rem"
  label-large:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: "1.25rem"
  label-small:
    fontFamily: "Roboto Flex, system-ui, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: "1rem"
    letterSpacing: "0.03rem"
  mono:
    fontFamily: "Roboto Mono, ui-monospace, monospace"
    fontSize: "12.5px"
    fontWeight: 400
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "28px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  gutter: "24px"
components:
  button-filled:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-large}"
    rounded: "{rounded.full}"
    padding: "0 24px"
    height: "40px"
  button-tonal:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    typography: "{typography.label-large}"
    rounded: "{rounded.full}"
    padding: "0 24px"
    height: "40px"
  button-text:
    textColor: "{colors.primary}"
    typography: "{typography.label-large}"
    rounded: "{rounded.full}"
    padding: "0 12px"
    height: "40px"
  chip-filter-selected:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    typography: "{typography.label-large}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  chip-unselected:
    textColor: "{colors.on-surface-variant}"
    typography: "{typography.label-large}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  card-outlined:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.md}"
    padding: "16px"
  task-card:
    backgroundColor: "{colors.surface-container-lowest}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.md}"
    padding: "12px"
  board-column:
    backgroundColor: "{colors.surface-container-low}"
    rounded: "{rounded.lg}"
    width: "320px"
  text-field-outlined:
    textColor: "{colors.on-surface}"
    typography: "{typography.body-medium}"
    rounded: "{rounded.xs}"
    padding: "0 16px"
  nav-rail-indicator-active:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    rounded: "{rounded.full}"
    height: "32px"
    width: "56px"
  dialog:
    backgroundColor: "{colors.surface-container-high}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.xl}"
    padding: "24px"
---

# Design System: Castor

## Overview

**Creative North Star: "The Field Notebook"**

Castor is a working record. Every card is a page of what ran, what it printed, what it
found and who decided what, and the interface reads like a well-kept notebook: soft tonal
paper, quiet ink, and colour reserved for the things that need a second look. It is Material 3
(Material You) built in-house (`web/src/lib/m3`), so the warmth comes from rounded shapes,
tonal surfaces and each person's own seed colour rather than from decoration.

The density is that of a tool people live in for hours: boards of 320px columns, compact
cards, small type for metadata, monospace where a value is literal (commands, tokens, logs).
Dark mode is first-class and light mode is complete; both are generated from one seed, and
any person can pick their own seed (`users.theme_seed`), which regenerates every colour
role. The design therefore speaks in roles, never in fixed colours.

The confirmed anti-reference is the dashboard-template look: no neon, no glowing gradients,
no glassmorphism.

**Key Characteristics:**
- Tonal layering over shadows: surfaces step through the surface-container ramp.
- Pill buttons, softly rounded cards, generous 28px dialog corners.
- Colour is for state and attention (failed, running, waiting, selected), on tonal containers.
- Roboto Flex everywhere, Roboto Mono for literal values, Material Symbols Rounded icons.
- Every surface follows the person's seed colour in light and dark.

## Colors

A muted indigo seed (#4f5bd5) generates a soft, low-chroma tonal palette; the values above are
the light theme, and the dark theme (surface #131318, primary #bdc2ff) comes from the same seed.

### Primary
- **Muted Indigo** (primary): the one accent for action: filled buttons, links, the command
  icon on a card, focus on a field. Dark: periwinkle (#bdc2ff).
- **Indigo Mist** (primary-container): succeeded job chips, selection highlight, avatars.

### Secondary
- **Slate Lavender** (secondary-container): the "you are here" and "selected" colour: the
  active navigation pill, selected filter chips, tonal buttons, @mention chips. The focus
  ring uses secondary too.

### Tertiary
- **Dusky Mauve** (tertiary / tertiary-container): the "look at this" accent: a running card's
  ring, warnings that aren't errors (copy-this-token-now banners, notices).

### Error
- **Signal Red** (error / error-container): failed jobs, over-limit column counts, unread
  badges, destructive confirm buttons.

### Extended
- **Blue, Amber, Violet, Green** (custom colours, harmonised to the seed by
  `gen-tokens.mjs`): categorical colour where roles run out (activity kinds, charts, asset
  types). Each has `on-`, `-container` and `on-…-container` partners.
- **Sticky-note colours** (`--note-*` in `app.css`): pastels in light, muted shades in dark,
  for pinned notes only.

### Neutral
- **Lavender Paper** (surface): the page, the navigation rail, the top bar.
- **The surface-container ramp** (lowest → highest): cards on boards sit on *lowest*, board
  columns on *low*, dialogs on *high*, code blocks and filled cards on *highest*.
- **Ink** (on-surface) for primary text; **Soft Ink** (on-surface-variant) for metadata,
  supporting text and inactive icons.
- **Outline / outline-variant**: field strokes and hairline card borders respectively.

### Named Rules
**The Roles Only Rule.** Use the role utilities (`bg-primary`, `text-on-surface-variant`,
`bg-tertiary-container`…), never a raw Tailwind palette colour or `dark:` variant: every
person can recolour the whole UI, and a fixed colour breaks their theme.

**The Container Rule.** State colour goes on a tonal container with its `on-` partner
(error-container + on-error-container), not as saturated fills, so status reads at a glance
without shouting.

## Typography

**Body Font:** Roboto Flex (with system-ui, sans-serif)
**Label/Mono Font:** Roboto Mono (with ui-monospace, monospace)

**Character:** One variable sans for everything gives the notebook its even, legible hand;
the mono face appears only where text is a literal you might copy.

### Hierarchy
- **Headline small** (400, 1.5rem, 2rem): dialog titles.
- **Title large** (500, 1.375rem, 1.75rem): page titles in the top app bar, Settings heading.
- **Title medium / small** (500, 1rem and 0.875rem): column names, card and list-row titles.
- **Body medium** (400, 0.875rem, 1.25rem): the default reading size: dialog text, comments,
  descriptions. Markdown (`.prose-m3`) uses line-height 1.6.
- **Body small** (400, 0.75rem, 1rem): metadata lines, supporting text under fields, times.
- **Label large / medium / small** (500): buttons and chips (large), navigation labels
  (medium), counts and status badges (small, 0.6875rem).
- **Mono** (Roboto Mono, 12.5px in code blocks, 0.85em inline): commands, tokens, prefixes, logs.

The display sizes exist in `app.css` but the app doesn't use them: the largest text is a
page title.

### Named Rules
**The Literal Rule.** Anything a person might paste into a terminal (a command, a token, a
path, a log line) is set in mono on a surface-container-highest well; nothing else is.

## Layout

A fixed 80px navigation rail on the left, a 64px top app bar per page, and a scrolling
content area with a 24px side gutter. Boards scroll horizontally through 320px columns with
12px gaps; list pages (Settings, tokens, alerts) cap their content at 896px (`max-w-4xl`) and
stack outlined cards 12px apart. Settings adds a 240px section nav from 768px up.

Spacing steps by 4px: 4 and 8 inside controls, 12 between cards and rows, 16 inside cards,
24 for page gutters and dialog padding. Dialogs come in four sizes (sm, md, lg, full) and
never exceed the viewport minus 2rem; long content scrolls inside them.

## Elevation & Depth

Depth is mostly tonal: a card is lighter or darker than what it sits on. Shadows are the
Material elevation scale (e1–e5) and appear in three places: cards on a board (e1, e2 on
hover), dialogs (e3) and the floating action button (e3, e4 on hover). Outlined cards on
list pages have no shadow at all.

### Shadow Vocabulary
- **e1** (`0 1px 2px rgb(0 0 0 / 0.3), 0 1px 3px 1px rgb(0 0 0 / 0.15)`): board cards at rest, elevated chips.
- **e2** (`0 1px 2px rgb(0 0 0 / 0.3), 0 2px 6px 2px rgb(0 0 0 / 0.15)`): hover on interactive cards and buttons.
- **e3** (`0 1px 3px rgb(0 0 0 / 0.3), 0 4px 8px 3px rgb(0 0 0 / 0.15)`): dialogs, FAB.

### Named Rules
**The Lift-On-Touch Rule.** A shadow grows only in answer to the pointer (hover e1 → e2);
nothing animates its elevation on its own.

## Shapes

Soft and consistent: pills for anything you press (buttons, icon buttons, the navigation
indicator, counts, avatars), 8px for chips, 12px for cards, 16px for board columns and the
FAB, 28px for dialogs, and a 4px corner on outlined text fields. Borders are hairlines in
outline-variant; the focus ring is a 3px secondary outline offset by 2px.

## Components

Every interactive element uses the shared **state layer**: a tint of its own text colour at
8% on hover and 10% on focus and press, so hover never changes the element's real colour.

### Buttons
- **Shape:** full pill (9999px), 40px tall, label-large text, optional 18px leading icon.
- **Filled:** primary on on-primary; the one main action per view.
- **Tonal:** secondary-container; secondary actions and empty-state calls to action.
- **Outlined / Text:** outline border or bare primary text (12px padding) for low-emphasis
  actions such as Cancel, Copy, Revoke.
- **Danger:** error on on-error, only in destructive confirmations.
- **Hover / Focus:** state layer plus e1 shadow on filled and tonal; focus ring on keyboard focus.

### Chips
- **Style:** 32px tall, 8px corners. Unselected: hairline outline-variant border,
  on-surface-variant text. Selected: secondary-container fill with a check icon.
- **State:** filter chips carry `aria-pressed`; input chips add a remove button.

### Cards / Containers
- **Corner Style:** 12px.
- **Background:** filled (surface-container-highest), outlined (surface + outline-variant
  hairline) for list rows, elevated (surface-container-low + e1).
- **Board cards:** surface-container-lowest, 12px padding, e1 at rest; a running card gets a
  1px tertiary ring.
- **Internal Padding:** 12px on board cards, 16px on list cards.

### Inputs / Fields
- **Style:** Material outlined text field: 4px corners, outline stroke, a floating label that
  notches the border, supporting or error text below in body-small.
- **Focus:** the stroke thickens to the primary colour; the label follows.
- **Error:** error-colour stroke and message.

### Navigation
- **Rail:** 80px wide on surface. Each destination is an icon over a label-medium caption;
  the active one sits in a 56×32px secondary-container pill with a filled icon. Unread
  counts are small error badges on the icon.
- **Top app bar:** 64px, title-large title with a body-small subtitle and actions at the right.

### Job console (signature)
xterm.js on a dark well inside the card: stdout as-is, stderr in red, system lines dimmed,
interactive jobs rendered raw with full 256/truecolor support. A "waiting for input" banner
and Take control sit above it.

### Status chips (signature)
Rounded chips with an icon per job status: queued and cancelled on
surface-container-highest, dispatched on secondary-container, running on tertiary-container
(spinning icon), succeeded on primary-container, failed and lost on error-container.

## Do's and Don'ts

### Do:
- **Do** use role utilities for every colour, so a person's seed recolours it correctly in
  light and dark.
- **Do** put state on tonal containers with their `on-` partner (failed → error-container).
- **Do** keep one filled button per view; make the rest tonal, outlined or text.
- **Do** set commands, tokens, paths and logs in Roboto Mono on surface-container-highest.
- **Do** use the shared `state-layer` and `focus-ring` utilities on anything clickable.
- **Do** animate appearing and disappearing rows with `lib/motion.ts` (240ms enter, 170ms
  leave, fades only under reduced motion).

### Don't:
- **Don't** use neon colours, glowing gradients or glassmorphism.
- **Don't** use raw Tailwind palette colours or the `dark:` variant.
- **Don't** add shadows to list cards; depth there is tonal and the hairline border.
- **Don't** hand-edit `tokens.css`; regenerate it with `scripts/gen-tokens.mjs`.
