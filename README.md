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
- **Blog** for announcements and donation tips.
- **App-like**: mobile-first layout with a bottom tab bar, dark mode, a web
  app manifest and a small service worker that caches only the offline page and
  core CSS/JS (never HTML pages or data).
- **SEO and ops**: `/robots.txt`, `/sitemap.xml`, branded 403, 404 and 500
  pages.

Phone numbers are private: they are never shown on public pages or used as a
display name.

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
| `SITE_CONTACT_EMAIL` | empty | Shown in the footer, About, FAQ and password-reset pages only when set. |
| `DONATION_INTERVAL_DAYS` | `120` | Recovery gap between donations, a reminder only and never medical clearance. Confirm the figure with your national blood transfusion service. |
| `DJANGO_SECRET_KEY` | insecure dev key | **Set this in production.** |
| `DJANGO_DEBUG` | `True` | Set to `False` in production. |
| `DJANGO_ALLOWED_HOSTS` | empty | Comma-separated hostnames, needed when `DJANGO_DEBUG=False`. |
| `EMAIL_BACKEND` | console backend | Emails (such as password-reset links) print to the console in development. Use an SMTP backend in production. |
| `DEFAULT_FROM_EMAIL` | `noreply@bloodbond.example` | Sender address for emails. |

## Tests

```bash
python manage.py test
```

CI runs the same command on every push and pull request.

## Project layout

| Directory | Contents |
| --- | --- |
| `accounts/` | Custom phone-based user, profile, login, sign-up, welcome page, password reset |
| `rewards/` | Donations, requests, directory, points, tiers, leaderboard |
| `pages/` | Home, learn hub, FAQ, About, robots, manifest, service worker |
| `insights/`, `stories/` | Impact numbers and member stories |
| `blogapp/` | Blog |
| `templates/`, `staticfiles/` | Shared templates, the `bloodbond.css` design kit, scripts and images |

See `DESIGN.md` for the design kit and the rules every page follows.

## License

This project is under the MIT license. See the [LICENSE](LICENSE) file.

Made with :heart: by <a href="https://github.com/Druvo" target="_blank">Druvo</a>
