# Pin base image by digest for reproducible builds
# Dependabot will auto-update this via .github/dependabot.yml
FROM python:3.12-slim@sha256:43e4d702bbfe3bd6d5b743dc571b67c19121302eb172951a9b7b0149783a1c21

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt ./

# Install dependencies (as root, before switching user)
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user for security
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Copy source code
COPY src ./src

# Create data and logs directories with correct ownership
RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app/data /app/logs

# Set Python path
ENV PYTHONPATH=/app/src

# Switch to non-root user
USER appuser

# Run the bot
CMD ["python", "-m", "zetherion_ai"]
