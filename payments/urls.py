from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("wallet/", views.wallet, name="wallet"),
    path("wallet/withdraw/", views.withdraw, name="withdraw"),
    path("platform/", views.platform, name="platform"),
    path("platform/run-payroll/", views.run_payroll_now, name="run_payroll_now"),
]
