import json
import os
import secrets
import time
import uuid

import redis
from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import zoo
from hub.celery import app as celery_app
from ledger.models import Entry
from ledger.models import ZooProbe as LedgerProbe

from .models import BeatTick, Hop, TaskRun, ZooProbe
from .tasks import event_river, ping, record_hop

NAME = "celery-hub"
STACK = "Django 5 + gunicorn + Celery"
BEAT_MAX_AGE_S = 150
chain_limit = zoo.RateLimit(10, 60)


def redis_client(var: str) -> redis.Redis:
    return redis.Redis.from_url(os.environ[var], socket_timeout=3, socket_connect_timeout=3)


def workers() -> list[dict]:
    """Live workers from a broadcast ping, cached 10 s in the shared cache."""
    cache, key = redis_client("CACHE_REDIS_URL"), "celery-hub:workers"
    if cached := cache.get(key):
        return json.loads(cached)
    found = [{"name": name, "reply": next(iter(reply.values()), "")}
             for r in celery_app.control.ping(timeout=1.0) for name, reply in r.items()]
    cache.set(key, json.dumps(found), ex=10)
    return found


def section(fn):
    try:
        return {"ok": True, "data": fn()}
    except Exception as e:  # shown on the page, never a 500
        return {"ok": False, "error": zoo.scrub(f"{type(e).__name__}: {e}", ["CELERY_BROKER_URL", "CACHE_REDIS_URL", "DATABASE_URL"])}


def status() -> dict:
    now = timezone.now()

    def runs():
        return [{"name": r.name.rsplit(".", 1)[-1], "state": r.state, "worker": r.worker, "detail": r.detail,
                 "started_at": zoo.iso(r.started_at.timestamp()),
                 "ms": int((r.finished_at - r.started_at).total_seconds() * 1000) if r.finished_at else None}
                for r in TaskRun.objects.order_by("-id")[:25]]

    def beat():
        last = BeatTick.objects.order_by("-at").first()
        return {"last_tick": zoo.iso(last.at.timestamp()), "age_s": int((now - last.at).total_seconds()),
                "ticks_24h": BeatTick.objects.count()} if last else None

    return {"workers": section(workers), "runs": section(runs), "beat": section(beat),
            "ledger": section(lambda: Entry.objects.count())}


@require_GET
def status_page(request):
    return render(request, "river/status.html", status() | {"name": NAME})


@require_GET
def status_json(request):
    return JsonResponse(status())


@require_GET
def health(request):
    return JsonResponse(zoo.health(NAME, STACK, str(settings.BASE_DIR / "build-info.json")))


@csrf_exempt
@require_POST
def chain(request, chain):
    if chain != "event-river":
        return JsonResponse({"error": "unknown chain"}, status=404)
    try:
        trace = json.loads(request.body).get("trace")
    except (ValueError, AttributeError):
        return JsonResponse({"error": "body must be JSON {\"trace\": \"<uuid>\"}"}, status=400)
    if not zoo.valid_trace(trace):
        return JsonResponse({"error": "bad trace id"}, status=400)
    if not chain_limit.allow():
        return JsonResponse({"error": "too many chain starts"}, status=429)
    # The hop goes first so it sorts before the worker's hops.
    task_id = str(uuid.uuid4())
    record_hop(trace, "queued", f"Celery task {task_id} on the CELERY_BROKER_URL redis")
    try:
        event_river.apply_async(args=[trace], task_id=task_id, retry=False)
    except Exception as e:
        record_hop(trace, "queue-failed", type(e).__name__)
        return JsonResponse({"error": "broker unavailable"}, status=503)
    return JsonResponse({"trace": trace, "started": True}, status=202)


@require_GET
def trace(request, trace):
    if not zoo.valid_trace(trace):
        return JsonResponse({"error": "bad trace id"}, status=400)
    hops = [zoo.hop(h.at, h.step, h.detail) for h in Hop.objects.filter(trace=trace).order_by("at", "id")]
    return JsonResponse(zoo.trace_body(trace, hops))


# ---- probe ---------------------------------------------------------------


def db_round_trip(model, alias: str) -> str:
    try:
        token = secrets.token_hex(8)
        row = model.objects.using(alias).create(token=token)
        got = model.objects.using(alias).get(pk=row.pk).token
        model.objects.using(alias).filter(pk=row.pk).delete()
        with connections[alias].cursor() as c:
            c.execute("SHOW server_version")
            version = c.fetchone()[0].split()[0]
        if got != token:
            raise RuntimeError("read back a different token")
        return f"zoo_probe row round trip in the {alias} database, server {version}"
    finally:
        connections.close_all()  # probe threads are short-lived


def redis_round_trip(var: str, what: str) -> str:
    r, key, token = redis_client(var), f"celery-hub:probe:{secrets.token_hex(6)}", secrets.token_hex(8)
    r.set(key, token, ex=30)
    got = r.get(key)
    r.delete(key)
    if got != token.encode():
        raise RuntimeError(f"read back {got!r}")
    return f"{what}: SET/GET/DEL, redis {r.info('server').get('redis_version')}"


def celery_round_trip() -> str:
    start = time.monotonic()
    result = ping.apply_async(retry=False)
    try:
        reply = result.get(timeout=4.5)
    finally:
        result.forget()
    return f"ping task answered by {reply['pong']} in {int((time.monotonic() - start) * 1000)} ms, result via the redis backend"


def beat_age() -> str:
    try:
        last = BeatTick.objects.order_by("-at").first()
    finally:
        connections.close_all()
    if last is None:
        raise RuntimeError("no beat tick recorded yet; is the beat worker running?")
    age = int((timezone.now() - last.at).total_seconds())
    if age > BEAT_MAX_AGE_S:
        raise RuntimeError(f"last beat tick {age}s ago (limit {BEAT_MAX_AGE_S}s); is the beat worker running?")
    return f"last beat tick {age}s ago"


prober = zoo.Prober(
    NAME, STACK,
    lambda: [
        zoo.Check("postgres", "Postgres write, read, delete", lambda: db_round_trip(ZooProbe, "default"), ["DATABASE_URL"]),
        zoo.Check("ledger", "Private ledger database write, read, delete",
                  lambda: db_round_trip(LedgerProbe, "ledger"), ["LEDGER_DATABASE_URL"]),
        zoo.Check("redis", "Broker redis SET/GET/DEL", lambda: redis_round_trip("CELERY_BROKER_URL", "private broker"),
                  ["CELERY_BROKER_URL"]),
        zoo.Check("cache", "Shared cache redis SET/GET/DEL", lambda: redis_round_trip("CACHE_REDIS_URL", "shared cache"),
                  ["CACHE_REDIS_URL"]),
        zoo.Check("celery", "Celery ping task, result in 5 s", celery_round_trip, ["CELERY_BROKER_URL"]),
        zoo.Check("beat", "Beat ticked in the last 150 s", beat_age, ["DATABASE_URL"]),
        zoo.peer_check(NAME, "event-bus", "EVENTS_URL", "EVENTS_KEY"),
    ],
    lambda: [
        zoo.var("CELERY_BROKER_URL", "reference", "REDIS_URL"),
        zoo.var("REDIS_URL", "service"),
        zoo.var("DATABASE_URL", "service"),
        zoo.var("LEDGER_DATABASE_URL", "service"),
        zoo.var("CACHE_REDIS_URL", "service"),
        zoo.var("EVENTS_KEY", "signs"),
        zoo.var("EVENTS_URL", "url", "event-bus"),
        zoo.var("ZOO_PANEL_ORIGIN", "plain"),
    ],
)


@require_GET
def probe(request):
    status_code, body = prober.run()
    return JsonResponse(body, status=status_code)
