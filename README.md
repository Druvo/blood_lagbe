<h1 align="center">BloodBond</h1>

<h4 align="center">
  Give blood. Build bonds. Save lives. <br>
  A blood-donation community for Bangladesh, built with Python &amp; Django.
</h4>

[![Python application](https://github.com/BloodLagbe/blood_lagbe/actions/workflows/python-app.yml/badge.svg)](https://github.com/BloodLagbe/blood_lagbe/actions/workflows/python-app.yml)

> **Note on names.** The project used to be called Blood Lagbe, and the
> repository, the project directory (`blood_lagbe/`) and the Django settings
> package (`blood_lagbe.settings`) still use that name. The product name shown
> to people is **BloodBond** and comes from the `SITE_NAME` setting, so
> renaming it is one environment variable.

## Features

- **Urgent blood requests**: post a request by blood group, hospital and
  district; donors answer with "I can help". Requests expire, can be reported,
  and are hidden once enough people flag them until staff review them.
- **Donor directory with consent**: donors opt in. Members send a short
  request and a donor's phone number is shared only if they accept.
- **Verified donations**: donors log a donation and a volunteer approves it in
  the admin. Only approved donations count towards points, tiers, streaks, the
  leaderboard and community stats.
- **Rewards**: points, tiers, streaks, hero badges for answering requests, a
  leaderboard and shareable badge cards. Privacy switches let anyone opt out
  of the leaderboard and of badge-card sharing.
- **Impact and stories**: public impact numbers and member stories.
- **Learn hub**: a "Can I donate?" pre-check (always a soft recommendation,
  never medical clearance), blood-group compatibility table, what happens on the
  day, after-care tips and myths vs facts, plus FAQ and About pages.
- **Accounts**: sign up with a phone number (blood group, district and an
  opt-in to the directory are optional), log in by phone, a first-run welcome
  checklist, and password reset by email.
- **Moments**: when a request you answered is closed you get a short "your
  blood reached someone" update; requesters can send thank-you notes (shown on
  a thanks wall); first donations and new tiers get a celebration; a "ready
  again in N days" countdown and an opt-in reminder; one streak freeze a year;
  a live "people responding" counter on open requests.
- **Today and habits**: three small daily actions (share a request, confirm
  your availability, learn a fact or take a myth-or-fact quiz), a cosmetic visit
  streak that never touches points, an opt-in weekly digest, a year in review,
  and titles (Request Champion, Connector, Storyteller) for people who cannot
  donate.
- **Blood drives and campaigns**: members host drives (staff approve them),
  RSVP, download a calendar file, enter their squad as a team, and earn
  seasonal campaign badges. Squad challenges show progress towards a goal.
- **Alerts**: an in-app inbox and opt-in email for requests that match your
  blood group and district. Capped per day, never during your recovery gap.
- **Growth and trust**: invite links (rewarded when a friend's first donation
  is verified, not at sign-up), opt-in public profiles, WhatsApp, Messenger and
  Facebook share text for requests, verified-hospital chips, and donor controls
  to hide a shared number again or block a requester.
- **English and Bangla**: a language switcher (nav, account menu on phones,
  footer). Bangla covers the main pages; the rest stays English. The Bangla
  text is a first draft and needs review by a native speaker, especially the
  health and safety copy.
- **Staff desk** at `/desk/`: counts of what waits for a moderator.
- **Blog** for announcements and donation tips.
- **App-like**: mobile-first layout with a bottom tab bar, dark mode, a web
  app manifest and a small service worker that caches only the offline page and
  core CSS/JS (never HTML pages or data).
- **SEO and ops**: `/robots.txt`, `/sitemap.xml`, branded 403, 404 and 500
  pages.

Phone numbers are private: they are never shown on public pages or used as a
display name.

## Languages (English and Bangla)

English is the default. Visitors switch to Bangla (বাংলা) with the "EN | বাংলা"
control in the top bar, the account menu and the footer; the choice is kept in
the `django_language` cookie, and a browser that asks for Bangla gets it on the
first visit. URLs do not change. Translations live in
`locale/bn/LC_MESSAGES/django.po` (edited by hand; it needs review by a native
speaker). GNU gettext is not needed:

```bash
python manage.py compile_po               # django.po -> django.mo (commit both)
python scripts/check_translations.py      # wrapped strings with no Bangla yet
```

Wrap new text with `{% trans %}` / `{% blocktrans %}` in templates and
`gettext` / `gettext_lazy` in Python, add the msgid to the `.po`, recompile.
The test `core.test_translations` fails when a wrapped string has no Bangla.

## Setup

```bash
# Get the code
git clone https://github.com/BloodLagbe/blood_lagbe.git
cd blood_lagbe

# Install dependencies (Python 3 and pip)
pip install -r requirements.txt

# Create the database
python manage.py migrate

# Create an admin (the login is a phone number)
python manage.py createsuperuser

# Run it
python manage.py runserver
# then open http://localhost:8000
```

Approve logged donations in the admin at `/admin/`.

## Configuration

All settings have working defaults for development. Set environment variables
to change them.

| Variable | Default | What it does |
| --- | --- | --- |
| `SITE_NAME` | `BloodBond` | Product name in pages, emails and the web manifest. |
| `SITE_TAGLINE` | `Give blood. Build bonds. Save lives.` | Tagline in the footer, About page and manifest. |
| `SITE_URL` | empty | Public address (`https://bloodbond.example`, no trailing slash) used for links in emails and share text. Falls back to the first `DJANGO_ALLOWED_HOSTS` entry. |
| `SITE_CONTACT_EMAIL` | empty | Shown in the footer, About, FAQ and password-reset pages only when set. |
| `DONATION_INTERVAL_DAYS` | `120` | Recovery gap between donations, a reminder only and never medical clearance. Confirm the figure with your national blood transfusion service. |
| `DJANGO_SECRET_KEY` | insecure dev key | **Set this in production.** With `DJANGO_DEBUG=False` the app refuses to start on the built-in key. |
| `DJANGO_DEBUG` | `True` | Set to `False` in production. This also turns the HTTPS switches below on. |
| `DJANGO_ALLOWED_HOSTS` | empty | Comma-separated hostnames, needed when `DJANGO_DEBUG=False`. Also protects password-reset links from Host header poisoning. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated origins (`https://bloodbond.example`) when a proxy changes the host. |
| `DJANGO_SECURE_SSL_REDIRECT` | on when not debugging | Redirect HTTP to HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE` | on when not debugging | Send the cookies over HTTPS only. |
| `DJANGO_HSTS_SECONDS` | `31536000` when not debugging, else `0` | HSTS lifetime. Use a small value first. |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` | on when not debugging | HSTS for subdomains. |
| `DJANGO_HSTS_PRELOAD` | off | Opt in only once you want the site on the browser preload list. |
| `DJANGO_BEHIND_PROXY` | off | Trust `X-Forwarded-Proto` from a reverse proxy you control (needed for HTTPS redirects behind one). |
| `DJANGO_RESET_TIMEOUT` | `10800` | Seconds a password-reset link stays valid. |
| `EMAIL_BACKEND` | console backend | Emails (such as password-reset links) print to the console in development. Use an SMTP backend in production. |
| `DEFAULT_FROM_EMAIL` | `noreply@bloodbond.example` | Sender address for emails. |

## Going to production

Set these, then run `python manage.py check --deploy`. With the values below
the only remaining warning is `security.W021` (HSTS preload), which is a
deliberate opt-in.

```bash
export DJANGO_DEBUG=False
export DJANGO_SECRET_KEY="<a long random string, e.g. python -c 'import secrets; print(secrets.token_urlsafe(64))'>"
export DJANGO_ALLOWED_HOSTS=bloodbond.example,www.bloodbond.example
export DJANGO_CSRF_TRUSTED_ORIGINS=https://bloodbond.example,https://www.bloodbond.example
export DJANGO_BEHIND_PROXY=True            # only if a reverse proxy terminates HTTPS
export EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend   # plus the EMAIL_HOST_* variables
```

Other things to know:

- **HTTPS is required.** With `DJANGO_DEBUG=False` the session and CSRF cookies
  are `Secure`, HTTP redirects to HTTPS and HSTS is sent. Serve the site over
  HTTPS before turning debug off.
- **Static and media files** are not served by Django when `DEBUG` is off.
  Serve `staticfiles/` (collected) and `media/` from the web server. There is
  no public file upload; profile and blog pictures are added by staff in the
  admin (type and size are validated: JPG, PNG or WebP, up to 2 MB).
- **Rate limits** (failed logins, password-reset mail, join-code guessing) are
  kept in the Django cache. The default in-memory cache is per process; with
  several workers configure a shared cache (Redis or Memcached) in `CACHES` so
  the counters are shared. The daily caps on requests, contact requests,
  pledges, reports and stories are counted in the database and always apply.
- **Behind a proxy** the limits use the connection's address. Do not trust
  `X-Forwarded-For` unless the proxy sets it; configure the proxy to pass the
  real client address as the connection address if you need per-person limits.
- **Logout** is a POST form with a CSRF token in the menu. A plain `GET
  /accounts/logout/` still works for old bookmarks: it only ends the visitor's
  own session, so forging that request is a nuisance, not a breach.
- **Pages for signed-in people** are sent with `Cache-Control: private,
  no-cache`, so a shared proxy never stores a page that carries a phone number.
  The public impact feed (`/impact/data.json`, `/impact/export.csv`) is
  aggregate-only and cacheable.
- **Content-Security-Policy** is not set: pages use small inline scripts and
  styles. Everything user-supplied is escaped, and the only `autoescape off`
  is the plain-text password-reset email.
- Staff accounts have every admin permission (`User.has_perm` returns `True`
  for anyone with `staff` set), so only give `staff` to people you trust fully.

### Translations

Strings are wrapped with `{% trans %}` or `gettext_lazy` and the Bangla text
lives in `locale/bn/LC_MESSAGES/django.po`. This project does not need the
gettext tools: after editing the `.po`, run

```bash
python manage.py compile_po                      # writes django.mo
python scripts/check_translations.py --stale     # missing or stale entries
```

`core/test_translations.py` fails if a wrapped string has no Bangla entry, or
the compiled `.mo` is out of date, so the two cannot drift apart.

### Scheduled jobs

Reminders, digests and cleanup run from one command. Put it in cron (or any
scheduler) once a day:

```bash
python manage.py run_scheduled            # all jobs
python manage.py run_scheduled --dry-run  # say what would happen
python manage.py run_scheduled --list     # job names
```

It runs `send_impact_updates`, `send_ready_reminders`, `send_weekly_digest`,
`award_campaign_badges` and `prune_notifications`. Each is idempotent, so a
second run the same day sends nothing twice, and one failing job does not stop
the others (the command exits non-zero so your scheduler can alert). All
emails are opt-in and carry a one-click off link.

Emails are sent while the request or command runs. That is fine for a small
community; with large volumes move sending to a queue.

### Privacy rules the tests enforce

`rewards/test_privacy_audit.py` crawls every URL as many kinds of viewer and
fails if a phone number, email address, or the name of an opted-out or
anonymous person appears where it should not. A contact phone is shown only to
logged-in viewers on its active, unflagged request page; responders only to
the requester and staff; a donor's phone only after they accept; a story
marked anonymous (or by someone hidden from public listings) never carries a
name; a badge card is public only when its owner allows sharing **and** is
listed on the leaderboard.

## Tests

```bash
python manage.py test
```

The suite includes `rewards/test_privacy_audit.py`,
`rewards/test_security_audit.py` and `pages/test_accessibility.py`. Tests start
with an empty cache (see `core/test_runner.py`) and do not depend on each other.

CI runs the same command on every push and pull request.

## Project layout

| Directory | Contents |
| --- | --- |
| `accounts/` | Custom phone-based user, profile, login, sign-up, welcome page, password reset |
| `rewards/` | Donations, requests, directory, points, tiers, leaderboard |
| `pages/` | Home, learn hub, FAQ, About, robots, manifest, service worker |
| `insights/`, `stories/` | Impact numbers and member stories |
| `moments/`, `habits/` | Thank-yous, celebrations, countdowns, daily actions, digest, year in review |
| `events/`, `squads/` | Blood drives, campaigns, challenges, squads |
| `alerts/`, `growth/` | Matched alerts and inbox; invites, public profiles, sharing, trust |
| `core/` | Rate limits, staff desk, scheduled-jobs command, test runner |
| `blogapp/` | Blog |
| `templates/`, `staticfiles/` | Shared templates, the `bloodbond.css` design kit, scripts and images |

See `DESIGN.md` for the design kit and the rules every page follows.

## License

This project is under the MIT license. See the [LICENSE](LICENSE) file.

Made with :heart: by <a href="https://github.com/Druvo" target="_blank">Druvo</a>
