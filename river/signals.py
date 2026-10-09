"""Record every task run in TaskRun (status page: recent task runs)."""

from celery.signals import task_failure, task_postrun, task_prerun
from django.utils import timezone

from .models import TaskRun


@task_prerun.connect
def started(task_id=None, task=None, **_):
    TaskRun.objects.update_or_create(task_id=task_id, defaults={
        "name": task.name, "worker": task.request.hostname or "", "state": "STARTED",
        "started_at": timezone.now(), "finished_at": None})


@task_postrun.connect
def finished(task_id=None, state=None, retval=None, **_):
    TaskRun.objects.filter(task_id=task_id).update(state=state or "", finished_at=timezone.now(),
                                                   detail=str(retval)[:300] if retval is not None else "")


@task_failure.connect
def failed(task_id=None, exception=None, **_):
    TaskRun.objects.filter(task_id=task_id).update(detail=f"{type(exception).__name__}: {exception}"[:300])
