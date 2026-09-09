from django.urls import path

from . import views

urlpatterns = [
    path("checkout/", views.checkout_view, name="checkout"),
    path("place_order/", views.Place_Order_View, name="place_order"),
    path("history/", views.orders_history, name="orders_history"),
    path("history/<int:order_id>/", views.past_order, name="past_order"),
]
