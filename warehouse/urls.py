from django.urls import path

from . import views

app_name = "warehouse"

urlpatterns = [
    path("incoming/", views.incoming, name="incoming"),
]