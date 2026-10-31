from django.db import models


class Warehouse(models.Model):
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Warehouse.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_active=True).order_by("-is_default", "pk").first()