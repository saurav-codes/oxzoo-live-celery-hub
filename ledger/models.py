from django.db import models


class Entry(models.Model):
    """The worker's ledger row for each event-river run (private database)."""
    trace = models.CharField(max_length=36, unique=True)
    text = models.CharField(max_length=300)
    at = models.DateTimeField(auto_now_add=True)


class ZooProbe(models.Model):
    token = models.CharField(max_length=32)
