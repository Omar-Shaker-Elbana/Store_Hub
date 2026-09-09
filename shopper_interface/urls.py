from django.urls import path

from . import views

app_name = "shopper_interface"

urlpatterns = [
    path("", views.home, name="home"),
    path("feed/page/", views.home_feed_page, name="home_feed_page"),
    path("cart/", views.Cart_view, name="cart"),
    path("cart/quick-add/<int:product_id>/", views.quick_add_to_cart, name="quick_add_to_cart"),
    path("cart/item/<int:item_id>/update/", views.update_cart_item, name="update_cart_item"),
    path("cart/item/<int:item_id>/remove/", views.remove_cart_item, name="remove_cart_item"),
    path("wishlist/", views.Wishlist_view, name="wishlist"),
    path("wishlist/quick-add/<int:product_id>/", views.quick_add_to_wishlist, name="quick_add_to_wishlist"),
    path("search/", views.search_view, name="search"),
    path("search/page/", views.search_results_page, name="search_results_page"),
]
