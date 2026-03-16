# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

Google Workspace MCP server — provides Gmail, Calendar, and Drive tools via the Model Context Protocol.

Forked from [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp). This fork runs as a Docker container in single-user mode with OAuth 2.0 credentials.

## Development Commands

```bash
# Install dependencies
uv sync

# Run the server locally
uv run main.py --transport streamable-http --tools gmail calendar drive

# Run tests
uv run pytest -v

# Linting
uv run ruff check .
uv run ruff format --check .
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` | Yes | Google OAuth 2.0 client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Google OAuth 2.0 client secret |
| `USER_GOOGLE_EMAIL` | Yes (single-user mode) | Email for the authenticated user |
| `MCP_SINGLE_USER_MODE` | No | Set to `1` for single-user operation |
| `WORKSPACE_MCP_CREDENTIALS_DIR` | No | Path to store OAuth credentials (default: `./store_creds`) |

## Docker

```bash
docker build -t google-mcp .
docker run -p 8000:8000 \
  -e GOOGLE_OAUTH_CLIENT_ID=... \
  -e GOOGLE_OAUTH_CLIENT_SECRET=... \
  -e USER_GOOGLE_EMAIL=... \
  -e MCP_SINGLE_USER_MODE=1 \
  -v google-mcp-creds:/app/store_creds \
  google-mcp
```

## Key Notes

- OAuth must be triggered through the running MCP server (proper MCP protocol session), not standalone CLI — the CLI doesn't share state with the server process.
- Port 8000 is the default. Override with the `PORT` environment variable.
- See upstream [README](https://github.com/taylorwilsdon/google_workspace_mcp) for full documentation.
