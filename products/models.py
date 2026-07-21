from django.db import models
from django.conf import settings
from merchant_interface.models import Store
# from django.db.models import AutoField
from django.core.validators import MinValueValidator, MaxValueValidator
# from django.utils import timezone

# Create your models here.
User = settings.AUTH_USER_MODEL

class Category(models.Model):
    name = models.CharField(max_length=100)

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children"
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="unique_category_per_parent"
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(parent__isnull=True),
                name="unique_root_category_name"
            ),
        ]

    def __str__(self):
        return self.name
        
class Product(models.Model):
    name = models.CharField(max_length=100, null=True, blank=True)
    # id = AutoField(primary_key=True)
    image1 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True)
    image2 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True)
    image3 = models.ImageField(upload_to='products_pics/',
                                null=True, blank=True) 
    description = models.CharField(max_length=500, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
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
    # store =  models.ForeignKey(Store, on_delete=models.CASCADE,
                                # db_index=True)
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
        models.Index(fields=["category"])
    ]

class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    stars = models.PositiveSmallIntegerField(
    validators=[
        MinValueValidator(1),
        MaxValueValidator(5)
    ],
    null=True,
    blank=True
)
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


class SpecType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Spec(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="specs"
    )

    spec_type = models.ForeignKey(
        SpecType,
        on_delete=models.PROTECT,
        related_name="specs",
        null=True, blank=True
    )

    value = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["spec_type__name"]

        constraints = [
            models.UniqueConstraint(
                fields=["product", "spec_type"],
                name="unique_spec_per_product"
            )
        ]

        indexes = [
            models.Index(fields=["spec_type"]),
            models.Index(fields=["spec_type", "value"]),
        ]

    def __str__(self):
        return f"{self.spec_type.name}: {self.value}"
    
class SuggestedCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    suggester = models.ForeignKey(User, on_delete=models.CASCADE)
    suggestion_date = models.DateField(auto_now_add=True,
                                      null=True, blank=True)
    
