from django.urls import path
from . import views

urlpatterns = [
    path('store/<int:store_id>/', views.show_store, name='show_store'),
    path('create_store/', views.create_store, name='create_store'),
    path('add_members/<int:store_id>/', views.add_members, name='add_members'),
    path('edit_store/<int:store_id>/', views.edit_store, name='edit_store'),
    path('all_my_stores/', views.all_my_stores, name='all_my_stores'),
]
