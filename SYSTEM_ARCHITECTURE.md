# Leap Networks ERP - System Architecture & Tech Stack

## Overview

The Leap Networks ERP is a web-based Enterprise Resource Planning system designed to manage sales pipelines, track projects, handle document management, and generate reports for a multi-regional telecommunications company.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Web Browser (Chrome, Firefox, Edge)           │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │   │
│  │  │  Dashboard  │  │  Projects   │  │  Documents  │  │  Reports   │ │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTP/HTTPS
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PRESENTATION LAYER                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     Django Template Engine                           │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │  Bootstrap 5  │  Chart.js  │  Bootstrap Icons  │  Custom CSS │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION LAYER                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Django 5.x Web Framework                        │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐       │   │
│  │  │  accounts  │ │  projects  │ │ dashboard  │ │  reports   │       │   │
│  │  │    App     │ │    App     │ │    App     │ │    App     │       │   │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘       │   │
│  │                                                                      │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │  Authentication  │  Authorization  │  Session Management     │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Django ORM                                    │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │  Models  │  Migrations  │  QuerySets  │  Managers            │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    SQLite Database (Development)                     │   │
│  │                    PostgreSQL (Production Ready)                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            FILE STORAGE                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  /static/          │  /media/                                        │   │
│  │  - CSS files       │  - Uploaded documents                          │   │
│  │  - JavaScript      │  - Vendor quotations                           │   │
│  │  - Images/Logos    │  - Proposals                                   │   │
│  │  - Favicon         │  - Technical documents                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

### Backend

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.11+ | Core programming language |
| **Django** | 5.x | Web framework |
| **Django ORM** | Built-in | Database abstraction layer |
| **SQLite** | 3.x | Development database |
| **PostgreSQL** | 15+ | Production database (recommended) |

### Frontend

| Technology | Version | Purpose |
|------------|---------|---------|
| **HTML5** | - | Markup structure |
| **CSS3** | - | Styling |
| **JavaScript** | ES6+ | Client-side interactivity |
| **Bootstrap** | 5.3.2 | CSS framework & components |
| **Bootstrap Icons** | 1.11.1 | Icon library |
| **Chart.js** | Latest | Data visualization & charts |

### Django Apps & Extensions

| Package | Purpose |
|---------|---------|
| **django-crispy-forms** | Enhanced form rendering |
| **crispy-bootstrap5** | Bootstrap 5 form templates |
| **django-filter** | Queryset filtering |
| **django.contrib.humanize** | Number formatting (intcomma) |

### Development Tools

| Tool | Purpose |
|------|---------|
| **Git** | Version control |
| **pip** | Package management |
| **Django Admin** | Backend administration |

---

## Application Structure

```
ERP-Leap/
├── erp_leap/                    # Main Django project
│   ├── __init__.py
│   ├── settings.py              # Project configuration
│   ├── urls.py                  # Root URL routing
│   ├── wsgi.py                  # WSGI entry point
│   └── asgi.py                  # ASGI entry point
│
├── accounts/                    # User management app
│   ├── models.py                # User, Role models
│   ├── views.py                 # Login, logout, profile views
│   ├── forms.py                 # Authentication forms
│   ├── decorators.py            # Role-based access decorators
│   └── urls.py                  # Account routes
│
├── projects/                    # Project management app
│   ├── models.py                # Project, Region, Status, Document models
│   ├── views.py                 # CRUD views for projects & documents
│   ├── forms.py                 # Project & document forms
│   └── urls.py                  # Project routes
│
├── dashboard/                   # Analytics & dashboard app
│   ├── views.py                 # Dashboard views with statistics
│   └── urls.py                  # Dashboard routes
│
├── reports/                     # Reporting app
│   ├── models.py                # Vendor, EPC, Exhibition, etc.
│   ├── views.py                 # Report generation views
│   └── urls.py                  # Report routes
│
├── templates/                   # HTML templates
│   ├── base.html                # Master template with sidebar
│   ├── accounts/                # Auth templates
│   ├── projects/                # Project templates
│   ├── dashboard/               # Dashboard templates
│   └── reports/                 # Report templates
│
├── static/                      # Static assets
│   ├── images/                  # Logos, favicon
│   └── css/                     # Custom stylesheets
│
├── media/                       # User uploaded files
│   └── documents/               # Uploaded documents
│
├── db.sqlite3                   # SQLite database
├── manage.py                    # Django management script
└── requirements.txt             # Python dependencies
```

---

## Database Schema

### Core Models

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│      User       │     │     Region      │     │  ProjectStatus  │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id              │     │ id              │     │ id              │
│ username        │     │ name            │     │ name            │
│ email           │     │ code (UK/LNA/PA)│     │ category        │
│ password        │     │ currency        │     │ color           │
│ first_name      │     │ is_active       │     │ order           │
│ last_name       │     └─────────────────┘     └─────────────────┘
│ role (FK)       │              │                      │
│ region (FK)     │              │                      │
└─────────────────┘              │                      │
        │                        │                      │
        │                        ▼                      ▼
        │              ┌─────────────────────────────────────┐
        │              │              Project                 │
        │              ├─────────────────────────────────────┤
        └──────────────│ id                                  │
                       │ project_name                        │
                       │ proposal_reference                  │
                       │ client_rfq_reference                │
                       │ owner (FK → User)                   │
                       │ region (FK → Region)                │
                       │ status (FK → ProjectStatus)         │
                       │ estimated_value                     │
                       │ actual_sales                        │
                       │ year                                │
                       │ submission_deadline                 │
                       │ created_at, updated_at              │
                       └─────────────────────────────────────┘
                                        │
                                        │ 1:N
                                        ▼
                       ┌─────────────────────────────────────┐
                       │             Document                 │
                       ├─────────────────────────────────────┤
                       │ id                                  │
                       │ name                                │
                       │ document_type                       │
                       │ file                                │
                       │ project (FK → Project, nullable)    │
                       │ vendor_name                         │
                       │ reference_number                    │
                       │ uploaded_by (FK → User)             │
                       │ uploaded_at                         │
                       └─────────────────────────────────────┘
```

### Annual Report Models

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│     Vendor      │  │       EPC       │  │   Exhibition    │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ id              │  │ id              │  │ id              │
│ name            │  │ name            │  │ name            │
│ vendor_type     │  │ region          │  │ year            │
│ contact_person  │  │ contact_person  │  │ location        │
│ email           │  │ email           │  │ leads_generated │
│ phone           │  │ specialization  │  │ is_attended     │
│ products        │  │ is_active       │  │ notes           │
│ is_active       │  └─────────────────┘  └─────────────────┘
└─────────────────┘

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ProcurementPortal│  │  Certification  │  │  SalesContact   │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ id              │  │ id              │  │ id              │
│ name            │  │ name            │  │ company_name    │
│ registration_   │  │ issuing_body    │  │ contact_name    │
│   type          │  │ status          │  │ category        │
│ registration_   │  │ issue_date      │  │ email           │
│   date          │  │ expiry_date     │  │ phone           │
│ expiry_date     │  │ certificate_no  │  │ is_contacted    │
│ is_active       │  └─────────────────┘  └─────────────────┘
└─────────────────┘
```

---

## Authentication & Authorization

### Role-Based Access Control (RBAC)

| Role | Permissions |
|------|-------------|
| **Administrator** | Full access to all features, user management, all regions |
| **Manager** | View/edit projects in own region, import/export, reports |
| **Sales Rep** | View/edit own projects only, basic export |

### Authentication Flow

```
User Login Request
        │
        ▼
┌─────────────────┐
│ CustomLoginView │
│ (Django Auth)   │
└─────────────────┘
        │
        ▼
┌─────────────────┐
│ Session Created │
│ (Server-side)   │
└─────────────────┘
        │
        ▼
┌─────────────────┐
│ Role Check via  │
│ Mixins/Decorators│
└─────────────────┘
        │
        ▼
┌─────────────────┐
│ Access Granted/ │
│ Denied          │
└─────────────────┘
```

---

## Key Features

### 1. Dashboard
- Regional pipeline summaries (LNUK, LNA, PA)
- Interactive charts (doughnut & bar)
- Real-time statistics by status
- Recent projects table

### 2. Project Management
- Full CRUD operations
- Advanced filtering (region, status, year, category)
- Consolidated regional view (UK + Global = LNUK)
- Status history tracking

### 3. Document Management
- File upload (PDF, Excel, Word, Images)
- Document types: Quotations, Proposals, Technical docs
- Project-linked and standalone documents
- Download and preview

### 4. Annual Report
- Vendor tracking
- EPC contractors
- Exhibitions attended
- Procurement portals
- Certifications

### 5. User Interface
- Responsive design (mobile-friendly)
- Collapsible sidebar
- Role-based menu visibility
- Toast notifications

---

## API Endpoints (URL Routes)

### Dashboard
| URL | View | Description |
|-----|------|-------------|
| `/` | `dashboard:index` | Main dashboard |

### Projects
| URL | View | Description |
|-----|------|-------------|
| `/projects/` | `projects:list` | List all projects |
| `/projects/create/` | `projects:create` | Create project |
| `/projects/<id>/` | `projects:detail` | Project details |
| `/projects/<id>/edit/` | `projects:edit` | Edit project |
| `/projects/<id>/delete/` | `projects:delete` | Delete project |

### Documents
| URL | View | Description |
|-----|------|-------------|
| `/projects/documents/` | `projects:document_list` | List documents |
| `/projects/documents/upload/` | `projects:document_create` | Upload document |
| `/projects/documents/<id>/` | `projects:document_detail` | Document details |
| `/projects/<id>/add-document/` | `projects:add_document` | Add doc to project |

### Reports
| URL | View | Description |
|-----|------|-------------|
| `/reports/` | `reports:index` | Reports home |
| `/reports/annual-report/` | `reports:annual_report` | Annual report |
| `/reports/export/` | `reports:export` | Export Excel |
| `/reports/import/` | `reports:import` | Import Excel |

### Accounts
| URL | View | Description |
|-----|------|-------------|
| `/accounts/login/` | `accounts:login` | User login |
| `/accounts/logout/` | `accounts:logout` | User logout |
| `/accounts/profile/` | `accounts:profile` | User profile |
| `/accounts/users/` | `accounts:user_list` | User management |

---

## Security Features

- CSRF protection on all forms
- Session-based authentication
- Password hashing (PBKDF2)
- Role-based access control
- Secure logout (POST method)
- XSS protection via Django templates

---

## Deployment Considerations

### Development
```bash
python manage.py runserver
```

### Production Checklist
- [ ] Set `DEBUG = False`
- [ ] Configure `ALLOWED_HOSTS`
- [ ] Use PostgreSQL database
- [ ] Set strong `SECRET_KEY`
- [ ] Configure static file serving (WhiteNoise/Nginx)
- [ ] Set up HTTPS/SSL
- [ ] Configure email backend
- [ ] Set up backup strategy

---

## Dependencies (requirements.txt)

```
Django>=5.0
psycopg2-binary
django-filter
django-crispy-forms
crispy-bootstrap5
openpyxl
pandas
python-dateutil
```

---

## Browser Support

- Google Chrome (latest)
- Mozilla Firefox (latest)
- Microsoft Edge (latest)
- Safari (latest)

---

*Document generated for Leap Networks ERP System*
*Version 1.0 | January 2026*
