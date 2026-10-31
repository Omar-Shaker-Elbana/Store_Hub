from django.db import migrations


class Migration(migrations.Migration):
    """Step 1 of moving Warehouse to the warehouse app: rename its table so
    the rows (and the missions' foreign keys to them) carry over untouched."""

    dependencies = [
        ("delivery", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="warehouse",
            table="warehouse_warehouse",
        ),
    ]