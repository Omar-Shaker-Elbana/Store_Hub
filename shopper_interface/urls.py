from django.urls import path
from . import views

app_name = 'shopper_interface'

urlpatterns = [
    path('', views.home, name='home'),
]
