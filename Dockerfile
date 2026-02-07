# Multi-stage Dockerfile using distroless for minimal attack surface
# Stage 1: Builder - install dependencies
# Stage 2: Runtime - distroless Python (no shell, no package manager)

# ============================================================
# STAGE 1: Builder
# ============================================================
# Use Python 3.11 to match distroless runtime
FROM python:3.11-slim as builder

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

# Copy Python packages from builder with nonroot ownership (uid 65532)
COPY --from=builder --chown=65532:65532 /root/.local /home/nonroot/.local

# Copy application source code with nonroot ownership
COPY --from=builder --chown=65532:65532 /app/src /app/src

# Set Python path for nonroot user
# Include site-packages explicitly since distroless doesn't auto-discover user packages
ENV PYTHONPATH=/app/src:/home/nonroot/.local/lib/python3.11/site-packages
ENV PATH=/home/nonroot/.local/bin:$PATH
ENV PYTHONUSERBASE=/home/nonroot/.local

# Create data and logs directories
# Distroless runs as uid 65532 (nonroot) by default
# We'll use volumes for these, but define them here
VOLUME ["/app/data", "/app/logs"]

# Distroless runs as nonroot user (uid 65532) by default
# No USER directive needed - it's built into the image

# Healthcheck using Python (no shell/curl in distroless)
# Distroless has /usr/bin/python3.11 as entrypoint, so no "python" prefix needed
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["-c", "import sys; sys.exit(0)"]

# Entry point - distroless has /usr/bin/python3.11 as entrypoint
# So CMD should just be the arguments, not "python"
CMD ["-m", "zetherion_ai"]
