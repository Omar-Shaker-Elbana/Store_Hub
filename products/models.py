from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from merchant_interface.models import Store

# Create your models here.
User = settings.AUTH_USER_MODEL


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ActiveProductManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Product(models.Model):
    name = models.CharField(max_length=100, null=True, blank=True)
    description = models.CharField(max_length=500, null=True, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products")
    manufacturing_price = models.DecimalField(
        null=True,
        blank=True,
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    selling_price = models.DecimalField(
        null=True,
        blank=True,
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(default=True)
    creation_date = models.DateField(auto_now_add=True, null=True, blank=True)
    current_stock = models.IntegerField(
        default=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    sold = models.IntegerField(
        null=True, blank=True, default=0, validators=[MinValueValidator(0)]
    )
    last_updated = models.DateTimeField(auto_now=True, null=True, blank=True)
    offer = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        null=True,
        blank=True,
    )
    objects = models.Manager()
    active = ActiveProductManager()

    class Meta:
        ordering = ["-creation_date"]
        indexes = [
            models.Index(fields=["store", "category"]),
            models.Index(fields=["category"]),
            models.Index(fields=["is_active"]),
        ]

    @property
    def discounted_price(self):
        if self.selling_price is None:
            return None
        if not self.offer:
            return self.selling_price
        discount = (self.selling_price * self.offer) / 100
        return self.selling_price - discount


class Product_Image(models.Model):
    image = models.ImageField(upload_to="products_pics/")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    is_primary = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        indexes = [
            models.Index(fields=["product", "order"]),
        ]


class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reviews")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="reviews"
    )
    stars = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)], null=True, blank=True
    )
    comment = models.CharField(max_length=500, null=True, blank=True)
    creation_date = models.DateField(auto_now_add=True, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], name="unique_review_per_user_per_product"
            )
        ]


class SpecType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Spec(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specs")

    spec_type = models.ForeignKey(
        SpecType, on_delete=models.PROTECT, related_name="specs", null=True, blank=True
    )

    value = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["spec_type__name"]

        constraints = [
            models.UniqueConstraint(
                fields=["product", "spec_type"], name="unique_spec_per_product"
            )
        ]

        indexes = [
            models.Index(fields=["spec_type"]),
            models.Index(fields=["spec_type", "value"]),
        ]

    def __str__(self):
        if self.spec_type is None:
            return f"(no type): {self.value}"
        return f"{self.spec_type.name}: {self.value}"


class SuggestedCategory(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    name = models.CharField(max_length=100, unique=True)
    suggester = models.ForeignKey(User, on_delete=models.CASCADE)
    suggestion_date = models.DateField(auto_now_add=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
