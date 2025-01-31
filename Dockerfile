# Use UV's Python image as base
# https://docs.astral.sh/uv/guides/integration/docker/#available-images
# TODO: maybe we can use a smaller image
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

LABEL org.opencontainers.image.source=https://github.com/opendatateam/udata-hydra
LABEL org.opencontainers.image.description="udata-hydra service"
LABEL org.opencontainers.image.licenses=MIT

# Install system dependencies
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    cmake \
    libgeos-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files needed for installation
COPY pyproject.toml README.md ./
COPY udata_hydra ./udata_hydra

# Create venv and install dependencies
RUN uv venv && uv pip install ".[dev]"

# The rest of the application code will be mounted in development
# or copied in production/CI
