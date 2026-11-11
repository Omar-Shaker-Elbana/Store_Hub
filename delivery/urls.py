from django.urls import path

from . import views

app_name = "delivery"

urlpatterns = [
    path("pool/", views.work_pool, name="work_pool"),
    path("claim/<int:mission_id>/", views.claim_mission, name="claim_mission"),
    path("claims/<int:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:claim_id>/pickup/", views.pick_up_claim, name="pick_up_claim"),
    path("claims/<int:claim_id>/complete/", views.complete_claim, name="complete_claim"),
    path("claims/<int:claim_id>/release/", views.release_claim, name="release_claim"),
    path("orders/<int:order_id>/tracking/", views.order_tracking, name="order_tracking"),
]