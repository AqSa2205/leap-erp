from django.apps import AppConfig


class DevtrackingConfig(AppConfig):
    name = "devtracking"

    def ready(self):
        import devtracking.signals  # noqa: F401