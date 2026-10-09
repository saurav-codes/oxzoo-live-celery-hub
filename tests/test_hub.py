import json
import os
import subprocess
import sys
import uuid

import pytest
from django.test import Client

import zoo
from river import views

PANEL = "http://localhost:5173"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ZOO_PANEL_ORIGIN", PANEL)
    views.chain_limit.times.clear()
    return Client()


def settings_import(env):
    clean = {k: v for k, v in os.environ.items() if k not in ("DJANGO_SECRET_KEY", "OX_ENV")}
    return subprocess.run([sys.executable, "-c", "import django; from django.conf import settings; "
                           "django.setup(); print(settings.DEBUG)"],
                          env=clean | {"DJANGO_SETTINGS_MODULE": "hub.settings"} | env, capture_output=True, text=True)


def test_production_refuses_to_start_without_secret_key():
    r = settings_import({"OX_ENV": "production"})
    assert r.returncode != 0 and "DJANGO_SECRET_KEY is not set" in r.stderr
    r = settings_import({"OX_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50})
    assert r.returncode == 0 and r.stdout.strip() == "False"


def test_health_and_cors(client):
    r = client.get("/_zoo/health", headers={"Origin": PANEL})
    body = r.json()
    assert r.status_code == 200 and body["name"] == "celery-hub" and body["build"]["tag"] == "zoo-1"
    assert r["Access-Control-Allow-Origin"] == PANEL and r["Vary"] == "Origin"
    assert not client.get("/_zoo/health", headers={"Origin": "https://evil.example"}).has_header("Access-Control-Allow-Origin")
    pre = client.options("/_zoo/chain/event-river", headers={"Origin": PANEL, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert pre["Access-Control-Allow-Headers"] == "Content-Type" and pre["Access-Control-Max-Age"] == "600"


def test_chain_and_trace_validation(client):
    post = lambda chain, body: client.post(f"/_zoo/chain/{chain}", body, content_type="application/json")
    assert post("event-river", {"trace": "not-a-uuid"}).status_code == 400
    assert post("event-river", {"trace": str(uuid.uuid4()).upper()}).status_code == 400
    assert post("event-river", "[]").status_code == 400
    assert post("shop-order", {"trace": str(uuid.uuid4())}).status_code == 404
    assert client.get("/_zoo/chain/event-river").status_code == 405
    assert client.get("/_zoo/trace/../etc").status_code in (400, 404)
    assert client.get("/_zoo/trace/1234").status_code == 400
    big = json.dumps({"trace": str(uuid.uuid4()), "pad": "x" * 70000})
    assert post("event-river", big).status_code == 400  # over the 64 KB body cap


def test_chain_rate_limit(client, monkeypatch):
    monkeypatch.setattr(views, "record_hop", lambda *a: None)
    monkeypatch.setattr(views.event_river, "apply_async", lambda **kw: None)
    codes = [client.post("/_zoo/chain/event-river", {"trace": str(uuid.uuid4())}, content_type="application/json").status_code
             for _ in range(11)]
    assert codes == [202] * 10 + [429]


def test_probe_reports_missing_vars(client, monkeypatch):
    for name in ("DATABASE_URL", "LEDGER_DATABASE_URL", "CELERY_BROKER_URL", "CACHE_REDIS_URL", "EVENTS_KEY", "EVENTS_URL", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    body = client.get("/_zoo/probe").json()
    assert body["ok"] is False
    errors = {c["id"]: c["error"] for c in body["checks"]}
    assert errors["ledger"] == "LEDGER_DATABASE_URL is not set" and errors["celery"] == "CELERY_BROKER_URL is not set"
    assert errors["peer:event-bus"] == "EVENTS_URL is not set"
    assert {"name": "CELERY_BROKER_URL", "role": "reference", "peer": "REDIS_URL", "missing": True} in body["vars"]


def test_reference_and_its_service_key_share_one_fp(client, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:1/0")
    got = {v["name"]: v for v in client.get("/_zoo/probe").json()["vars"]}
    assert got["CELERY_BROKER_URL"]["role"] == "reference" and got["REDIS_URL"]["role"] == "service"
    assert got["CELERY_BROKER_URL"]["fp"] == got["REDIS_URL"]["fp"] == zoo.fp("redis://127.0.0.1:1/0")
    assert "value" not in got["REDIS_URL"]


needs_db = pytest.mark.skipif(not (os.environ.get("DATABASE_URL") and os.environ.get("LEDGER_DATABASE_URL")),
                              reason="set DATABASE_URL and LEDGER_DATABASE_URL (test databases are made from them)")


@needs_db
@pytest.mark.django_db(databases=["default", "ledger"])
def test_event_river_task_writes_ledger_and_sends_signed(monkeypatch, settings):
    from ledger.models import Entry
    from river.models import Hop
    from river.tasks import event_river

    sent = []

    def fake_request(method, url, key, caller, body=b"", trace=None, **kw):
        sent.append((method, url, caller, json.loads(body), trace, zoo.sign_header(key, caller, method, "/api/events", body)))
        return 201, b'{"id":1}'

    monkeypatch.setenv("EVENTS_URL", "https://event-bus.s3.zoo.sorv.dev")
    monkeypatch.setenv("EVENTS_KEY", "zoo-test-key-0123456789abcdef")
    monkeypatch.setattr(zoo, "signed_request", fake_request)
    trace = str(uuid.uuid4())
    event_river.apply(args=[trace]).get()
    event_river.apply(args=[trace]).get()  # a redelivery keeps one ledger row
    assert Entry.objects.filter(trace=trace).count() == 1
    method, url, caller, body, header_trace, _ = sent[0]
    assert (method, url, caller) == ("POST", "https://event-bus.s3.zoo.sorv.dev/api/events", "celery-hub")
    assert body == {"trace": trace, "kind": "zoo.chain", "text": f"celery-hub event-river {trace}"} and header_trace == trace
    steps = list(Hop.objects.filter(trace=trace).order_by("id").values_list("step", flat=True))
    assert steps == ["worker-ledger", "sent", "sent"]
    found = Client().get(f"/_zoo/trace/{trace}").json()
    assert found["found"] and [h["step"] for h in found["hops"]] == steps
