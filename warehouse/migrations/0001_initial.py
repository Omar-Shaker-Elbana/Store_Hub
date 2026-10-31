from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 2: the warehouse app takes over the model. Database untouched:
    the table was already renamed to warehouse_warehouse by delivery 0002."""

    initial = True

    dependencies = [
        ("delivery", "0002_rename_warehouse_table"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Warehouse",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("name", models.CharField(max_length=120)),
                        ("address", models.CharField(max_length=255)),
                        ("phone", models.CharField(blank=True, max_length=32)),
                        ("is_default", models.BooleanField(default=False)),
                        ("is_active", models.BooleanField(default=True)),
                    ],
                ),
            ],
        ),
    ]