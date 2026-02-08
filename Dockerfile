# Multi-stage Dockerfile using Chainguard for minimal attack surface
# Stage 1: Builder - install dependencies (with pip, shell, build tools)
# Stage 2: Runtime - Chainguard distroless Python (no shell, no package manager)
#
# Security: Chainguard images have zero known CVEs vs 19+ in Debian-based distroless

# ============================================================
# STAGE 1: Builder
# ============================================================
FROM cgr.dev/chainguard/python:latest-dev AS builder

WORKDIR /app

# Install dependencies in user directory (will be copied to runtime)
# PYO3_USE_ABI3_FORWARD_COMPATIBILITY allows pydantic-core to build on Python 3.14
# using the stable ABI (PyO3 currently only officially supports up to 3.13)
COPY requirements.txt ./
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
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
# STAGE 2: Chainguard Distroless Runtime
# ============================================================
FROM cgr.dev/chainguard/python:latest

WORKDIR /app

# Copy Python packages from builder with nonroot ownership (uid 65532)
COPY --from=builder --chown=65532:65532 /home/nonroot/.local /home/nonroot/.local

# Copy application source code with nonroot ownership
COPY --from=builder --chown=65532:65532 /app/src /app/src

# Set Python path for nonroot user
# Chainguard uses Python 3.14, packages are in site-packages
ENV PYTHONPATH=/app/src:/home/nonroot/.local/lib/python3.14/site-packages
ENV PATH=/home/nonroot/.local/bin:$PATH
ENV PYTHONUSERBASE=/home/nonroot/.local

# Create data and logs directories
# Chainguard runs as uid 65532 (nonroot) by default
VOLUME ["/app/data", "/app/logs"]

# Chainguard runs as nonroot user (uid 65532) by default
# No USER directive needed - it's built into the image

# Healthcheck using Python (no shell/curl in distroless)
# Note: HEALTHCHECK CMD doesn't use ENTRYPOINT, so we must specify full path
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/usr/bin/python", "-c", "import sys; sys.exit(0)"]

# Entry point - run the application
# Note: ENTRYPOINT is /usr/bin/python, so we only need the module args
CMD ["-m", "zetherion_ai"]
