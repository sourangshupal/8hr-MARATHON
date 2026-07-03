FROM python:3.11-slim-bookworm

# Patch OS-level CVEs, then install system deps required by torch and native packages
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so pip install is a cached layer.
# Re-runs only when requirements.txt changes, not on every code change.
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements-prod.txt

# Copy only the app package — everything else (evals/, DATA/, DOCS/) stays out
COPY app/ ./app/

# Copy the Streamlit UI so the same image can run the rag-ui ECS service.
COPY ui/ ./ui/

# Expose the port documented in the task definitions and health checks.
EXPOSE 8080

# Run as a non-root user for production hardening.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"]
