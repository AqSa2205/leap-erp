from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "dashboard"

    def ready(self):
        # Wire central FileField cleanup signals (delete stored files on row
        # delete / replacement) across all file-bearing models.
        from dashboard.storage_cleanup import register_file_cleanup
        register_file_cleanup()
