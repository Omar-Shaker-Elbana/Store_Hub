from django.core.management.base import BaseCommand

from payments import services


class Command(BaseCommand):
    help = (
        "Pay every finished month's salaries and owner profit shares that "
        "haven't been paid yet. Safe to run as often as you like (cron-friendly)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--current",
            action="store_true",
            help="Also pay the current, unfinished month right now (early close).",
        )

    def handle(self, *args, **options):
        if options["current"]:
            ran = services.run_payroll_now()
            self.stdout.write(self.style.SUCCESS(f"Paid {ran} store-month(s) incl. the current month."))
        else:
            closed = services.close_due_payrolls()
            self.stdout.write(self.style.SUCCESS(f"Paid {closed} finished store-month(s)."))
