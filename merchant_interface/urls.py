from django.urls import path

from . import views

urlpatterns = [
    path("store/<int:store_id>/", views.show_store, name="show_store"),
    path("create_store/", views.create_store, name="create_store"),
    path(
        "store/<int:store_id>/invitations/",
        views.manage_store_invitations,
        name="manage_store_invitations",
    ),
    path("edit_store/<int:store_id>/", views.edit_store, name="edit_store"),
    path("all_my_stores/", views.all_my_stores, name="all_my_stores"),
    path(
        "membership/<int:membership_id>/edit/",
        views.edit_membership,
        name="edit_membership",
    ),
    path(
        "membership/<int:membership_id>/remove/",
        views.remove_membership,
        name="remove_membership",
    ),
    path("my_job_invitations/", views.my_job_invitations, name="my_job_invitations"),
    path(
        "my_promotion_history/", views.my_promotion_history, name="my_promotion_history"
    ),
    path(
        "user/<int:user_id>/history/",
        views.view_user_promotion_history,
        name="view_user_promotion_history",
    ),
    path(
        "store/<int:store_id>/inventory/",
        views.manage_store_inventory,
        name="manage_store_inventory",
    ),
    # path("store/<int:store_id>/members/", views.store_members, name="store_members"),
    # was views.my_store / "my_store" — function is actually store_analytics
    # path(
    #     "my_store/<int:store_id>/",
    #     views.store_analytics,
    #     name="store_analytics",
    # ),
    # was views.my_analytics / "my_analytics" — function is actually user_analytics
    # path(
    #     "my_store/<int:store_id>/analytics/",
    #     views.user_analytics,
    #     name="user_analytics",
    # ),
]
