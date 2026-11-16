# RepoPrint-Backend

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
