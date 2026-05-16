# General Ledger app image.
# Single-stage build on python:3.12-slim. Non-root user. Production
# CMD runs uvicorn WITHOUT --reload — that's a dev-only flag.

FROM python:3.12-slim

# Avoid Python writing .pyc files and buffering stdout (so docker logs
# show output promptly).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user up front so all subsequent COPY+RUN steps can
# set ownership cleanly. The home dir is /app so cache files (if any)
# land inside the working dir rather than /root.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Install runtime deps first so the image layer cache survives app-code
# changes that don't touch requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app itself. Anything you DON'T want in the image must be
# listed in .dockerignore.
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY VERSION .

# Create the upload directory inside the image with the right ownership.
# At runtime the docker-compose `gl_uploads` named volume is mounted on
# top of this path; first-time mounts inherit the directory's perms so
# the app user can write into the volume.
RUN mkdir -p /app/uploads && chown -R app:app /app

USER app

EXPOSE 8000

# No --reload: production. Local dev outside Docker can still use --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
