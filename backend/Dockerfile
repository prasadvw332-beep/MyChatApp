# ---------------------------------------------------------------------------
# Backend image: FastAPI app served by uvicorn.
#
# Build:  docker build -t mychatapp-backend:1.0 ./backend
# Run:    docker run -p 8000:8000 --env-file backend/.env mychatapp-backend:1.0
#
# Each instruction below creates a "layer". Docker caches layers, so we copy
# requirements.txt and install dependencies BEFORE copying the source code:
# editing app/*.py then only rebuilds the last layers (seconds, not minutes).
# ---------------------------------------------------------------------------

# 1. Base image: a small Debian with Python 3.14 already installed.
FROM python:3.14-slim

# 2. Python settings that make sense inside containers:
#    - don't write .pyc files (the container filesystem is thrown away anyway)
#    - print logs immediately instead of buffering them (so `docker logs` / `kubectl logs` show them)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 3. All following commands run in /app inside the image.
WORKDIR /app

# 4. Install dependencies first (cached layer, see note at the top).
#    requirements.txt is exported from uv.lock (no dev deps like ragas).
COPY requirements.txt .
RUN pip install -r requirements.txt

# 5. Copy the application code and the database migrations.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY evals ./evals

# 6. Don't run as root: if someone breaks into the app, they get a powerless user.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# 7. Documentation only: tells readers (and tools) which port the app listens on.
EXPOSE 8000

# 8. Docker checks this; if it keeps failing the container is marked "unhealthy".
#    (Kubernetes ignores HEALTHCHECK and uses its own probes, see deploy/k8s/backend-deployment.yaml.)
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# 9. The command the container runs. Listen on 0.0.0.0, not localhost,
#    otherwise nothing outside the container can reach it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
