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

### Protecting `/dfd`

`/dfd` downloads the repository archive (up to 100 MB) and parses it, and browsers call it directly, so it is guarded
(`app/services/guard.py`). All of this is in memory, per process.

| Variable | Default | Meaning |
|---|---|---|
| `DFD_CACHE_TTL_SECONDS` | `600` | A repository analysed in this time is answered from memory: instantly, and without spending GitHub's rate limit. `0` turns the cache off. |
| `DFD_CACHE_MAX_ENTRIES` | `32` | How many results are kept. |
| `DFD_RATE_LIMIT` | `10` | New analyses each client may start per window; the next gets `429` with `Retry-After`. Cached answers and bad addresses do not count. `0` turns it off. |
| `DFD_RATE_WINDOW_SECONDS` | `60` | The window. |
| `DFD_MAX_CONCURRENT` | `2` | Analyses running at the same time. |
| `DFD_QUEUE_TIMEOUT_SECONDS` | `10` | How long a request waits for a free slot before getting `503` with `Retry-After`. |
| `TRUST_FORWARDED_FOR` | `false` | Behind a reverse proxy every client arrives with the proxy's address, so set this to `true` to use `X-Forwarded-For` instead. Leave it `false` when the API is reachable directly: the header is then ignored, because a client could set it to anything. |

There is still no authentication: every client is anonymous. When the platform's login exists, `/dfd` should require it.
