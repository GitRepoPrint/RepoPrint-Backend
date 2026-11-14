# RepoPrint-Backend

FastAPI service that reads a public GitHub repository and returns what the frontend cannot compute in the browser.

```sh
uv sync
uv run uvicorn main:app --reload      # http://localhost:8000
uv run pytest
```

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /github/activity?url=` | commit, contributor and pull-request metrics |
| `GET /dfd?url=&group_by=directory\|file` | the code-level data flow diagram, as a graph (`docs/dfd-schema.md` in the root repository) |

## Settings

Read from the environment or from a `.env` file.

| Variable | Default | Meaning |
|---|---|---|
| `GITHUB_TOKEN` | none | Raises GitHub's rate limit from 60 to 5,000 requests per hour. |
| `ACTIVITY_CACHE_TTL_SECONDS` | `600` | How long `/github/activity` answers are cached in memory. |
| `CORS_ORIGINS` | `http://localhost:3000` | Origins (comma-separated, or `*`) whose pages may call this API from a browser. Read-only: GET, no cookies. |

`/dfd` downloads the repository archive (up to 100 MB) and parses it, so it is slow and expensive. It has no authentication
or rate limiting yet: put one in front of it before exposing it publicly.
