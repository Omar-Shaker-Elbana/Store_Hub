from django.db import models
from django.conf import settings
from merchant_interface.models import Store
from django.db.models import AutoField
from django.core.validators import MinValueValidator, MaxValueValidator
# from django.utils import timezone

# Create your models here.
User = settings.AUTH_USER_MODEL

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

class Product(models.Model):
    name = models.CharField(max_length=100, null=True, blank=True)
    id = AutoField(primary_key=True)
    image1 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True)
    image2 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True)
    image3 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True) 
    description = models.CharField(max_length=500,
                                    null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, db_index=True)
    manufacturing_price = models.DecimalField(null=True,
                                               blank=True,
                                                max_digits=10,
                                                decimal_places=2)
    selling_price = models.DecimalField(null=True,
                                        blank=True,
                                        max_digits=10,
                                        decimal_places=2)
    creation_date = models.DateField(auto_now_add=True,
                                      null=True, blank=True)
    store =  models.ForeignKey(Store, on_delete=models.CASCADE,
                                db_index=True)
    current_stock = models.IntegerField( default=0, 
                                        null=True, blank=True)
    sold = models.IntegerField(null=True, blank=True, default=0)
    offer = models.DecimalField(
    max_digits=5,
    decimal_places=2,
    validators=[MinValueValidator(0), MaxValueValidator(100)],
    null=True,
    blank=True)

    class Meta:
        ordering = ['-creation_date']
        indexes = [
        models.Index(fields=['store', 'category']),
    ]

class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.OneToOneField(Product, on_delete=models.CASCADE)
    stars = models.IntegerField(validators=[MinValueValidator(1), 
                                            MaxValueValidator(5)],
                                            null=True, blank=True)
    comment = models.CharField(max_length=500, null=True, blank=True)
    creation_date = models.DateField(auto_now_add=True,
                                      null=True, blank=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'product'],
                name='unique_review_per_user_per_product'
            )
        ]


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