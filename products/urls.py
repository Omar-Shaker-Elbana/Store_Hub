from django.urls import path
from . import views

urlpatterns = [
   path('create_product/', views.Create_Product, name='create_product'),
   path('create_spec/<int:product_id>/', views.Create_Spec, name='create_spec'),
]
