# celery-hub

> **Role in the zoo:** project `celery-hub` of [oxzoo-live](https://github.com/saurav-codes/oxzoo-live-control/blob/main/zoo/README.md#projects), deployed with ox on server s1 at https://celery-hub.s1.zoo.sorv.dev. The contract it follows is [DESIGN.md](https://github.com/saurav-codes/oxzoo-live-control/blob/main/zoo/DESIGN.md).

Django 5 + gunicorn with a Celery worker and Celery beat as ox workers, two
Postgres databases and two Redis instances. It starts the `event-river`
chain.

## What it proves

- ox runs a Django app (start, install detected) plus two `[workers]` with
  memory caps sized for a 4 GB host (worker concurrency 2).
- Four services: the shared Postgres (`DATABASE_URL`), a private second
  Postgres `ledger` (`LEDGER_DATABASE_URL`), a private Redis used as the
  Celery broker and result backend (`REDIS_URL`), and a shared Redis
  `cache` (`CACHE_REDIS_URL`).
- An ox `${...}` reference: the broker reads `CELERY_BROKER_URL`, which the
  owner sets to `${REDIS_URL}`. The probe lists it as a `reference` and
  `REDIS_URL` as a `service`; both show the same fp.
- Django in production: `DJANGO_SECRET_KEY` is required under ox (settings
  refuse to load without it, so the build fails), `DEBUG` is off,
  `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` come from `PUBLIC_HOST` and
  `PUBLIC_URL`, migrations run on both databases in the migrate step, static
  files are collected at build and served by WhiteNoise.
- A small Flower-style page at `/` (JSON at `/api/status`): workers that
  answer a broadcast ping (cached 10 s in the shared cache), the last 25 task
  runs (recorded by task signals), beat's last tick (beat runs a task every
  minute that records one) and the ledger row count.

## Zoo endpoints

- `GET /_zoo/health`, `GET /_zoo/probe`, `POST /_zoo/chain/event-river`,
  `GET /_zoo/trace/<id>`, with CORS for `ZOO_PANEL_ORIGIN`.
- Probe checks: default Postgres and ledger Postgres row round trips, broker
  Redis and shared cache SET/GET/DEL, a Celery `ping` task whose result comes
  back through the Redis result backend within 5 s, beat's last tick under
  150 s old, and a signed `GET ${EVENTS_URL}/_zoo/verify` to event-bus with
  `EVENTS_KEY`.
- event-river: the chain endpoint records `queued` and enqueues a task; the
  worker writes a ledger row (`worker-ledger`), then POSTs signed with
  `EVENTS_KEY` to `${EVENTS_URL}/api/events`
  `{"trace","kind":"zoo.chain","text"}` and records `sent`. A failed send is
  retried twice, then recorded as `send-failed`. 10 starts per minute per
  process, then 429.

## Variables

| Variable | From | Role in the probe |
| --- | --- | --- |
| `DATABASE_URL` | ox, shared postgres | service |
| `LEDGER_DATABASE_URL` | ox, private postgres `ledger` | service |
| `REDIS_URL` | ox, private redis | service |
| `CACHE_REDIS_URL` | ox, shared redis `cache` | service |
| `PORT`, `PUBLIC_HOST`, `PUBLIC_URL`, `OX_ENV`, `OX_RELEASE`, `OX_DATA_DIR` | ox | |
| `DJANGO_SECRET_KEY` | yours, secret | not listed |
| `EVENTS_KEY` | yours, secret shared with event-bus | signs |
| `CELERY_BROKER_URL` | yours, `${REDIS_URL}` | reference |
| `EVENTS_URL` | yours, `https://event-bus.s3.zoo.sorv.dev` | url |
| `ZOO_PANEL_ORIGIN` | yours, `https://zoo-control.s1.zoo.sorv.dev` | plain |

## Run locally

```sh
uv sync
export DATABASE_URL=postgres://user@127.0.0.1:5432/celery
export LEDGER_DATABASE_URL=postgres://user@127.0.0.1:5432/celery_ledger
export REDIS_URL=redis://127.0.0.1:6379/2 CELERY_BROKER_URL=redis://127.0.0.1:6379/2
export CACHE_REDIS_URL=redis://127.0.0.1:6379/3 OX_DATA_DIR=/tmp/celery-hub
uv run python manage.py migrate && uv run python manage.py migrate --database ledger
uv run gunicorn hub.wsgi --bind 127.0.0.1:8000
.venv/bin/celery -A hub worker --concurrency 2   # on macOS add FORKED_BY_MULTIPROCESSING=1
.venv/bin/celery -A hub beat --schedule /tmp/celery-hub/celerybeat-schedule
```

On macOS, billiard starts pool children with spawn and tasks then fail with
`not enough values to unpack`; `FORKED_BY_MULTIPROCESSING=1` fixes that
locally. Linux forks, so ox needs nothing.

## Tests

```sh
uv run pytest                     # unit tests, no services
DATABASE_URL=... LEDGER_DATABASE_URL=... uv run pytest   # plus the task test (pytest-django makes test_ databases)
```

Recorded: unit only 32 passed, 1 skipped; with local Postgres 18, 33 passed.
Also run by hand against local Postgres 18 and Redis 8 with gunicorn, a real
worker and beat, and a local stand-in for event-bus that verifies zoo-sig:
probe ok on all 7 checks, event-river hops `queued`, `worker-ledger`, `sent`,
status page and hashed static file 200, preflight 204. Measured memory: worker
tree about 235 MB (macOS, spawn), beat about 85 MB, under the 384M and 160M
caps.

## ox check

```
ox check . (manifest: ox.toml)

  app.start                  uv run gunicorn hub.wsgi --bind 127.0.0.1:$PORT      detected:manage.py
  app.health                 /_zoo/health                                         declared
  build.install              uv sync --frozen --no-dev                            detected:uv.lock
  build.commands[0]          uv run python manage.py collectstatic --noinput      declared
  build.commands[1]          uv run python -c 'import zoo; zoo.write_build_info("build-info.json")' declared
  build.migrate              uv run python manage.py migrate --noinput && uv run python manage.py migrate --noinput --database ledger declared
  workers.beat               exec .venv/bin/celery -A hub beat --loglevel INFO --schedule "$OX_DATA_DIR/celerybeat-schedule" declared
  workers.worker             exec .venv/bin/celery -A hub worker --concurrency 2 --loglevel INFO --without-mingle declared
  tools.python               3.13                                                 detected:.python-version
  tools.uv                   0.11                                                 default
  services.cache             redis 8 (shared)                                     declared
  services.ledger            postgres 18 (only for this project)                  declared
  services.postgres          postgres 18 (shared)                                 default
  services.redis             redis 8 (only for this project)                      default

  Provided by ox: PORT, HOST, OX_ENV, OX_PROJECT, OX_RELEASE, OX_DATA_DIR, PUBLIC_URL, PUBLIC_HOST, CACHE_REDIS_URL, LEDGER_DATABASE_URL, DATABASE_URL, REDIS_URL
  Set on the dashboard before the first deploy: DJANGO_SECRET_KEY, EVENTS_KEY, CELERY_BROKER_URL, EVENTS_URL, ZOO_PANEL_ORIGIN

Ready to deploy.
```

Not verified here: the real event-bus (a local stand-in was used), ox's
sandbox and cgroup memory accounting for the worker under Linux fork, and the
shared Redis index ox hands out.
