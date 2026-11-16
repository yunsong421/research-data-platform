FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=5000

# fonts-noto-cjk is required so the charts can render Chinese labels
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py models.py config.py init_admin.py wsgi.py visualizations.py local_tools.py backup_store.py restore_backup.py ./
COPY templates ./templates
COPY static ./static

# the SQLite database lives here; mount a volume over it to keep data
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data

EXPOSE 5000

CMD ["python", "wsgi.py"]
