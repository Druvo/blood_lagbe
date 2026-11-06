# BloodBond design contract

Name and tagline come from `settings.SITE_NAME` / `SITE_TAGLINE` (context vars
`{{ SITE_NAME }}`, `{{ SITE_TAGLINE }}`). Never hardcode the product name in
templates, emails, share text or JS; the old name "Blood Lagbe" must not appear.

## Stack rules
- Django 3.2, SQLite, no new third-party packages, no CDN scripts, no network
  assets other than the Google Fonts already in `base.html`.
- The old Sociala theme (`style.css`, bootstrap 5 utilities such as `fw-700`,
  `me-4`, `card`, `btn-danger`, `form-control`, `bg-current`) is **no longer
  loaded**. Use only `bb-` classes from `staticfiles/css/bloodbond.css`. Icons are
  the feather font: `<i class="feather-heart"></i>`.
- Templates `{% extends 'base/base.html' %}` and fill `title`, `body`, and
  optionally `meta`, `extra_style`, `extra_scripts`. The base already renders
  flash messages (`django.contrib.messages`) at the top, so templates must NOT
  loop over `messages` themselves.
- Mobile first. Check every page at 375px and 1280px. Tap targets >= 44px. No
  horizontal page scroll. Dark mode must stay readable: use the CSS variables
  (`var(--bb-ink)`, `var(--bb-surface)`, ...), never hardcoded white/black text
  or backgrounds (the brand red `#d8213a` and `bb-card--hero` are fine).
- Less text, more selects/chips/buttons. Plain, kind English. Bangla-friendly
  (font stack already includes Hind Siliguri); never break Bangla text.
- Images: author inline SVG yourself. Do not download third-party assets.
- Server-side SVG for charts (see `.bb-chart`, `.bb-hbar`); no JS chart libs.
- Phone numbers are private. Never render `user.phone` on a public page, and
  never use a phone number as a display name (use
  `rewards.leaderboard.display_name`). Contact numbers only after consent.
- Safety copy: eligibility and the donation interval are reminders, never medical
  clearance. Interval label is `{{ donation_interval_label }}` (context processor).
- Lives: show "est. people helped" only as an estimate; headline verified donations.

## Layout classes
`bb-container` (+`--narrow`, `--form`), `bb-section` (+`--tight`, `--alt`),
`bb-stack` (+`--sm`, `--lg`), `bb-row` (+`--between`, `--center`, `--end`, `--top`),
`bb-grow`, `bb-grid`, `bb-cols-2|3|4` (responsive), `bb-cols-auto`, `bb-split`
(2/3 + 1/3 on desktop, `--even`), spacing `bb-mt-0..4`, `bb-mb-0..3`,
`bb-center`, `bb-right`, `bb-hide-sm` (hidden on mobile), `bb-only-sm`.

## Components
- Text: `bb-h1..h4`, `bb-lead`, `bb-muted`, `bb-small`, `bb-xs`, `bb-strong`,
  `bb-eyebrow` (small red label above a heading), `bb-gradient-text`, `bb-num`.
- Card: `bb-card` (+`--flat`, `--lift` hover, `--accent` red left border, `--ok`,
  `--warn`, `--hero` red gradient, `--soft`), `bb-card__title`.
- Buttons: `bb-btn` + `--primary|--dark|--ghost|--soft|--ok|--white`, sizes
  `--sm|--lg`, `--block`. Use `<button>` for actions, `<a>` for navigation.
- Forms: wrap in `<form class="bb-form">`; plain inputs/selects/textareas are styled
  automatically. Fields: `<div class="bb-field"><label class="bb-label">..</label>
  {{ form.x }}<div class="bb-help">..</div>{{ form.x.errors }}</div>`. Checkbox rows:
  `<label class="bb-check"><input type="checkbox" ..><span><strong>..</strong>
  <span class="bb-help">..</span></span></label>`. Form errors: `.errorlist` is styled.
- Chips/pills: `bb-chips` > `bb-chip` (+`--active`); `bb-pill` (+`--ok|--warn|--bad|--info`).
- Blood group badge: `<span class="bb-bloodtag"><span>O+</span></span>` (+`--sm|--lg`).
- Alerts: `bb-alert` + `--ok|--bad|--warn|--info`.
- Stats: `bb-stat` > `bb-stat__value` (+`data-count="N"` for count-up) + `bb-stat__label`.
- Progress: `bb-progress` > `<span style="width:40%">`. (`--ok` green)
- Table: `bb-table-wrap` > `bb-table` (use `bb-right` on numeric cells).
- Misc: `bb-pagehead`, `bb-empty` (illustrated empty state, see Art system), `bb-avatar` (+`--lg`),
  `bb-seg` (segmented links, `.is-active`), `bb-tile`, `bb-divider`, `bb-timeline`
  (`li.is-done`), `bb-hero`, `bb-glass`, `bb-toast` via `window.bbToast('msg')`,
  copy button `data-copy="text-or-#selector"`, `bb-noprint`.
- Charts: `<svg class="bb-chart" viewBox="0 0 W H">` with `.bb-axis`, `.bb-bar`,
  `.bb-line`, `.bb-area`; horizontal bars via `bb-hbar` (label, `bb-hbar__track>span`, value).
- Accessibility: one `<h1>` per page, labels on every input, `aria-label` on icon-only
  buttons, visible focus (already styled), `alt=""` for decorative images.

## URL names already wired (nav, footer, tab bar rely on them)
home, request-list, request-create, request-detail, request-respond, request-close,
donor-directory, contact-inbox, contact-donor, contact-respond, leaderboard,
log-donation (the donor "hub"), badge-card, privacy (profile and privacy), signup,
login, logout, impact, stories, learn, about, faq, blogapp:blog-view.
Do not rename these. New routes are fine.

## Data & behaviour you can rely on
- Only admin-**approved** donations count (points, tiers, leaderboard, stats).
  `Donation.status` pending/approved/rejected. Cooldown counts pending + approved.
- `Donation.Meta.ordering` pollutes `values().annotate()`; always `.order_by()` first.
- Profile (accounts.Profile): `blood_group`, `district`, `in_directory` (opt-in),
  `available_to_donate`, `show_on_leaderboard`, `public_badge_card`.
- `rewards.geo`: `DISTRICT_CHOICES`, `DISTRICT_NAMES`, `DISTRICT_TO_DIVISION`, `DIVISIONS`.
- `rewards.blood`: `BLOOD_GROUP_CHOICES`, `COMPATIBLE_DONORS`, `donor_groups_for`.
- `rewards.models.Place` (hospital/blood bank), `ContactRequest`, `BloodRequest`,
  `RequestResponse`, points/tiers/streak helpers (`get_donor_points`,
  `get_donor_badge_progress`, `get_current_streak`, `get_eligibility`, ...).
- Dev data: users 01711000001..04 (password `testpass123`), db is `db.sqlite3`.
  Log into the browser without typing a password: in `manage.py shell` use
  `Client(HTTP_HOST='localhost').force_login(user)` and copy the `sessionid` cookie
  into the browser with `document.cookie`. Dev server: http://localhost:9100.

## Definition of done for any page
Renders in light and dark, 375px and 1280px, no console errors, works without
JS where feasible, has tests for views, permissions, edge cases and privacy.

## Art system (illustrations, medals, motion)

All graphics are hand-authored SVG, no raster files, no external hosts, no JS
libraries. New static weight: 1.8 KB of SVG mask files, about 19 KB of CSS and 1.5 KB
of JS, plus inline SVG in templates (home hero 7.6 KB; other scenes 1 to 2.5 KB; the
full header set is 6 KB but only one scene renders per page).

Where things live
- `templates/snippets/art/*.html`: inline scenes. `hero.html` (home hero, also the
  auth split panel), `steps.html` (`step=1|2|3`), `strip.html` (`which=who|often|safe`),
  `header.html` (`which=learn|faq|about|impact|stories|events|leaderboard`, optional
  `inline=True` for headers that are a flex row), `error.html` (`which=404|403|csrf`).
- `templates/snippets/empty_art.html` (`scene=requests|alerts|events|stories|donations|search`)
  and `templates/snippets/empty_state.html` (adds title, text, optional button;
  `card=True` wraps it in a `bb-card`). Prefer the include over hand-written `bb-empty`
  markup. Where a template already has custom markup, keep it and include only
  `empty_art.html` inside the `bb-empty` block.
- `staticfiles/images/art/`: `wave.svg`, `drops.svg`, `skyline.svg`. They are used as
  CSS masks (tinted by `background`), never as `<img>`.
- `core/templatetags/art_tags.py`: `{% load art_tags %}`
  - `{% badge_art badge 56 %}` metallic medal. Shape encodes the tier (circle Bronze,
    hexagon Silver, scalloped seal Gold, gem Platinum, crowned shield Legend, squircle
    with flame for streaks, shield with heart for hero badges), so it never depends on
    colour alone. Colour comes from `Badge.color` only after a strict hex check, else a
    per-tier default. The optional third argument is an aria-label; without it the SVG
    is `aria-hidden` (the tier name is always printed next to it).
  - `{% progress_ring percent 84 %}` circular progress (XP; the hub recovery ring has
    its own markup).
- CSS: the "ART SYSTEM" block at the end of `staticfiles/css/bloodbond.css`.
- JS: scroll reveal at the end of `staticfiles/js/bloodbond.js`.

Tokens and classes
- People: `--art-skin-1..5`, `--art-hair`, clothing `--art-teal|gold|green|indigo|cream|coral|plum`.
  Scene fills are classes: `a-red`, `a-rose`, `a-soft`, `a-paper`, `a-mute`, `a-skin-*`,
  `a-teal`, ... and strokes `a-stroke*`. Use `var(--bb-...)` or `currentColor`, never a
  literal hex inside a scene (the hero is the one scene that sits on a fixed red, so it
  uses its own `--art-*` / `ha-*` tokens, identical in both themes).
- Motion: `bb-art-float`, `bb-art-beat`, `bb-art-sway` (SVG elements), `bb-pulse`
  (heartbeat ring on a primary CTA, put it on one or two buttons per page, not all),
  scroll reveal is automatic for children of `bb-cols-*`, `bb-cols-auto`, `bb-strip` and
  `[data-reveal]`.
- Decor: `bb-hero--art`, `bb-hero__wave`, `bb-section--wave` (wave above an alt section),
  `bb-section--pattern`, `bb-section--rose`, `bb-container--art` + `.bb-headart`.

How to add a scene
1. Draw it in a 160x120 (empty state), 200x130 (header, steps) or 300x200 (error) viewBox.
2. Fill with `a-*` classes, add `class="bb-art"` and `aria-hidden="true" focusable="false"`.
3. Put it in `snippets/art/` and include it. Keep it under about 2.5 KB; no `<image>`,
   no `<filter>`, no text (numerals in the leaderboard header are the one exception).

Accessibility rules
- Decorative art is `aria-hidden="true"` and never the only carrier of meaning: a
  heading or label next to it says the same thing. Do not put real text in an SVG.
- Animations are decorative, off under `prefers-reduced-motion: reduce` (a global rule
  plus explicit ones), and the page must read fine with them stopped.
- Reveal: html gets class `js` from an inline script in `base.html`; without it, or if
  `bloodbond.js` does not load within 4 s, everything is simply visible. Only opacity
  and transform animate, so nothing shifts layout.
- Focus ring is a 3px outline plus a soft halo; on red surfaces (`bb-hero`,
  `bb-card--hero`, footer) it flips to white.
- Header art is hidden below 760px; the hero art shrinks to about 330px wide on phones
  and sits after the headline and buttons.

Size budget: keep new static files under 250 KB (currently about 25 KB including CSS and JS), no raster,
and give every `<svg>`/`<img>` width and height so nothing shifts. Below-the-fold
`<img>` needs `loading="lazy"` (there are none today).
