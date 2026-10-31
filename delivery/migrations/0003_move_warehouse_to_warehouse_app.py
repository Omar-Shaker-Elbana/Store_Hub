import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3: delivery stops owning Warehouse. Only Django's model state
    changes; no SQL runs."""

    dependencies = [
        ("delivery", "0002_rename_warehouse_table"),
        ("warehouse", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="deliverymission",
                    name="warehouse",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="missions",
                        to="warehouse.warehouse",
                    ),
                ),
                migrations.DeleteModel(name="Warehouse"),
            ],
        ),
    ]