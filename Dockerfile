# Dockerfile for Render deployment with LibreOffice (for DOCX → PDF conversion
# required by the PQD export flow). If you deploy without Docker, PQD exports
# will fall back to a reportlab-drawn body with the attachments merged.

FROM python:3.11-slim

# System deps: libreoffice headless for DOCX/PPTX → PDF, plus fonts
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-core \
        libreoffice-writer \
        libreoffice-impress \
        libreoffice-calc \
        libreoffice-common \
        fonts-dejavu \
        fonts-liberation \
        fonts-freefont-ttf \
        libpq-dev \
        gcc \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x build.sh || true

ENV PYTHONUNBUFFERED=1 \
    PORT=10000

EXPOSE 10000

CMD ["sh", "-c", "./build.sh && gunicorn erp_leap.wsgi:application --bind 0.0.0.0:$PORT"]
