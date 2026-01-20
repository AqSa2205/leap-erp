# Leap Networks ERP - Deployment Guide

## Render Deployment (Production)

### Prerequisites
- GitHub account
- Render account (https://render.com)

### Step 1: Push to GitHub

1. Create a new repository on GitHub:
   - Go to https://github.com/new
   - Repository name: `leap-erp` (or your preferred name)
   - Set to **Private** (recommended for business applications)
   - Do NOT initialize with README

2. Connect local repository to GitHub:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/leap-erp.git
   git push -u origin main
   ```

### Step 2: Deploy on Render

1. Go to https://dashboard.render.com
2. Click **New +** > **Blueprint**
3. Connect your GitHub account if not already connected
4. Select your `leap-erp` repository
5. Render will automatically detect the `render.yaml` file
6. Click **Apply** to create the services

Render will automatically:
- Create a PostgreSQL database (`leap-erp-db`)
- Create a web service (`leap-erp`)
- Set up environment variables
- Run the build script (`build.sh`)
- Start the application with Gunicorn

### Step 3: Access Your Application

After deployment completes (5-10 minutes):
- Your app URL: `https://leap-erp.onrender.com`
- Default admin login: `admin` / `LeapAdmin@2026`

**Important:** Change the admin password immediately after first login!

---

## Development Workflow

### Branch Structure
- `main` - Production branch (deployed to Render)
- `dev` - Development branch (for testing changes)

### Making Changes

1. Switch to dev branch:
   ```bash
   git checkout dev
   ```

2. Make your changes and commit:
   ```bash
   git add .
   git commit -m "Description of changes"
   ```

3. Test locally:
   ```bash
   python manage.py runserver
   ```

4. When ready for production, merge to main:
   ```bash
   git checkout main
   git merge dev
   git push origin main
   ```

Render will automatically redeploy when you push to `main`.

---

## Environment Variables

### Production (set automatically by Render)
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Django secret key (auto-generated) |
| `DJANGO_ENV` | Set to `production` |
| `DEBUG` | Set to `False` |

### Local Development
Create a `.env` file (optional):
```
DJANGO_ENV=development
DEBUG=True
SECRET_KEY=your-local-secret-key
```

---

## Database Management

### Access Production Database
From Render dashboard:
1. Go to your database service
2. Click **Connect** > **External Connection**
3. Use the connection string with a PostgreSQL client

### Run Migrations
Migrations run automatically during deployment via `build.sh`.

To run manually:
```bash
# On Render Shell
python manage.py migrate
```

---

## Troubleshooting

### Build Fails
- Check Render logs for error messages
- Ensure `requirements.txt` has all dependencies
- Verify `build.sh` has correct line endings (LF, not CRLF)

### Static Files Not Loading
- Run `python manage.py collectstatic`
- Ensure WhiteNoise is in MIDDLEWARE

### Database Connection Issues
- Verify `DATABASE_URL` is set in environment
- Check PostgreSQL service is running on Render

---

## Scaling for 50+ Users

The free tier supports light usage. For 50 users:

### Recommended Upgrades
1. **Web Service:** Upgrade to Starter ($7/month) or Standard ($25/month)
2. **Database:** Upgrade to Starter ($7/month) for better performance

### Performance Tips
- Enable database connection pooling (`conn_max_age=600` already set)
- Use CDN for static files (optional)
- Enable Render's auto-scaling for peak usage

---

## Security Checklist

- [ ] Change default admin password
- [ ] Verify `DEBUG=False` in production
- [ ] Check HTTPS is enforced
- [ ] Review user permissions
- [ ] Set up regular database backups (Render Pro feature)
