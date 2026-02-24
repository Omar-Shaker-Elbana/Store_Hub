from django.contrib import admin
from .models import Product, Category, Review, Spec
# Register your models here.

admin.site.register(Product)
admin.site.register(Category)  
admin.site.register(Review)
admin.site.register(Spec)