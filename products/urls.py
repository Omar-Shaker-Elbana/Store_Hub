from django.urls import path

from . import views

urlpatterns = [
    path(
        "create_product/<int:current_store_id>/",
        views.Create_Product,
        name="create_product",
    ),
    path("manage_specs/<int:product_id>/", views.Manage_Specs, name="manage_specs"),
    path("view_product/<int:product_id>/", views.View_Product, name="view_product"),
    path(
        "update_product/<int:product_id>/", views.Update_Product, name="update_product"
    ),
    path(
        "manage_images/<int:product_id>/",
        views.Manage_Product_Images,
        name="manage_product_images",
    ),
    path(
        "review_suggested_categories/",
        views.Review_Suggested_Categories,
        name="review_suggested_categories",
    ),
    path("spec-types/search/", views.spec_type_search, name="spec_type_search"),
]
