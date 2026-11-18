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
| `GET /dfd/stream?url=&group_by=` | the same analysis as NDJSON, one JSON line per event, so a page can show real progress (see below) |

## Settings

Read from the environment or from a `.env` file.

| Variable | Default | Meaning |
|---|---|---|
| `GITHUB_TOKEN` | none | Raises GitHub's rate limit from 60 to 5,000 requests per hour. |
| `ACTIVITY_CACHE_TTL_SECONDS` | `600` | How long `/github/activity` answers are cached in memory. |
| `DFD_FETCH_METHOD` | `auto` | `auto` reads the code file by file (see "How the code is read"), falling back to the whole archive if that fails; `archive` always downloads the archive. |
| `DFD_FETCH_CONCURRENCY` | `48` | Requests to `raw.githubusercontent.com` at the same time (only without a token). |
| `GITHUB_RAW_URL` | `https://raw.githubusercontent.com` | Where raw files come from. |
| `CORS_ORIGINS` | `http://localhost:3000` | Origins (comma-separated, or `*`) whose pages may call this API from a browser. Read-only: GET, no cookies. |

### How the code is read

GitHub's archive (`/tarball`) is generated on the fly and downloads everything: 13 to 17 s for FastAPI (18 MB), and it never finishes for
something like the Linux kernel (250 MB, and GitHub answers 429). So `/dfd` lists the files with one Git Trees request (a very big
repository is listed folder by folder) and reads only the source files:

1. **With a `GITHUB_TOKEN`:** about 100 files per GraphQL request, asked for by blob sha (0.6 s per request; asking by path takes 2 to 5 s).
   Up to 2,500 files.
2. **Without a token:** one request per file to `raw.githubusercontent.com`, 48 at a time, up to 1,200 files (it has its own rate limit).
3. **If that fails** (the network, or most files cannot be read): the whole archive, as before. A repository that does not exist or a
   GitHub rate limit is an error and is not retried another way.

A repository with more source files than the limit is **sampled**: folders closest to the root first, one file per folder in turn so
that no huge folder (`drivers/` in Linux) takes everyone's place, and product code before tests, docs, examples and scripts. The result
says so (`meta.stats`: `files_found`, `files_parsed`, `sampled`, `listing_truncated`, `fetch_method`). Compiled files such as `.mo` are
never picked. Parsing a repository of 400 files or more is split over up to 4 processes (threads do not help: the GIL).

Measured (live): FastAPI 6.7 s (was 16 to 20 s); Django 11 s; Linux 33 to 40 s for a sample of 2,500 of 75,317 files (was a failure).

### Progress: `GET /dfd/stream`

Same parameters, guards and cache as `GET /dfd`; the answer is `application/x-ndjson`:

```
{"type":"progress","stage":"listing|downloading|parsing|building","done":120,"total":1046}   (total 0: not known)
{"type":"result","graph":{...}}                       the same graph as GET /dfd
{"type":"error","status":404,"detail":"..."}          the analysis failed (after it started)
```

A bad address, the per-client limit (429) and a busy server (503) are normal HTTP errors, before anything is streamed. A result that is
already cached is a single `result` line. If the client disconnects the work stops and the slot is given back.

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
## Base de dados e migrações (Alembic)

O schema da base de dados (PostgreSQL) está definido pelos modelos em `app/models/`. O Alembic guarda cada mudança ao schema como um ficheiro em `alembic/versions/`, para que toda a gente tenha a mesma base de dados.

No `docker-compose.yml` do repositório `RepoPrint`, o serviço `migrate` corre `alembic upgrade head` sempre que o stack sobe, antes do backend. Só aplica as migrações que faltam.

### Subir e repor a base de dados

A partir da pasta `RepoPrint/`, com `POSTGRES_PASSWORD` definida no `.env` (ver `.env.example`):

```
docker compose up --build      # sobe db, migrate, backend e frontend
docker compose down            # pára, os dados ficam no volume
docker compose down -v         # pára e apaga os dados; no próximo up as migrações recriam tudo
docker compose up db           # só o Postgres, para usar o Alembic a partir do teu PC
```

### Confirmar que a base de dados está ativa

Depois do `docker compose up`, a partir de `RepoPrint/`:

| Comando | O que deves ver |
| --- | --- |
| `docker compose ps -a` | `db` e `backend` em `healthy`, e `migrate` em `Exited (0)` (terminou com sucesso) |
| `docker compose logs migrate` | `Running upgrade ... initial schema` na primeira vez; nas seguintes, nenhuma linha `Running upgrade` (nada a aplicar) |
| `curl localhost:8000/health/db` | `{"status":"ok"}` (`503` se o backend não chega à base de dados) |
| `docker compose exec db pg_isready -U repoprint -d repoprint` | `accepting connections` |
| `docker compose exec db psql -U repoprint -d repoprint -c '\dt'` | 14 linhas: as 13 tabelas e a `alembic_version` |
| `docker compose exec db psql -U repoprint -d repoprint -c 'select * from alembic_version'` | o id da última migração (o mesmo de `alembic heads`) |

Se mudaste `POSTGRES_USER` ou `POSTGRES_DB` no `.env`, usa os teus valores nos comandos `-U` e `-d`. Também podes ligar um cliente gráfico a `localhost:5433` (a `POSTGRES_PORT`).

Se o `migrate` ficar em `Exited (1)`, o backend não arranca. Vê o erro com `docker compose logs migrate`.

### Usar o Alembic no teu PC

1. Põe a ligação em `backend/.env` (o ficheiro está no `.gitignore`):

```
DB_HOST=localhost
DB_PORT=5433
DB_NAME=repoprint
DB_USER=repoprint
DB_PASSWORD=<a mesma POSTGRES_PASSWORD>
```

A porta é a `POSTGRES_PORT` do compose (5433 por defeito). Dentro do compose o backend usa `DB_HOST=db` e a porta 5432.

2. Corre os comandos a partir da pasta `backend/`, com o Postgres a correr:

| Comando | O que faz |
| --- | --- |
| `uv run alembic current` | mostra em que migração a base de dados está |
| `uv run alembic history` | lista as migrações, da mais recente para a mais antiga |
| `uv run alembic heads` | mostra a última migração (tem de haver só uma) |
| `uv run alembic upgrade head` | aplica as migrações que faltam |
| `uv run alembic downgrade -1` | desfaz a última migração |
| `uv run alembic check` | diz se os modelos têm mudanças sem migração |

### Alterar o schema

1. Muda ou cria o modelo em `app/models/`. Se criaste um ficheiro novo, importa as classes em `app/models/__init__.py`.
2. Gera a migração: `uv run alembic revision --autogenerate -m "o que mudou"`.
3. Abre o ficheiro gerado em `alembic/versions/` e confirma que faz o que queres. O Alembic não distingue renomear de apagar mais criar, e isso perde dados.
4. Aplica: `uv run alembic upgrade head`.
5. Corre os testes (ver abaixo).
6. Faz commit do modelo e da migração juntos.

Regras:

- Não edites uma migração que já esteja no `dev` ou que outra pessoa já aplicou. Cria uma nova.
- Não escrevas SQL em strings. Usa o ORM e as expressões do SQLAlchemy (`select`, `insert`, `delete`), que enviam os valores separados do SQL e evitam injeção de SQL.
- Não alteres a tabela `alembic_version` à mão.

### Duas branches com migrações

Se duas branches criarem uma migração a partir da mesma anterior, depois do merge há duas "heads" e o `alembic upgrade head` falha com `Multiple head revisions`. Resolve assim:

```
uv run alembic heads                      # confirma as duas heads
uv run alembic merge heads -m "merge"     # cria uma migração que as junta
```

Faz commit da migração nova. Há um teste (`test_there_is_a_single_migration_head`) que falha enquanto houver mais do que uma.

### Testes

Os testes da base de dados correm em Postgres a sério e **apagam todas as tabelas**, por isso só correm se `DB_NAME` acabar em `_test`. Sem isso ficam saltados.

```
docker compose exec db createdb -U repoprint repoprint_test    # uma vez, a partir de RepoPrint/
env DB_NAME=repoprint_test uv run pytest                        # a partir de backend/
```

### Problemas comuns

| Mensagem ou sintoma | Causa e solução |
| --- | --- |
| `password authentication failed` | O volume guarda a palavra-passe da primeira vez que foi criado. Mudar o `.env` depois não a altera. Usa a antiga ou corre `docker compose down -v` (apaga os dados). |
| `connection refused` | O Postgres não está a correr, ou estás a usar a porta errada. Do teu PC é a `POSTGRES_PORT` (5433), não a 5432. |
| `Multiple head revisions` | Ver "Duas branches com migrações". |
| `Can't locate revision identified by ...` | A base de dados tem uma migração que não existe na tua branch. Faz merge da branch que a criou, ou repõe a base de dados com `docker compose down -v`. |
| `alembic revision --autogenerate` gera uma migração vazia | O modelo novo não está importado em `app/models/__init__.py`, ou não mudaste nada. |
