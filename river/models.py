from django.db import models


class Hop(models.Model):
    """One step of a chain trace (event-river)."""
    trace = models.CharField(max_length=36, db_index=True)
    at = models.DateTimeField(auto_now_add=True)
    step = models.CharField(max_length=40)
    detail = models.CharField(max_length=300, blank=True)


class TaskRun(models.Model):
    """Every Celery task run, written by the worker's task signals."""
    task_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    worker = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=20)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    detail = models.CharField(max_length=300, blank=True)


class BeatTick(models.Model):
    """Written once a minute by the task beat schedules."""
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    worker = models.CharField(max_length=120, blank=True)


class ZooProbe(models.Model):
    token = models.CharField(max_length=32)
