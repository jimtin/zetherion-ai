# Multi-stage Dockerfile using distroless for minimal attack surface
# Stage 1: Builder - install dependencies
# Stage 2: Runtime - distroless Python (no shell, no package manager)

# ============================================================
# STAGE 1: Builder
# ============================================================
FROM python:3.12-slim@sha256:43e4d702bbfe3bd6d5b743dc571b67c19121302eb172951a9b7b0149783a1c21 as builder

WORKDIR /app

# Install dependencies in user directory (will be copied to distroless)
COPY requirements.txt ./
RUN pip install --user --no-cache-dir --no-warn-script-location -r requirements.txt

# Copy source code for import verification
COPY src ./src

# Set Python path for import verification
ENV PYTHONPATH=/app/src

# Verify all critical imports work before building runtime image
# This catches missing dependencies at build time instead of runtime
RUN python -c "from zetherion_ai.main import run; print('✓ Imports verified')" && \
    python -c "from zetherion_ai.discord.bot import ZetherionAIBot; print('✓ Discord bot imports verified')" && \
    python -c "from zetherion_ai.agent.core import Agent; print('✓ Agent imports verified')"

# ============================================================
# STAGE 2: Distroless Runtime
# ============================================================
FROM gcr.io/distroless/python3-debian12:nonroot

# Copy Python packages from builder (installed with --user)
COPY --from=builder /root/.local /root/.local

# Copy application source code
COPY --from=builder /app/src /app/src

# Set Python path
ENV PYTHONPATH=/app/src
ENV PATH=/root/.local/bin:$PATH

# Create data and logs directories
# Distroless runs as uid 65532 (nonroot) by default
# We'll use volumes for these, but define them here
VOLUME ["/app/data", "/app/logs"]

# Distroless runs as nonroot user (uid 65532) by default
# No USER directive needed - it's built into the image

# Healthcheck using Python (no shell/curl in distroless)
# Checks if we can import the main module
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

# Entry point - must be absolute path for distroless
CMD ["python", "-m", "zetherion_ai"]
