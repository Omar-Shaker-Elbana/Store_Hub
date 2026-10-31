from django.urls import path

from . import views

app_name = "warehouse"

urlpatterns = [
    path("incoming/", views.incoming, name="incoming"),
    path("pool/", views.pack_pool, name="pack_pool"),
    path("claim/<int:task_id>/", views.claim_task, name="claim_task"),
    path("claims/<int:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:claim_id>/finish/", views.finish_claim, name="finish_claim"),
    path("claims/<int:claim_id>/release/", views.release_claim, name="release_claim"),
]