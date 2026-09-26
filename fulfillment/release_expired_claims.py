from django.core.management.base import BaseCommand

from fulfillment.models import OrderClaim
from fulfillment.services import release_expired_claims


class Command(BaseCommand):
    help = (
        "Expire any OrderClaim whose 24-hour window has passed, freeing its "
        "StoreOrder back into the work pool. The views already do this "
        "opportunistically on every visit, so running this on a schedule "
        "(cron/celery beat) is optional — it just keeps things tidy even "
        "when nobody's looking at the dashboard."
    )

    def handle(self, *args, **options):
        before = set(
            OrderClaim.objects.filter(status="claimed").values_list("id", flat=True)
        )
        release_expired_claims()
        after = set(
            OrderClaim.objects.filter(status="expired").values_list("id", flat=True)
        )
        expired_now = before & after
        self.stdout.write(
            self.style.SUCCESS(f"Expired {len(expired_now)} stale claim(s).")
        )