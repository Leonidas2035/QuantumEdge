# Build Stage
FROM python:3.11-slim-bullseye as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements/toml
COPY pyproject.toml .

# Generate requirements.txt from pyproject.toml (simple extraction for now or use pip directly)
# Assuming typical pip install . works
RUN pip install --upgrade pip

# Runtime Stage
FROM python:3.11-slim-bullseye

# Create non-root user
RUN groupadd -g 1000 quantum && \
    useradd -u 1000 -g quantum -m -s /bin/bash quantum

WORKDIR /app

# Install runtime libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder (if we did a venv) or just install here for simplicity in this step.
# For robustness in a single file without complex venv copying:
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source code
COPY src /app/src
COPY config /app/config
# Put scripts if needed
COPY scripts /app/scripts

# Set basic environment
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Change ownership
RUN chown -R quantum:quantum /app

USER quantum

# Default command (can be overridden in compose)
CMD ["python", "/home/korben/.hermes/hermes/service.py"]

