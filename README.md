# Leap ERP

A comprehensive Enterprise Resource Planning (ERP) system built with Django for Leap Networks. This system manages projects, sales operations, costing/BOM management, and sales call tracking across multiple regional offices.

## Features

### Project Management
- Track projects/bids across multiple regions (UK, Saudi Arabia, Pacific Asia, Global)
- Project status tracking with customizable statuses
- Financial tracking with estimated values and success quotients
- Regional filtering and dashboards

### Costing Module (BOM Management)
- Create and manage Bill of Materials (BOM) costing sheets
- Multi-currency support with automatic exchange rate conversion
- Configurable rates: Margin, Discount, Shipping, Customs, Finances, Installation
- Sheet-level defaults with per-line-item overrides
- Export to Excel and PDF (Professional Commercial Offer format)

### Sales Call Reports
- Track sales interactions with clients
- Record contact details, call goals, and outcomes
- Schedule next actions with reminders
- Manager/Admin response system for feedback
- Export capabilities

### Reports & Analytics
- Vendor and Partner management
- EPC contractor tracking
- Exhibition and trade show management
- Procurement portal registration tracking
- Certification management
- Sales contacts database

### User Management
- Role-based access control (Admin, Manager, Sales Rep)
- Region-based data filtering
- Secure authentication

## Tech Stack

- **Backend:** Django 6.0
- **Database:** PostgreSQL (Production) / SQLite (Development)
- **Frontend:** Bootstrap 5, Bootstrap Icons
- **PDF Generation:** ReportLab
- **Excel Export:** OpenPyXL
- **Deployment:** Render

## Installation

### Prerequisites
- Python 3.11+
- pip
- Git

### Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/AqSa2205/leap-erp.git
   cd leap-erp
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Load initial data**
   ```bash
   python manage.py load_initial_data
   ```

7. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

8. **Run development server**
   ```bash
   python manage.py runserver
   ```

9. **Access the application**
   - Main app: http://127.0.0.1:8000/
   - Admin panel: http://127.0.0.1:8000/admin/

> **Note:** steps 4–8 need no production credentials. With `DATABASE_URL`
> unset, Django creates a local `db.sqlite3` automatically. If setup fails
> asking for a secret, ask — do not go looking for production values.

## Working on this codebase

### Ground rules

These exist because this project has already lost its entire commercial
pipeline to an accidental deletion on production. Please take them literally.

1. **Never point your local checkout at the production database.**
   Leave `DATABASE_URL` unset so you use local SQLite. A single `migrate`,
   `flush`, `loaddata`, or test run against production destroys real client
   data. Production credentials are not distributed to developers.
2. **Never commit `.env`, `db.sqlite3`, or any credential.** Both are
   gitignored; keep it that way.
3. **Leave `USE_R2` off locally** so uploads go to `media/` instead of the
   shared production bucket.
4. **Keep the console email backend locally** so test runs cannot email real
   staff or clients.
5. **Never push directly to `main` or `dev`.** Both are protected; work goes
   in via pull request.
6. **Flag destructive migrations in your PR description.** Anything
   containing `RemoveField`, `DeleteModel`, or `RunPython` needs an explicit
   call-out so it gets a careful review before it reaches production.

### Branch workflow

```
feat/<your-name>-<short-description>   ← your work; short-lived (1–2 days)
        ↓  pull request + review
dev                                    ← integration branch
        ↓
test                                   ← staging (own service + own database)
        ↓  pull request + review
main                                   ← PRODUCTION — deploys live on push
```

Day-to-day:

```bash
# Start a piece of work
git checkout dev && git pull origin dev
git checkout -b feat/yourname-what-youre-doing

# Keep up to date while you work (do this daily — small conflicts beat big ones)
git pull --rebase origin dev

# Publish and open a PR against dev
git push -u origin feat/yourname-what-youre-doing
```

- **One feature per branch**, merged within a couple of days. Long-lived
  branches are what cause painful conflicts — not the number of people.
- **Rebase daily.** Conflicts then arrive in three-line doses.
- **Only ever force-push your own `feat/` branch.** Never `dev`, `test`, or
  `main`.
- **Never merge `main` backwards into `dev`.** Changes flow one direction.
- Where possible, **split work by module** (procurement / HR / costing). The
  large view files are conflict magnets when two people edit them at once.

### Before you open a PR

```bash
python manage.py test          # full suite must pass (do not use pytest)
python manage.py makemigrations --check --dry-run   # no uncommitted model changes
```

### Avoiding merge conflicts

Most conflicts are habits, not git. With more than one person on the repo:

1. **Keep branches short and small.** One feature per branch, opened and
   merged within a day or two. A branch's *lifespan* causes conflicts far
   more than the number of people. A 200-line PR reviews in minutes; a
   2000-line one blocks everyone.
2. **Rebase on `dev` every morning** before you write code:
   ```bash
   git checkout dev && git pull origin dev
   git checkout feat/your-branch
   git rebase dev
   ```
   Conflicts then arrive in three-line doses instead of a 300-line mess at
   merge time.
3. **Split work by module and say so.** Each person owns an app
   (procurement / hr / costing / …). The large view files are conflict
   magnets when two people edit them the same day — a one-line "I'm in the
   costing PDF export today" in the team chat prevents it.

### Working with migrations (read this — it is the #1 thing that breaks)

Django numbers migrations sequentially, so two people who both run
`makemigrations` on their own branch each create an `0022_*` — and when both
merge, Django refuses to run with a "conflicting migrations" error.

- **Pull `dev` immediately before `makemigrations`**, so your migration
  number follows the latest one on `dev`.
- **One model change per PR.** Do not batch unrelated migrations together.
- **Never edit or delete a migration that has already merged to `dev` or
  `main`.** It has already run on production; changing it corrupts the
  migration history. Write a new migration instead.
- **Flag every migration in your PR description**, especially anything with
  `RemoveField`, `DeleteModel`, or `RunPython` — those are destructive and
  get a careful review before they reach production.
- If a collision slips through, the fix is:
  ```bash
  git checkout dev && git pull origin dev
  git checkout feat/your-branch && git rebase dev
  python manage.py makemigrations --merge   # only if genuinely needed
  ```

### Never commit these

`db.sqlite3` and `.env` are gitignored — keep it that way. If either shows up
in `git status`, something is wrong; do not force it in. A committed
`db.sqlite3` overwrites everyone else's local database on their next pull.

## Project Structure

```
leap-erp/
├── accounts/          # User authentication and management
├── costing/           # BOM and costing module
├── dashboard/         # Main dashboard views
├── erp_leap/          # Django project settings
├── fixtures/          # Data fixtures for deployment
├── projects/          # Project/bid management
├── reports/           # Sales reports and analytics
├── static/            # Static files (CSS, JS, images)
├── templates/         # HTML templates
├── manage.py
├── requirements.txt
├── render.yaml        # Render deployment config
└── build.sh           # Build script for deployment
```

## Deployment

The application is configured for deployment on Render. See `DEPLOYMENT.md` for detailed deployment instructions.

### Quick Deploy to Render

1. Fork this repository
2. Connect your GitHub account to Render
3. Create a new Web Service from the repository
4. Render will automatically use `render.yaml` configuration

## Regional Offices

- **LNUK** - United Kingdom
- **LNA** - Leap Networks Arabia (Saudi Arabia)
- **PA** - Pace Arabia
- **NEO-Dubai** - Dubai Office
- **NEO-KSA** - KSA Office
- **Global** - Global Operations

## License

Proprietary - Leap Networks. All rights reserved.

## Support

For support and inquiries, contact the Leap Networks IT team.
