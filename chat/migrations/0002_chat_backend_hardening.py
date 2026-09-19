import django.utils.timezone
from django.db import migrations, models
from django.db.models import F, Q


def normalise_participant_order(apps, schema_editor):
    """The canonical (lowest pk first) ordering was only enforced in the
    manager, so older rows may be reversed. Fix them before the CheckConstraint
    lands."""
    DirectConversation = apps.get_model("chat", "DirectConversation")
    DirectMessage = apps.get_model("chat", "DirectMessage")

    reversed_rows = DirectConversation.objects.filter(
        participant_one_id__gt=F("participant_two_id")
    )
    for conversation in reversed_rows:
        low = conversation.participant_two_id
        high = conversation.participant_one_id
        canonical = (
            DirectConversation.objects.filter(
                participant_one_id=low, participant_two_id=high
            )
            .exclude(pk=conversation.pk)
            .first()
        )
        if canonical is None:
            conversation.participant_one_id = low
            conversation.participant_two_id = high
            conversation.save(update_fields=["participant_one", "participant_two"])
        else:
            # Both orderings exist - fold the reversed one into the canonical.
            DirectMessage.objects.filter(conversation=conversation).update(
                conversation=canonical
            )
            conversation.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(normalise_participant_order, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="directconversation",
            name="updated_at",
            field=models.DateTimeField(
                db_index=True, default=django.utils.timezone.now
            ),
        ),
        migrations.AddConstraint(
            model_name="directconversation",
            constraint=models.CheckConstraint(
                condition=Q(participant_one__lt=F("participant_two")),
                name="conversation_participants_ordered",
            ),
        ),
        migrations.AddIndex(
            model_name="directmessage",
            index=models.Index(
                fields=["conversation", "created_at"], name="chat_dm_convo_created_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="directmessage",
            index=models.Index(
                fields=["conversation", "is_read"], name="chat_dm_convo_read_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="announcement",
            index=models.Index(
                fields=["store", "-created_at"], name="chat_ann_store_created_idx"
            ),
        ),
    ]