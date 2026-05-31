FROM python:3.11-slim

# Install system dependencies required by psycopg and pdfplumber
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser \
    && mkdir -p /tmp/researchforge_uploads /tmp/researchforge_pdfs \
    && chown -R appuser:appuser /app /tmp/researchforge_uploads \
       /tmp/researchforge_pdfs

USER appuser

EXPOSE 8000

# Railway injects $PORT. Fall back to 8000 for local Docker runs.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
