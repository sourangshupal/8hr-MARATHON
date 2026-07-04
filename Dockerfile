FROM python:3.11-slim-bookworm

# Pull uv binary from the official image — no pip install needed.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Patch OS-level CVEs, then install system deps required by torch and native packages.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer: install dependencies only (cached until pyproject.toml changes).
# tomllib (stdlib in 3.11+) extracts [project.dependencies] so we can
# install deps without copying source — preserves cache on code-only changes.
# uv prefers binary wheels by default (no --prefer-binary flag needed).
COPY pyproject.toml .
RUN python3 -c "import tomllib,subprocess; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.run(['uv','pip','install','--system','--no-cache']+deps,check=True)"

# Layer: copy source — only invalidates on code changes, not dep changes.
COPY app/ ./app/
COPY ui/ ./ui/

# Expose the port documented in the task definitions and health checks.
EXPOSE 8080

# Run as a non-root user for production hardening.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"]
