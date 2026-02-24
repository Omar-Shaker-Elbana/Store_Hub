from django.db import models
from django.db import models
from django.conf import settings

# Create your models here.

User = settings.AUTH_USER_MODEL

class Niche(models.Model):
    name = models.CharField(max_length=100, unique=True)

class Store(models.Model):
    name = models.CharField(max_length=50, 
                            null=True, blank=True)
    niche = models.ForeignKey(Niche, on_delete=models.PROTECT, 
                              db_index=True)
    inauguration_date = models.DateField(auto_now_add=True, 
                                        blank=True, null=True)
    picture = models.ImageField(upload_to='store_pics/', 
                                null=True, blank=True)
    nationality = models.CharField(max_length=100, 
                                   null=True, blank=True)
    
class Membership(models.Model):
    ROLE_CHOICES = [
    ('Owner', 'owner'),
    ('Helper', 'helper'),
    ('Manager', 'manager')
    ]

    WAGE_CHOICES = [
    ('Salary', 'salary'),
    ('Percentage', 'percentage')
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=7, 
                            choices=ROLE_CHOICES, default='helper')
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    join_date = models.DateField(null=True, blank=True)
    wage_type = models.CharField(max_length=10, 
                                 choices=WAGE_CHOICES, default='helper')
    wage = models.DecimalField(null = True, blank = True, 
                               decimal_places=2, max_digits=10)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'store'],
                name='unique_membership_per_store'
            )
        ]

class MembershipInvitation(models.Model):
    inviter = models.ForeignKey(User, on_delete=models.CASCADE, 
                                related_name='sent_invitations')
    invitee_email = models.EmailField(max_length=254, null=True, blank=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    role = models.CharField(max_length=7, 
                            choices=Membership.ROLE_CHOICES, default='helper')
    wage_type = models.CharField(max_length=10, 
                                 choices=Membership.WAGE_CHOICES, default='helper')
    wage = models.DecimalField(null=True, blank=True, 
                               decimal_places=2, max_digits=10)
    sent_at = models.DateTimeField(auto_now_add=True)

class SuggestedNiche(models.Model):
    name = models.CharField(max_length=100, unique=True)
    suggested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    suggestion_time = models.DateTimeField(auto_now_add=True, null=True, blank=True)