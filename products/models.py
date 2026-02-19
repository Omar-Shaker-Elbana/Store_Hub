from django.db import models
from django.conf import settings
from merchant_interface.models import Store
from django.db.models import AutoField
from django.core.validators import MinValueValidator, MaxValueValidator

# Create your models here.
User = settings.AUTH_USER_MODEL

class Product(models.Model):
    name = models.CharField(max_length=100, null=True, blank=True)
    id = AutoField(primary_key=True)
    description = models.CharField(max_length=500, null=True, blank=True)
    category = models.CharField(max_length=50, null=True, blank=True)
    manufacturing_price = models.DecimalField(null=True, blank=True, max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(null=True, blank=True, max_digits=10, decimal_places=2)
    creation_date = models.DateField(auto_now_add=True, null=True, blank=True)
    store =  models.ForeignKey(Store, on_delete=models.CASCADE)
    stock = models.IntegerField(null=True, blank=True, default=0)
    sold = models.IntegerField(null=True, blank=True, default=0)

class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.OneToOneField(Product, on_delete=models.CASCADE)
    stars = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=500, null=True, blank=True)

class Spec(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, null=True, blank=True)
    value = models.CharField(max_length=100, null=True, blank=True)
    is_selected = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'name'],
                name='unique_spec_name_per_product'
            )
        ]