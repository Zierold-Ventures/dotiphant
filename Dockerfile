# Runs the Dotiphant MCP server over stdio with an empty archive and no Telegram login.
# Meant for MCP directory checks (Glama) that start the server and list its tools.
# Real use is a local install: `uv tool install` and `dotiphant setup`, see README.md.
#
#   docker build -t dotiphant-mcp .
#   docker run -i --rm dotiphant-mcp

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.16 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_DEFAULT_INDEX=https://pypi.org/simple \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# No system keyring in a container: DOTIPHANT_SECRETS=file keeps secrets in files under
# DOTIPHANT_HOME instead. The archive starts empty; tools answer "archive never synced".
RUN useradd --create-home --uid 10001 dotiphant && mkdir /data && chown dotiphant /data
USER dotiphant
ENV DOTIPHANT_HOME=/data \
    DOTIPHANT_SECRETS=file \
    PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["dotiphant", "mcp"]
