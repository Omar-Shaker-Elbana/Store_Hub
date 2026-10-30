from django.core.management.base import BaseCommand

from delivery import services


class Command(BaseCommand):
    help = "Return missions whose claim window has expired to the work pool."

    def handle(self, *args, **options):
        released = services.release_expired_missions()
        self.stdout.write(f"Released {released} expired mission(s).")