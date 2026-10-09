from django.apps import AppConfig


class RiverConfig(AppConfig):
    name = "river"

    def ready(self):
        from . import signals  # noqa: F401
