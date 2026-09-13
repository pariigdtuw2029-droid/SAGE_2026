FROM python:3.13-slim

WORKDIR /app/backend

# Install backend dependencies first (better layer caching)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend package + frontend static folder (sibling of backend/, as expected
# by the auto-detection in app/main.py)
COPY backend/ ./backend/
COPY Frontend/ ./Frontend/

ENV PYTHONPATH=/app/backend/backend
EXPOSE 8000

WORKDIR /app/backend/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
