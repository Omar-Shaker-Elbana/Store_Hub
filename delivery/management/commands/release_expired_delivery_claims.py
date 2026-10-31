from django.core.management.base import BaseCommand

from delivery.services import release_expired_claims
from warehouse.services import release_expired_claims as release_expired_pack_claims


class Command(BaseCommand):
    help = (
        "Return delivery missions whose 24-hour claim has expired to the work "
        "pool. Views already do this on every visit, so scheduling this "
        "(cron/celery beat) is optional — it just keeps statuses fresh when "
        "nobody is looking at a dashboard."
    )

    def handle(self, *args, **options):
        released = release_expired_claims() + release_expired_pack_claims()
        self.stdout.write(self.style.SUCCESS(f"Released {released} expired claim(s)."))