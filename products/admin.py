from django.contrib import admin

from .models import (Category, Product, Product_Image, Review, Spec, SpecType,
                     SuggestedCategory)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    search_fields = ("name", "store__name")


admin.site.register(Category)
admin.site.register(Review)
admin.site.register(Spec)
admin.site.register(SuggestedCategory)
admin.site.register(Product_Image)
admin.site.register(SpecType)