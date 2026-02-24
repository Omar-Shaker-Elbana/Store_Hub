from django.urls import path
from . import views

urlpatterns = [
   path('create_product/<int:current_store_id>/', views.Create_Product, name='create_product'),
   path('create_spec/<int:product_id>/', views.Create_Spec, name='create_spec'),
   path('view_product/<int:product_id>/', views.View_Product, name='view_product'),
   path('update_spec/<int:product_id>/<str:spec_name>/', views.Update_Spec, name='update_spec'),
   path('suggest_category/', views.Suggest_Category, name='suggest_category'),
   path('update_product/<int:product_id>/', views.Update_Product, name='update_product'),
]
