from django.urls import path

from . import views

app_name = "fulfillment"

urlpatterns = [
    path("store/<int:store_id>/pool/", views.work_pool, name="work_pool"),
    path(
        "store/<int:store_id>/claim/<int:store_order_id>/",
        views.claim_order,
        name="claim_order",
    ),
    path("claim/<int:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claim/<int:claim_id>/finish/", views.finish_claim, name="finish_claim"),
    path("claim/<int:claim_id>/release/", views.release_claim, name="release_claim"),
    path("store/<int:store_id>/overview/", views.store_overview, name="store_overview"),
    path("store/<int:store_id>/wallet/", views.my_wallet, name="my_wallet"),
]