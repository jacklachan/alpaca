FROM python:3.12-slim

WORKDIR /app

# The dashboard is deliberately credential-free. Its only data source is the
# frozen public journal copied into dashboard/evidence at release time.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY glassbox/ ./glassbox/
COPY dashboard/ ./dashboard/

ENV PYTHONUNBUFFERED=1
ENV GLASSBOX_JOURNAL_PATH=/app/dashboard/evidence/journal.jsonl

CMD ["sh", "-c", "uvicorn dashboard.app:app --host 0.0.0.0 --port ${PORT:-10000}"]
