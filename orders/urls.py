from django.urls import path
from . import views

urlpatterns = [
    path('mycart/', views.Cart_view, name='mycart'),
    path('wishlist/', views.Wishlist_view, name='wishlist'),
    path('place_order/', views.Place_Order_View, name='place_order'),
]