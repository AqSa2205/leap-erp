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
- Calculation formulas:
  - Discount Amount = Base Cost × Discount %
  - Unit Cost = Base Cost - Discount Amount
  - Selling Price = Cost ÷ (1 - Margin %)
  - Final Price = Selling Price + Addon Costs
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
