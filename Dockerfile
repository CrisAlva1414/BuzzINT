FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright and PostgreSQL
RUN apt-get update && apt-get install -y \
    postgresql-client \
    libglib2.0-0 \
    libpangocairo-1.0 \
    libpango-1.0 \
    libatk1.0-0 \
    libcairo-gobject2 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxrender1 \
    libnss3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY scraper/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers with system dependencies
RUN playwright install --with-deps chromium

# Copy application
COPY . .

# Create data directory
RUN mkdir -p data

EXPOSE 8000

CMD ["uvicorn", "scraper.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
