from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

# Create your models here.

User = settings.AUTH_USER_MODEL


class Niche(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Store(models.Model):
    name = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    niche = models.ForeignKey(Niche, on_delete=models.PROTECT, db_index=True)
    inauguration_date = models.DateField(auto_now_add=True, blank=True, null=True)
    main_picture = models.ImageField(upload_to="store_pics/", null=True, blank=True)
    cover_picture = models.ImageField(upload_to="store_pics/", null=True, blank=True)
    nationality = models.CharField(max_length=100, null=True, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["-inauguration_date"]

    def __str__(self):
        return self.name or f"Store #{self.pk}"


class Membership(models.Model):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("helper", "Helper"),
        ("manager", "Manager"),
    ]

    WAGE_CHOICES = [
        ("salary", "Salary"),
        ("percentage", "Percentage"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=7, choices=ROLE_CHOICES, default="helper")
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    join_date = models.DateField(null=True, blank=True)
    wage_type = models.CharField(max_length=10, choices=WAGE_CHOICES, default="salary")
    wage = models.DecimalField(
        null=True,
        blank=True,
        decimal_places=2,
        max_digits=10,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        ordering = ["join_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "store"], name="unique_membership_per_store"
            ),
            models.CheckConstraint(
                condition=models.Q(wage_type="salary")
                | models.Q(wage_type="percentage", wage__lte=100),
                name="percentage_wage_capped_at_100",
            ),
            models.CheckConstraint(
                condition=models.Q(wage__isnull=True) | models.Q(wage__gte=0),
                name="membership_wage_not_negative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.role == "owner" and self.wage_type != "percentage":
            raise ValidationError(
                "Owners are paid in profit percentage, not salary — "
                "set wage type to percentage."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} @ {self.store} ({self.role})"


class MembershipInvitation(models.Model):

    STATUS_CHOICES = [
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("pending", "Pending"),
    ]

    inviter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_invitations"
    )
    invitee_email = models.EmailField(max_length=254, null=True, blank=True)
    invitee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_invitations",
        null=True,
        blank=True,
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=7, choices=Membership.ROLE_CHOICES, default="helper"
    )
    wage_type = models.CharField(
        max_length=10, choices=Membership.WAGE_CHOICES, default="salary"
    )
    wage = models.DecimalField(
        null=True,
        blank=True,
        decimal_places=2,
        max_digits=10,
        validators=[MinValueValidator(0)],
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "invitee_email"],
                condition=models.Q(status="pending"),
                name="one_pending_invite_per_email_per_store",
            ),
        ]

    def __str__(self):
        return f"{self.invitee_email} -> {self.store} ({self.status})"


class SuggestedNiche(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    name = models.CharField(max_length=100, unique=True, null=True, blank=True)
    suggested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    suggestion_time = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")

    class Meta:
        ordering = ["-suggestion_time"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class MembershipChangeRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
    ]

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="change_requests"
    )
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    new_role = models.CharField(max_length=7, choices=Membership.ROLE_CHOICES)
    new_wage_type = models.CharField(max_length=10, choices=Membership.WAGE_CHOICES)
    new_wage = models.DecimalField(
        null=True,
        blank=True,
        decimal_places=2,
        max_digits=10,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["membership"],
                condition=models.Q(status="pending"),
                name="one_pending_change_request_per_membership",
            ),
        ]

    def __str__(self):
        return f"Change for {self.membership} ({self.status})"


class Promotion(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    date = models.DateField()
    old_position = models.CharField(max_length=100, null=True, blank=True)
    new_position = models.CharField(max_length=100, null=True, blank=True)
    old_wage_type = models.CharField(max_length=20, null=True, blank=True)
    new_wage_type = models.CharField(max_length=20, null=True, blank=True)
    old_wage = models.DecimalField(
        null=True, blank=True, decimal_places=2, max_digits=10
    )
    new_wage = models.DecimalField(
        null=True, blank=True, decimal_places=2, max_digits=10
    )
    enabled = models.BooleanField(default=True)
    Giver = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="given_promotions"
    )
    Receiver = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="received_promotions"
    )

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.old_position} -> {self.new_position} ({self.store})"
