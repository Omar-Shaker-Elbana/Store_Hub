"""
Manual, end-to-end smoke test for the fulfillment app.

Runs the exact same request/view/template path a real browser hits — via
Django's test Client — and prints what happens at every step: status
codes, redirect targets, flash messages, and the resulting DB state.

Safe to re-run: the store/users/product are get_or_create'd, so repeated
runs just add another order to a growing, realistic order history instead
of erroring on duplicate data.

Usage:
    python manage.py shell -c "exec(open('fulfillment/smoke_test.py').read())"

After it runs, the printed credentials let you log into the actual
browser (http://127.0.0.1:8000/ or whatever port you're running on) as
any of these demo users and click through the same flow by hand.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client
from django.test.utils import setup_test_environment
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from merchant_interface.models import Membership, Niche, Store
from orders.models import Order, OrderItem, StoreOrder
from products.models import Category, Product
from fulfillment.models import OrderClaim, WalletTransaction
from fulfillment import services

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")

setup_test_environment()

User = get_user_model()
DEMO_PASSWORD = "DemoPass123!"


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------

def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def step(description):
    print(f"\n--- {description} ---")


def show_response(response, expect_status=None):
    line = f"status={response.status_code}"
    if response.status_code in (301, 302):
        line += f"  ->  redirects to: {response.url}"
    print(line)
    if expect_status is not None and response.status_code != expect_status:
        print(f"    !! expected status {expect_status}, got {response.status_code}")
    msgs = list(get_messages(response.wsgi_request))
    for m in msgs:
        print(f"    [message:{m.tags}] {m}")
    return response


# ---------------------------------------------------------------------------
# 1. Set up demo data (idempotent)
# ---------------------------------------------------------------------------

section("SETTING UP DEMO STORE, STAFF, AND A CUSTOMER ORDER")

niche, _ = Niche.objects.get_or_create(name="Demo Niche")
store, _ = Store.objects.get_or_create(name="Fulfillment Smoke Test Store", defaults={"niche": niche})

def get_or_create_user(username):
    user, created = User.objects.get_or_create(
        username=username, defaults={"email": f"{username}@example.com"}
    )
    if created:
        user.set_password(DEMO_PASSWORD)
        user.save()
    return user

owner = get_or_create_user("demo_owner")
manager = get_or_create_user("demo_manager")
helper1 = get_or_create_user("demo_helper1")
helper2 = get_or_create_user("demo_helper2")
customer = get_or_create_user("demo_customer")

def get_or_create_membership(user, role, wage_type, wage):
    membership, _ = Membership.objects.get_or_create(
        user=user,
        store=store,
        defaults={
            "role": role,
            "wage_type": wage_type,
            "wage": wage,
            "join_date": timezone.now().date(),
        },
    )
    return membership

owner_membership = get_or_create_membership(owner, "owner", "percentage", Decimal("100.00"))
manager_membership = get_or_create_membership(manager, "manager", "percentage", Decimal("20.00"))
helper1_membership = get_or_create_membership(helper1, "helper", "percentage", Decimal("10.00"))
helper2_membership = get_or_create_membership(helper2, "helper", "salary", None)

category, _ = Category.objects.get_or_create(name="Demo Category")
product, _ = Product.objects.get_or_create(
    store=store, category=category, name="Demo Widget",
    defaults={"selling_price": Decimal("15.00"), "current_stock": 100},
)

# Fresh order every run, so the walkthrough always has something to claim
order = Order.objects.create(
    user=customer, shipping_address="1 Demo Street", payment_type="cash", status="Pending"
)
store_order = StoreOrder.objects.create(order=order, store=store, status="Pending", subtotal=Decimal("45.00"))
OrderItem.objects.create(store_order=store_order, product=product, quantity=3, price_at_purchase=Decimal("15.00"))

second_order = Order.objects.create(
    user=customer, shipping_address="1 Demo Street", payment_type="cash", status="Pending"
)
second_store_order = StoreOrder.objects.create(order=second_order, store=store, status="Pending", subtotal=Decimal("15.00"))
OrderItem.objects.create(store_order=second_store_order, product=product, quantity=1, price_at_purchase=Decimal("15.00"))

print(f"Store:            {store.name}  (id={store.id})")
print(f"Owner:            {owner.username} / {DEMO_PASSWORD}")
print(f"Manager:          {manager.username} / {DEMO_PASSWORD}  (percentage, 20%)")
print(f"Helper 1:         {helper1.username} / {DEMO_PASSWORD}  (percentage, 10%)")
print(f"Helper 2:         {helper2.username} / {DEMO_PASSWORD}  (salary)")
print(f"Order to claim:   #{order.id} / StoreOrder #{store_order.id}  ($45.00, 3x Demo Widget)")
print(f"Order to expire:  #{second_order.id} / StoreOrder #{second_store_order.id}  ($15.00, 1x Demo Widget)")


# ---------------------------------------------------------------------------
# 2. Permission boundaries
# ---------------------------------------------------------------------------

section("PERMISSION BOUNDARIES")

step("Anonymous visitor hits the work pool (should redirect to login)")
anon_client = Client()
show_response(anon_client.get(reverse("fulfillment:work_pool", args=[store.id])))

step("Pure owner tries to open the work pool (owners can't work orders)")
owner_client = Client()
owner_client.force_login(owner)
show_response(owner_client.get(reverse("fulfillment:work_pool", args=[store.id])))

step("Helper tries to open Store Overview (helpers can't view oversight)")
helper_probe_client = Client()
helper_probe_client.force_login(helper1)
show_response(helper_probe_client.get(reverse("fulfillment:store_overview", args=[store.id])))

step("Owner opens Store Overview (should succeed)")
owner_client2 = Client()
owner_client2.force_login(owner)
show_response(owner_client2.get(reverse("fulfillment:store_overview", args=[store.id])), expect_status=200)


# ---------------------------------------------------------------------------
# 3. The main claim -> finish loop, as Helper 1
# ---------------------------------------------------------------------------

section("HELPER 1 WORKS AN ORDER FROM CLAIM TO FINISH")

client1 = Client()
client1.force_login(helper1)

step("Helper 1 opens the work pool")
response = show_response(client1.get(reverse("fulfillment:work_pool", args=[store.id])), expect_status=200)
available_ids = [so.id for so in response.context["available_orders"]]
print(f"    available_orders in pool: {available_ids}")

step("Helper 1 claims the order")
response = client1.post(
    reverse("fulfillment:claim_order", args=[store.id, store_order.id])
)
show_response(response)
claim = OrderClaim.objects.get(store_order=store_order, status="claimed")
print(f"    claim id={claim.id}, deadline={claim.deadline}")

step("Helper 2 tries to claim the SAME order (should fail: already claimed)")
client2 = Client()
client2.force_login(helper2)
response = client2.post(reverse("fulfillment:claim_order", args=[store.id, store_order.id]))
show_response(response)

step("Helper 1 views the claim detail page")
response = show_response(client1.get(reverse("fulfillment:claim_detail", args=[claim.id])), expect_status=200)
print(f"    can_act={response.context['can_act']}  status={response.context['claim'].status}")

step("Manager views the same claim (read-only oversight)")
client_mgr = Client()
client_mgr.force_login(manager)
response = show_response(client_mgr.get(reverse("fulfillment:claim_detail", args=[claim.id])), expect_status=200)
print(f"    can_act={response.context['can_act']}  (should be False)")

step("Manager tries to finish helper 1's claim anyway (should fail)")
response = client_mgr.post(reverse("fulfillment:finish_claim", args=[claim.id]))
show_response(response)
claim.refresh_from_db()
print(f"    claim status after manager's attempt: {claim.status} (should still be 'claimed')")

step("Helper 1 finishes the claim for real")
response = client1.post(reverse("fulfillment:finish_claim", args=[claim.id]))
show_response(response)
claim.refresh_from_db()
store_order.refresh_from_db()
print(f"    claim.status={claim.status}  items_processed={claim.items_processed}  wage_earned={claim.wage_earned}")
print(f"    store_order.status={store_order.status}")

step("Helper 1 checks their wallet")
response = show_response(client1.get(reverse("fulfillment:my_wallet", args=[store.id])), expect_status=200)
print(f"    wallet_balance={response.context['membership'].wallet_balance}")
print(f"    transactions this run: {[str(t) for t in response.context['transactions'][:3]]}")


# ---------------------------------------------------------------------------
# 4. Claim -> release loop, as Helper 2
# ---------------------------------------------------------------------------

section("HELPER 2 CLAIMS THE SECOND ORDER, THEN RELEASES IT")

step("Helper 2 claims the second order")
response = client2.post(reverse("fulfillment:claim_order", args=[store.id, second_store_order.id]))
show_response(response)
second_claim = OrderClaim.objects.get(store_order=second_store_order, status="claimed")

step("Helper 1 tries to release Helper 2's claim (should fail)")
response = client1.post(reverse("fulfillment:release_claim", args=[second_claim.id]))
show_response(response)
second_claim.refresh_from_db()
print(f"    claim status: {second_claim.status} (should still be 'claimed')")

step("Helper 2 releases their own claim")
response = client2.post(reverse("fulfillment:release_claim", args=[second_claim.id]))
show_response(response)
second_claim.refresh_from_db()
print(f"    claim status: {second_claim.status} (should be 'released')")

step("The order is back in the pool for helper 1")
response = client1.get(reverse("fulfillment:work_pool", args=[store.id]))
back_in_pool = second_store_order in response.context["available_orders"]
print(f"    second_store_order back in available_orders: {back_in_pool}")


# ---------------------------------------------------------------------------
# 5. Expiry
# ---------------------------------------------------------------------------

section("A STALE CLAIM AUTO-EXPIRES ON THE NEXT PAGE VIEW")

step("Helper 1 re-claims the second order, then we backdate the deadline (simulating 24h+ passing)")
client1.post(reverse("fulfillment:claim_order", args=[store.id, second_store_order.id]))
stale_claim = OrderClaim.objects.get(store_order=second_store_order, status="claimed")
stale_claim.deadline = timezone.now() - timedelta(hours=1)
stale_claim.save(update_fields=["deadline"])
print(f"    claim id={stale_claim.id} deadline backdated to {stale_claim.deadline}")

step("Helper 2 loads the work pool (this triggers the opportunistic expiry check)")
response = client2.get(reverse("fulfillment:work_pool", args=[store.id]))
stale_claim.refresh_from_db()
print(f"    claim status after page load: {stale_claim.status} (should be 'expired')")
back_in_pool = second_store_order in response.context["available_orders"]
print(f"    second_store_order back in available_orders: {back_in_pool}")


# ---------------------------------------------------------------------------
# 6. Owner's-eye view
# ---------------------------------------------------------------------------

section("OWNER CHECKS STORE OVERVIEW")

final_owner_client = Client()
final_owner_client.force_login(owner)
response = final_owner_client.get(reverse("fulfillment:store_overview", args=[store.id]))
print(f"active_claims:  {[c.id for c in response.context['active_claims']]}")
print(f"recent_claims:  {[(c.id, c.status) for c in response.context['recent_claims']]}")
print("staff_wallets:")
for m in response.context["staff_wallets"]:
    print(f"    {m.user.username:15s} role={m.role:8s} wage_type={m.wage_type:11s} balance=${m.wallet_balance}")


section("DONE")
print("Log into the browser with any of the credentials printed above to click through")
print(f"the same flow by hand, starting at:  /fulfillment/store/{store.id}/pool/")