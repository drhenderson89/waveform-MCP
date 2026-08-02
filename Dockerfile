# Container image for the waveform-mcp server.
#
# Exists so registries and sandboxes (e.g. Glama) can build and introspect this
# server rather than inferring an image. Introspection needs the server to
# start and answer tools/list; _collect_tools() builds the catalogue from
# module-level TOOLS lists and touches neither the DAW nor the desktop.
#
# What this image can and cannot do, stated plainly: it serves the full tool
# CATALOGUE, but the tools themselves drive Tracktion Waveform, a Windows
# desktop application. Calling them from a Linux container will not work. This
# image is for introspection and schema review, not for running the server in
# anger -- use a Windows host for that.
#
# Two things make headless introspection work:
#   * the Windows-only tool modules (win_input, waveform_workflows,
#     schema_capture) are behind a sys.platform == "win32" guard, so on Linux
#     the catalogue is simply smaller rather than failing to import.
#   * pyautogui is imported lazily INSIDE the functions that use it, never at
#     module load, so no X display is needed to start the server.

FROM python:3.12-slim

WORKDIR /app

# Build deps for the scientific wheels, dropped again in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && apt-get purge -y gcc || true

# Unbuffered: stdio IS the transport, so a buffered reply looks like a hang.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "waveform_mcp.server"]
