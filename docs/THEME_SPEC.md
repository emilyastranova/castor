# Theme spec

A theme restyles Castor's web UI. It is a YAML file: colours for light and dark, fonts,
corners, elevation, icons, and any extra CSS. The server checks it and compiles it to CSS
scoped to `html:root[data-skin='<id>']`; the browser injects that CSS when someone picks the
theme (Account → Theme), and remembers it so the next page, and the sign-in page, paint in it
straight away.

- **Built-in themes** are files in `themes/` (`CASTOR_THEMES_DIR`), loaded at start-up and
  read-only in the UI. `themes/astralabs.yaml` is the reference example.
- **Custom themes** are added by admins in Settings → Themes (or `POST /api/themes`, or the
  MCP tools `create_theme`, `update_theme`…). Deleting one sends its users back to Castor's own
  look.
- **Castor's own look** (`id: ""`) isn't a theme file: it is the default Material 3 palette,
  coloured from each person's theme colour.

Code: `server/castor_server/theme_spec.py` (format, checks, compiler), `theme_library.py`,
`api/themes.py`; web `lib/m3/theme.ts` (`applyTheme`, the theme colour), `lib/themes.svelte.ts`,
`app.html` (first paint).

## A small theme

```yaml
id: dusk
name: Dusk
description: Violet on charcoal, softly rounded.
modes: dark
colour: accent
default_colour: "#7c5cff"
shape:
  corners: { small: 4px, medium: 6px, large: 8px, extra-large: 12px }
elevation: hairline
colours:
  dark:
    primary: "#7c5cff"
    on-primary: "#ffffff"
    surface: "#101014"
    on-surface: "#e8e6f0"
    on-surface-variant: "#a6a2b5"
    surface-container-low: "#141419"
    surface-container: "#18181e"
    surface-container-high: "#1d1d24"
    surface-container-highest: "#24242c"
    outline: "#77738a"
    outline-variant: "#2e2d36"
```

Anything a theme leaves out keeps Castor's value, so a theme can be as small as a few colours.

## Fields

| Field | Default | What it does |
| --- | --- | --- |
| `id` | (required) | Lowercase letters, digits and dashes, 2–40 characters. Can't change once saved. |
| `name` | (required) | Shown in the picker. Up to 60 characters. |
| `description` | `""` | One sentence for the picker. |
| `modes` | `both` | `both`, `dark` or `light`. With one mode the light/dark switch is hidden. |
| `colour` | `accent` | What a person's theme colour changes: `palette` (every role is generated from it, as in Castor's own look), `accent` (only the primary family, see below) or `fixed` (nothing; the theme's colours always win). |
| `default_colour` | `#4f5bd5` | The colour the picker shows as "Default": the theme's own accent. |
| `fonts` | `[]` | Stylesheets to load with the theme (https only), such as a Google Fonts URL. |
| `font_faces` | `[]` | Font files to load (`@font-face`): `family`, `src` (an https URL or a path on this server, like `/fonts/x.woff2`), `weight` (`400`, `100 800`), `style`, `stretch` (`75% 112.5%`), `format` (`woff2`…). |
| `type.body` | Roboto Flex | Font family list for everything written. |
| `type.mono` | Roboto Mono | Font family list for commands, code and logs (the job terminal uses it in dark themes). |
| `type.icons` | `rounded` | Material Symbols set: `rounded`, `sharp` or `outlined` (loaded for you). |
| `type.icon_weight` | `400` | Icon stroke weight, 100–700. |
| `type.icon_fill` | (Castor's) | `false` never fills icons, `true` always does; left out, the active navigation icon is filled. |
| `shape.square` | `false` | Square every corner in the app (pills, avatars, dialogs, chips), except spinners and radio buttons. |
| `shape.corners` | Castor's | Any of `extra-small` (4px), `small` (8px), `medium` (12px), `large` (16px), `extra-large` (28px), as a length. |
| `elevation` | `shadow` | `shadow` (Material shadows), `hairline` (a 1px `outline-variant` line instead of every shadow, brighter on hover) or `none`. |
| `colours.dark`, `colours.light` | `{}` | Colour roles for each mode (see below). A dark-only theme has no `light`, and the other way round. |
| `preview` | from the colours | Four `#rrggbb` colours for the picker's tile: background, surface, text, accent. |
| `css` | `""` | Extra CSS, nested inside the theme's scope (see below). |

## Colour roles

Values are colours: `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `hsl()`, `oklch()`, `color-mix()`,
`color()`, `transparent` or `var(--…)`.

- **Material 3 roles** (`--md-sys-color-<role>`): `primary`, `on-primary`, `primary-container`,
  `on-primary-container`, the same four for `secondary`, `tertiary` and `error`; `background`,
  `on-background`, `surface`, `on-surface`, `surface-variant`, `on-surface-variant`,
  `surface-dim`, `surface-bright`, `surface-container-lowest`, `-low`, `surface-container`,
  `-high`, `-highest`; `inverse-surface`, `inverse-on-surface`, `inverse-primary`, `outline`,
  `outline-variant`, `shadow`, `scrim`, `surface-tint`; and the fixed roles (`primary-fixed`,
  `primary-fixed-dim`, `on-primary-fixed`, `on-primary-fixed-variant`, and the same for
  secondary and tertiary).
- **Categorical colours** (`--md-custom-color-…`): `blue`, `amber`, `violet`, `green`, each with
  `on-…`, `…-container` and `on-…-container`. Used where roles run out: activity kinds, charts,
  asset types.
- **Pinned notes** (`--note-…`): `note-default`, `note-red`, `note-orange`, `note-yellow`,
  `note-green`, `note-teal`, `note-blue`, `note-navy`, `note-purple`, `note-pink`, `note-brown`,
  `note-gray`.
- **`primary-hover`** (`--theme-primary-hover`): a filled primary button's hover, for your own
  CSS to use.

How Castor uses them: cards on a board sit on `surface-container-lowest`, columns on
`-low`, dialogs on `-high`, code wells on `-highest`; `on-surface-variant` is metadata text;
`outline` draws field borders (keep it 3:1 against the surface), `outline-variant` hairlines;
`secondary-container` is "selected" (navigation, chips); `tertiary` is "look at this";
`error` is failure.

Keep text roles at 4.5:1 or better against the surfaces they sit on: the server doesn't check
contrast.

### The theme colour with `colour: accent`

People can still pick a theme colour. In an accent theme it replaces the primary family only:
`primary`, `surface-tint`, `primary-fixed` → `--theme-accent`; `primary-hover` and
`on-primary-container` → `--theme-accent-hover`; `primary-container` →
`--theme-accent-container`; `inverse-primary` → `--theme-accent-inverse`; `primary-fixed-dim` →
`--theme-accent-dim`; `on-primary-fixed-variant` → `--theme-accent-deep`. The browser works these
out from the person's colour, vivid enough to read on the theme's surfaces (light tones on dark,
dark tones on light), and your values are what they get with "Default". So write your CSS
against `var(--md-sys-color-primary)` and `var(--theme-primary-hover)`, not a hex, and it follows
their colour.

## Extra CSS

`css` holds any rules you like. They are nested inside the theme's scope with CSS nesting, so
`.m3-btn { … }` means `html:root[data-skin='<id>'] .m3-btn { … }` and never reaches anyone on
another theme. Declarations at the top level apply to the root, which is the place for your own
variables:

```yaml
css: |
  --dusk-glow: color-mix(in oklab, var(--md-sys-color-primary) 30%, transparent);
  .m3-btn-filled:hover { background: var(--theme-primary-hover); }
  ::selection { background: var(--dusk-glow); }
```

Rules:

- No `@import`, `@charset`, `@namespace` or `@font-face` (use `fonts` and `font_faces`).
- `url()` only points at this server (`url(/fonts/x.png)`) or a `data:` URI.
- No backslash escapes, `</`, `expression()`, `javascript:` or other script.
- Braces must balance, outside strings and comments.
- At most 100 000 characters.

Your rules are unlayered, so they win over Castor's Tailwind utilities without `!important`.

### Hook classes

Castor's components carry classes for themes to target:

| Class | On |
| --- | --- |
| `.m3-btn`, `.m3-btn-filled`, `-tonal`, `-outlined`, `-text`, `-elevated`, `-danger` | Buttons |
| `.m3-tab`, `.m3-tab-ind` (`[aria-selected='true']` on the chosen tab) | Tabs and the chosen tab's indicator |
| `.m3-switch` (`[aria-checked='true']`), `.m3-switch-thumb` | Switches |
| `.m3-rail-ind` (`[data-active]`), `.m3-rail-label` | Navigation (rail and phone bar): the icon's indicator and the label |
| `.m3-dialog` | Dialogs (`.m3-dialog::backdrop` for the scrim) |
| `.m3-snackbar` | Snackbars (with `.bg-error-container` for errors) |
| `.app-shell` | The page behind everything signed in (navigation and content); give it a background to draw on the page |
| `.job-console` | The job output terminal's frame |
| `.prose-m3` | Rendered markdown (comments, descriptions, notes) |
| `.material-symbols-rounded` | Every icon |
| `.label-large`, `-medium`, `-small`, `.title-…`, `.headline-…`, `.body-…`, `.display-…` | The type scale |
| `.state-layer` | The hover and press tint (its `::before`) |
| `.focus-ring` | Keyboard focus outlines |

New hook classes come with new components; `themes/astralabs.yaml` uses most of these.
