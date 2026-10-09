import json
import os
import socket
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

import zoo
from ledger.models import Entry

from .models import BeatTick, Hop, TaskRun

NAME = "celery-hub"
KEEP_RUNS = 500


def record_hop(trace: str, step: str, detail: str = "") -> None:
    Hop.objects.create(trace=trace, step=step, detail=detail[:300])


@shared_task(name="river.tasks.ping")
def ping() -> dict:
    return {"pong": socket.gethostname()}


@shared_task(name="river.tasks.beat_tick", bind=True)
def beat_tick(self) -> str:
    BeatTick.objects.create(worker=self.request.hostname or "")
    BeatTick.objects.filter(at__lt=timezone.now() - timedelta(days=1)).delete()
    cutoff = TaskRun.objects.order_by("-id").values_list("id", flat=True)[KEEP_RUNS:KEEP_RUNS + 1].first()
    if cutoff:
        TaskRun.objects.filter(id__lte=cutoff).delete()
    return "tick"


@shared_task(name="river.tasks.event_river", bind=True, max_retries=2)
def event_river(self, trace: str) -> str:
    if not zoo.valid_trace(trace):
        raise ValueError("bad trace id")
    text = f"{NAME} event-river {trace}"
    _, created = Entry.objects.get_or_create(trace=trace, defaults={"text": text})
    if created:  # a retry keeps the one ledger row and hop
        record_hop(trace, "worker-ledger", f"ledger row written by {self.request.hostname}")
    url = os.environ.get("EVENTS_URL", "").rstrip("/") + "/api/events"
    body = json.dumps({"trace": trace, "kind": "zoo.chain", "text": text}, separators=(",", ":")).encode()
    try:
        status, answer = zoo.signed_request("POST", url, os.environ["EVENTS_KEY"], NAME, body, trace=trace)
        if not 200 <= status < 300:
            raise RuntimeError(f"event-bus answered {status}: {answer[:120].decode(errors='replace')}")
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=5)
        record_hop(trace, "send-failed", zoo.scrub(f"{type(e).__name__}: {e}", ["EVENTS_KEY"]))
        raise
    record_hop(trace, "sent", f"POST /api/events signed with EVENTS_KEY, event-bus answered {status}")
    return f"sent {trace}"
