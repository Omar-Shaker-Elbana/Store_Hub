"""
Tests for the payments app: rates, ledger accounts, courier / packer pay,
order settlement (1% platform fee, store treasury, staff commission),
monthly payroll, withdrawals, the wallet / platform pages, and the checkout
fee. The last class drives a real order through pickup -> packing -> delivery
and proves the two guarantees that matter:

* nothing is created or lost (all balances add up to the cash collected), and
* the platform account ends up with exactly its 1% (never negative).
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from delivery.models import DeliveryMission
from delivery.tests import DeliveryTestCase, make_product, make_store, make_user
from fulfillment.models import OrderClaim
from merchant_interface.models import Membership
from orders.models import Order, StoreOrder
from shopper_interface.models import Cart, CartItem

from . import services
from .models import Account, LedgerEntry, OrderSettlement, PayrollRun
from .rates import (
    COURIER_TRIP_FEE,
    PLATFORM_FEE_RATE,
    WAREHOUSE_PACK_FEE,
    money,
    platform_fee,
    shipping_fee,
)

FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]
D = Decimal


def fund(account, amount, kind="sale"):
    """Put money straight into an account (test setup only)."""
    with transaction.atomic():
        locked = services._lock(account)
        services._post(locked, D(amount), kind, "test funding")
    account.refresh_from_db()


def member(user, store, role, wage_type, wage, join_date=None):
    return Membership.objects.create(
        user=user, store=store, role=role, wage_type=wage_type, wage=wage, join_date=join_date
    )


def aware(day):
    return timezone.make_aware(datetime.combine(day, time(12)))


def first_of_next_month_plus(days=5):
    this_month = timezone.now().date().replace(day=1)
    return aware(services.next_month(this_month) + timedelta(days=days))


# ===========================================================================
# 1. Rates
# ===========================================================================


class RatesTests(SimpleTestCase):
    def test_the_agreed_numbers(self):
        self.assertEqual(PLATFORM_FEE_RATE, D("0.01"))
        self.assertEqual(COURIER_TRIP_FEE, D("20.00"))
        self.assertEqual(WAREHOUSE_PACK_FEE, D("10.00"))

    def test_platform_fee_is_one_percent(self):
        self.assertEqual(platform_fee(D("100.00")), D("1.00"))
        self.assertEqual(platform_fee(D("2500")), D("25.00"))
        self.assertEqual(platform_fee(0), D("0.00"))

    def test_platform_fee_rounds_half_up_to_cents(self):
        self.assertEqual(platform_fee(D("0.50")), D("0.01"))  # 0.005 -> 0.01
        self.assertEqual(platform_fee(D("49.99")), D("0.50"))  # 0.4999 -> 0.50
        self.assertEqual(platform_fee(D("0.49")), D("0.00"))

    def test_money_rounds_half_up(self):
        self.assertEqual(money("1.005"), D("1.01"))
        self.assertEqual(money("1.004"), D("1.00"))

    def test_shipping_fee_is_exactly_the_logistics_cost(self):
        # one pickup per store + one dropoff trip + one packing fee
        for stores in (1, 2, 3, 5):
            cost = COURIER_TRIP_FEE * stores + COURIER_TRIP_FEE + WAREHOUSE_PACK_FEE
            self.assertEqual(shipping_fee(stores), cost)
        self.assertEqual(shipping_fee(1), D("50.00"))
        self.assertEqual(shipping_fee(2), D("70.00"))

    def test_shipping_fee_of_nothing_is_zero(self):
        self.assertEqual(shipping_fee(0), D("0.00"))
        self.assertEqual(shipping_fee(None), D("0.00"))


# ===========================================================================
# 2. Accounts and constraints
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class AccountTests(TestCase):
    def test_platform_account_is_a_singleton(self):
        a = services.platform_account()
        b = services.platform_account()
        self.assertEqual(a.pk, b.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(kind="platform")

    def test_user_and_store_accounts_are_created_once(self):
        user, store = make_user("u1"), make_store()
        self.assertEqual(services.user_account(user).pk, services.user_account(user).pk)
        self.assertEqual(services.store_account(store).pk, services.store_account(store).pk)
        self.assertEqual(Account.objects.filter(kind="user").count(), 1)
        self.assertEqual(Account.objects.filter(kind="store").count(), 1)

    def test_account_must_match_its_kind(self):
        user = make_user("u1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(kind="user")  # a wallet needs a user
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(kind="platform", user=user)

    def test_only_the_platform_account_may_go_negative(self):
        user, store = make_user("u1"), make_store()
        wallet, treasury = services.user_account(user), services.store_account(store)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.filter(pk=wallet.pk).update(balance=D("-0.01"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.filter(pk=treasury.pk).update(balance=D("-0.01"))
        platform = services.platform_account()
        Account.objects.filter(pk=platform.pk).update(balance=D("-5.00"))  # allowed

    def test_wallet_balance_never_creates_a_wallet(self):
        user = make_user("u1")
        self.assertEqual(services.wallet_balance(user), D("0.00"))
        self.assertFalse(Account.objects.filter(user=user).exists())

    def test_str_methods(self):
        user, store = make_user("u1"), make_store()
        for account in (services.platform_account(), services.user_account(user), services.store_account(store)):
            self.assertTrue(str(account))

    def test_entries_record_the_running_balance(self):
        wallet = services.user_account(make_user("u1"))
        fund(wallet, "10.00")
        fund(wallet, "5.50")
        balances = list(LedgerEntry.objects.filter(account=wallet).order_by("id").values_list("balance_after", flat=True))
        self.assertEqual(balances, [D("10.00"), D("15.50")])

    def test_ledger_ref_is_unique_per_account_and_kind(self):
        wallet = services.user_account(make_user("u1"))
        fields = dict(account=wallet, kind="packing", amount=D("10"), balance_after=D("10"), ref="x:1")
        LedgerEntry.objects.create(**fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LedgerEntry.objects.create(**fields)
        # blank refs (withdrawals) can repeat freely
        for _ in range(2):
            LedgerEntry.objects.create(account=wallet, kind="withdrawal", amount=D("-1"), balance_after=D("0"))


# ===========================================================================
# 3. Withdrawals
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class WithdrawTests(TestCase):
    def setUp(self):
        self.user = make_user("worker")
        self.wallet = services.user_account(self.user)
        fund(self.wallet, "50.00", kind="packing")

    def balance(self):
        return services.wallet_balance(self.user)

    def test_withdraw_only_subtracts_the_requested_amount(self):
        entry = services.withdraw(self.user, "20")
        self.assertEqual(self.balance(), D("30.00"))
        self.assertEqual(entry.amount, D("-20.00"))
        self.assertEqual(entry.kind, "withdrawal")
        self.assertEqual(entry.balance_after, D("30.00"))

    def test_withdraw_everything(self):
        services.withdraw(self.user, "50.00")
        self.assertEqual(self.balance(), D("0.00"))

    def test_cannot_withdraw_more_than_the_balance(self):
        with self.assertRaises(services.WalletError):
            services.withdraw(self.user, "50.01")
        self.assertEqual(self.balance(), D("50.00"))
        self.assertEqual(LedgerEntry.objects.filter(kind="withdrawal").count(), 0)

    def test_invalid_amounts_are_rejected(self):
        for raw in ("", "abc", "0", "-5", "0.001", "nan", "inf", "1e999", None):
            with self.assertRaises(services.WalletError, msg=repr(raw)):
                services.withdraw(self.user, raw)
        self.assertEqual(self.balance(), D("50.00"))

    def test_extra_decimals_are_rounded_down_never_up(self):
        entry = services.withdraw(self.user, "12.999")
        self.assertEqual(entry.amount, D("-12.99"))

    def test_several_withdrawals_add_up(self):
        services.withdraw(self.user, "10")
        services.withdraw(self.user, "15.25")
        self.assertEqual(self.balance(), D("24.75"))
        totals = services.wallet_totals(Account.objects.get(user=self.user))
        self.assertEqual(totals["withdrawn"], D("25.25"))
        self.assertEqual(totals["earned"], D("50.00"))

    def test_a_user_with_no_wallet_gets_a_clear_error(self):
        stranger = make_user("stranger")
        with self.assertRaises(services.WalletError):
            services.withdraw(stranger, "1")


# ===========================================================================
# 4. Courier and packer pay
# ===========================================================================


class PaidWorkTests(DeliveryTestCase):
    def test_courier_gets_a_flat_20_per_trip_from_the_platform(self):
        order, [so] = self.order_with_stores(1)
        claim = self.deliver_pickup(so)
        self.assertEqual(claim.fee_earned, D("20.00"))
        self.assertEqual(services.wallet_balance(self.courier), D("20.00"))
        self.assertEqual(services.platform_account().balance, D("-20.00"))

    def test_pickup_and_dropoff_are_two_separate_trips(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)  # store -> warehouse (courier)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim = self.claim(dropoff, self.courier2)  # warehouse -> shopper (courier2)
        self.pick_up(claim, self.courier2)
        self.complete(claim, self.courier2)
        self.assertEqual(services.wallet_balance(self.courier), D("20.00"))
        self.assertEqual(services.wallet_balance(self.courier2), D("20.00"))

    def test_one_courier_doing_both_legs_earns_both(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim = self.claim(dropoff)
        self.pick_up(claim)
        self.complete(claim)
        self.assertEqual(services.wallet_balance(self.courier), D("40.00"))

    def test_paying_the_same_claim_twice_is_a_no_op(self):
        order, [so] = self.order_with_stores(1)
        claim = self.deliver_pickup(so)
        self.assertIsNone(services.pay_courier(claim))
        self.assertEqual(services.wallet_balance(self.courier), D("20.00"))
        self.assertEqual(LedgerEntry.objects.filter(kind="delivery_trip", account__kind="user").count(), 1)

    def test_a_claim_with_no_fee_pays_nothing(self):
        order, [so] = self.order_with_stores(1)
        claim = self.deliver_pickup(so)
        LedgerEntry.objects.all().delete()
        claim.fee_earned = None
        self.assertIsNone(services.pay_courier(claim))

    def test_warehouse_worker_gets_10_per_package_from_the_platform(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.pack(order)
        self.assertEqual(services.wallet_balance(self.staff), D("10.00"))
        entry = LedgerEntry.objects.get(account__user=self.staff)
        self.assertEqual(entry.kind, "packing")
        self.assertEqual(entry.order_id, order.pk)

    def test_each_order_is_one_package_however_many_stores(self):
        order, sos = self.order_with_stores(3)
        for so in sos:
            self.deliver_pickup(so)
        self.pack(order)
        self.assertEqual(services.wallet_balance(self.staff), D("10.00"))

    def test_paying_the_same_packing_claim_twice_is_a_no_op(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        claim = self.pack(order)
        self.assertIsNone(services.pay_packer(claim))
        self.assertEqual(services.wallet_balance(self.staff), D("10.00"))

    def test_an_unfinished_or_released_packing_claim_pays_nothing(self):
        from warehouse import services as wh_services
        from warehouse.models import WarehouseTask

        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        task = WarehouseTask.objects.get(order=order)
        claim, _ = wh_services.claim_task(task_id=task.pk, user=self.staff)
        wh_services.release_claim(claim_id=claim.pk, user=self.staff)
        self.assertEqual(services.wallet_balance(self.staff), D("0.00"))


# ===========================================================================
# 5. Settlement: 1% to the platform, the rest to the store, commission to staff
# ===========================================================================


class SettleOrderTests(DeliveryTestCase):
    def deliver_everything(self, order, sos):
        for so in sos:
            self.deliver_pickup(so)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        result, error = self.complete(claim, self.courier2)
        self.assertIsNone(error)
        return dropoff

    def test_nothing_is_settled_before_delivery(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.pack(order)
        self.assertFalse(OrderSettlement.objects.exists())
        self.assertEqual(Account.objects.filter(kind="store").count(), 0)

    def test_one_percent_goes_to_the_platform_and_the_rest_to_the_store(self):
        order, [so] = self.order_with_stores(1)  # 2 x $50 = $100
        self.deliver_everything(order, [so])
        settlement = OrderSettlement.objects.get(order=order)
        self.assertEqual(settlement.gross_sales, D("100.00"))
        self.assertEqual(settlement.platform_fee, D("1.00"))
        self.assertEqual(settlement.shipping_fee, D("50.00"))
        treasury = Account.objects.get(store=so.store)
        self.assertEqual(treasury.balance, D("99.00"))
        fee_entry = LedgerEntry.objects.get(account__kind="platform", kind="platform_fee")
        self.assertEqual(fee_entry.amount, D("1.00"))
        self.assertEqual(fee_entry.order_id, order.pk)

    def test_multi_store_order_takes_one_percent_from_each_store(self):
        order, sos = self.order_with_stores(2)
        self.deliver_everything(order, sos)
        settlement = OrderSettlement.objects.get(order=order)
        self.assertEqual(settlement.gross_sales, D("200.00"))
        self.assertEqual(settlement.platform_fee, D("2.00"))
        self.assertEqual(settlement.shipping_fee, shipping_fee(2))
        for so in sos:
            self.assertEqual(Account.objects.get(store=so.store).balance, D("99.00"))

    def test_settling_twice_changes_nothing(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_everything(order, [so])
        before = LedgerEntry.objects.count()
        again = services.settle_order(order.pk)
        self.assertEqual(again, OrderSettlement.objects.get(order=order))
        self.assertEqual(LedgerEntry.objects.count(), before)
        self.assertEqual(OrderSettlement.objects.count(), 1)

    def test_unknown_or_undelivered_orders_are_not_settled(self):
        self.assertIsNone(services.settle_order(999999))
        order, [so] = self.order_with_stores(1)
        self.assertIsNone(services.settle_order(order.pk))
        self.assertFalse(OrderSettlement.objects.exists())

    def test_percentage_helper_earns_commission_on_the_net_sales_of_orders_they_finished(self):
        order, [so] = self.order_with_stores(1)
        helper = make_user("helper")
        membership = member(helper, so.store, "helper", "percentage", D("10.00"))
        claim = OrderClaim.objects.create(
            store_order=so, membership=membership, claimant=helper,
            status="finished", deadline=timezone.now() + timedelta(hours=1),
        )
        self.deliver_everything(order, [so])
        # 10% of the $99.00 left after the platform's 1%
        self.assertEqual(services.wallet_balance(helper), D("9.90"))
        self.assertEqual(Account.objects.get(store=so.store).balance, D("89.10"))
        claim.refresh_from_db()
        self.assertEqual(claim.wage_earned, D("9.90"))
        entry = LedgerEntry.objects.get(account__user=helper)
        self.assertEqual((entry.kind, entry.store_id), ("commission", so.store_id))

    def test_salaried_staff_get_no_per_order_commission(self):
        order, [so] = self.order_with_stores(1)
        helper = make_user("helper")
        membership = member(helper, so.store, "helper", "salary", D("500.00"))
        OrderClaim.objects.create(
            store_order=so, membership=membership, claimant=helper,
            status="finished", deadline=timezone.now() + timedelta(hours=1),
        )
        self.deliver_everything(order, [so])
        self.assertEqual(services.wallet_balance(helper), D("0.00"))
        self.assertEqual(Account.objects.get(store=so.store).balance, D("99.00"))

    def test_released_and_expired_claims_earn_no_commission(self):
        order, [so] = self.order_with_stores(1)
        helper = make_user("helper")
        membership = member(helper, so.store, "helper", "percentage", D("10.00"))
        for status in ("released", "expired"):
            OrderClaim.objects.create(
                store_order=so, membership=membership, claimant=helper,
                status=status, deadline=timezone.now() + timedelta(hours=1),
            )
        self.deliver_everything(order, [so])
        self.assertEqual(services.wallet_balance(helper), D("0.00"))

    def test_a_hundred_percent_commission_leaves_the_treasury_at_zero_not_negative(self):
        order, [so] = self.order_with_stores(1)
        helper = make_user("helper")
        membership = member(helper, so.store, "manager", "percentage", D("100.00"))
        OrderClaim.objects.create(
            store_order=so, membership=membership, claimant=helper,
            status="finished", deadline=timezone.now() + timedelta(hours=1),
        )
        self.deliver_everything(order, [so])
        self.assertEqual(services.wallet_balance(helper), D("99.00"))
        self.assertEqual(Account.objects.get(store=so.store).balance, D("0.00"))

    def test_commission_rounds_to_cents(self):
        order, [so] = self.order_with_stores(1)
        helper = make_user("helper")
        membership = member(helper, so.store, "helper", "percentage", D("33.33"))
        OrderClaim.objects.create(
            store_order=so, membership=membership, claimant=helper,
            status="finished", deadline=timezone.now() + timedelta(hours=1),
        )
        self.deliver_everything(order, [so])
        self.assertEqual(services.wallet_balance(helper), D("33.00"))  # 99 * 33.33% = 32.9967 -> 33.00

    def test_a_cancelled_store_order_is_not_charged(self):
        order, [so1, so2] = self.order_with_stores(2)
        StoreOrder.objects.filter(pk=so2.pk).update(status="Cancelled")
        self.deliver_everything(order, [so1])
        settlement = OrderSettlement.objects.get(order=order)
        self.assertEqual(settlement.gross_sales, D("100.00"))
        self.assertEqual(settlement.shipping_fee, shipping_fee(1))
        self.assertFalse(Account.objects.filter(store=so2.store).exists())


# ===========================================================================
# 6. The big guarantees: conservation and platform solvency
# ===========================================================================


class MoneyIntegrityTests(DeliveryTestCase):
    def run_order(self, stores, commission=None):
        order, sos = self.order_with_stores(stores)
        if commission:
            helper = make_user(f"helper-{order.pk}")
            membership = member(helper, sos[0].store, "helper", "percentage", commission)
            OrderClaim.objects.create(
                store_order=sos[0], membership=membership, claimant=helper,
                status="finished", deadline=timezone.now() + timedelta(hours=1),
            )
        for so in sos:
            self.deliver_pickup(so)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        self.complete(claim, self.courier2)
        return order, dropoff

    def total_in_system(self):
        return sum(Account.objects.values_list("balance", flat=True))

    def test_all_balances_add_up_to_exactly_the_cash_the_courier_collected(self):
        order, dropoff = self.run_order(2, commission=D("12.50"))
        self.assertEqual(self.total_in_system(), dropoff.cod_amount)

    def test_the_platform_ends_with_exactly_its_one_percent(self):
        order, dropoff = self.run_order(1)
        platform = services.platform_account()
        platform.refresh_from_db()
        self.assertEqual(platform.balance, D("1.00"))

    def test_many_orders_never_leave_the_platform_negative(self):
        # varied sizes, store counts and prices, including awkward rounding
        shapes = [(1, D("50.00"), 2), (2, D("19.99"), 3), (3, D("7.33"), 1), (1, D("1.00"), 1), (2, D("333.33"), 4)]
        expected_fees = D("0.00")
        for stores, price, qty in shapes:
            order = self.new_order()
            sos = [self.add_store_order(order, price=price, qty=qty, product_name=f"p{i}") for i in range(stores)]
            expected_fees += sum(platform_fee(price * qty) for _ in sos)
            for so in sos:
                self.deliver_pickup(so)
            self.pack(order)
            dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
            claim = self.claim(dropoff, self.courier2)
            self.pick_up(claim, self.courier2)
            self.complete(claim, self.courier2)
            platform = services.platform_account()
            platform.refresh_from_db()
            self.assertGreaterEqual(platform.balance, 0)  # after EVERY delivered order
        platform = services.platform_account()
        platform.refresh_from_db()
        self.assertEqual(platform.balance, expected_fees)
        self.assertEqual(OrderSettlement.objects.count(), len(shapes))

    def test_platform_only_dips_while_an_order_is_in_transit_and_recovers(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        summary = services.platform_summary()
        self.assertEqual(summary["balance"], D("-30.00"))  # pickup $20 + packing $10 advanced
        self.assertEqual(summary["in_flight"], D("30.00"))
        self.assertEqual(summary["balance_after_in_flight"], D("0.00"))

        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        self.complete(claim, self.courier2)
        summary = services.platform_summary()
        self.assertEqual(summary["balance"], D("1.00"))
        self.assertEqual(summary["in_flight"], D("0.00"))

    def test_withdrawing_never_changes_what_the_platform_holds(self):
        self.run_order(1)
        platform_before = services.platform_account().balance
        services.withdraw(self.courier, "10")
        self.assertEqual(services.platform_account().balance, platform_before)

    def test_platform_summary_numbers(self):
        self.run_order(1)
        summary = services.platform_summary()
        self.assertEqual(summary["fees_total"], D("1.00"))
        self.assertEqual(summary["fees_month"], D("1.00"))
        self.assertEqual(summary["shipping_total"], D("50.00"))
        self.assertEqual(summary["courier_total"], D("40.00"))
        self.assertEqual(summary["packing_total"], D("10.00"))
        self.assertEqual(summary["month_net"], D("1.00"))
        self.assertEqual(summary["orders_settled"], 1)

    def test_warehouse_worker_payouts_report(self):
        order, dropoff = self.run_order(1)
        rows = list(services.warehouse_worker_payouts())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account__user__username"], "wh-staff")
        self.assertEqual((rows[0]["packages"], rows[0]["paid"]), (1, D("10.00")))
        self.assertEqual(list(services.warehouse_worker_payouts(since=date(2999, 1, 1))), [])


# ===========================================================================
# 7. Monthly payroll
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class PayrollTests(TestCase):
    def setUp(self):
        self.store = make_store("Corner Shop")
        self.treasury = services.store_account(self.store)
        self.owner_a, self.owner_b = make_user("owner-a"), make_user("owner-b")
        self.helper, self.manager = make_user("helper"), make_user("manager")
        self.period = date(2026, 4, 1)  # a 30-day month

    def staff(self):
        member(self.owner_a, self.store, "owner", "percentage", D("60.00"))
        member(self.owner_b, self.store, "owner", "percentage", D("40.00"))
        member(self.helper, self.store, "helper", "salary", D("30.00"))
        member(self.manager, self.store, "manager", "salary", D("20.00"))

    def bal(self, user):
        return services.wallet_balance(user)

    def test_salaries_come_first_then_owners_share_the_profit(self):
        self.staff()
        fund(self.treasury, "200.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual((run.salaries_due, run.salaries_paid), (D("50.00"), D("50.00")))
        self.assertEqual(run.profit_distributed, D("150.00"))
        self.assertEqual(self.bal(self.helper), D("30.00"))
        self.assertEqual(self.bal(self.manager), D("20.00"))
        self.assertEqual(self.bal(self.owner_a), D("90.00"))
        self.assertEqual(self.bal(self.owner_b), D("60.00"))
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.balance, D("0.00"))
        self.assertEqual(run.salary_shortfall, D("0.00"))

    def test_when_the_treasury_is_short_everyone_gets_the_same_fraction_and_owners_get_nothing(self):
        self.staff()
        fund(self.treasury, "25.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual((run.salaries_due, run.salaries_paid), (D("50.00"), D("25.00")))
        self.assertEqual(run.salary_shortfall, D("25.00"))
        self.assertEqual(self.bal(self.helper), D("15.00"))
        self.assertEqual(self.bal(self.manager), D("10.00"))
        self.assertEqual(self.bal(self.owner_a), D("0.00"))
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.balance, D("0.00"))

    def test_an_empty_treasury_pays_nobody_and_never_goes_negative(self):
        self.staff()
        run = services.run_payroll(self.store, self.period)
        self.assertEqual(run.salaries_paid, D("0.00"))
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.balance, D("0.00"))

    def test_a_month_can_only_be_paid_once(self):
        self.staff()
        fund(self.treasury, "200.00")
        self.assertIsNotNone(services.run_payroll(self.store, self.period))
        entries = LedgerEntry.objects.count()
        self.assertIsNone(services.run_payroll(self.store, self.period))
        self.assertEqual(LedgerEntry.objects.count(), entries)
        self.assertEqual(PayrollRun.objects.count(), 1)

    def test_any_day_of_the_month_means_that_month(self):
        self.staff()
        fund(self.treasury, "100.00")
        run = services.run_payroll(self.store, date(2026, 4, 17))
        self.assertEqual(run.period, date(2026, 4, 1))

    def test_owner_shares_round_down_and_the_cents_stay_in_the_treasury(self):
        for name in ("o1", "o2", "o3"):
            member(make_user(name), self.store, "owner", "percentage", D("33.33"))
        fund(self.treasury, "100.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual(run.profit_distributed, D("99.99"))
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.balance, D("0.01"))

    def test_unallocated_owner_percentage_stays_in_the_treasury(self):
        member(self.owner_a, self.store, "owner", "percentage", D("50.00"))
        fund(self.treasury, "100.00")
        services.run_payroll(self.store, self.period)
        self.assertEqual(self.bal(self.owner_a), D("50.00"))
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.balance, D("50.00"))

    def test_no_money_is_created_or_lost_by_payroll(self):
        self.staff()
        fund(self.treasury, "333.33")
        services.run_payroll(self.store, self.period)
        total = sum(Account.objects.values_list("balance", flat=True))
        self.assertEqual(total, D("333.33"))

    def test_someone_who_joined_mid_month_is_pro_rated_by_days(self):
        member(self.helper, self.store, "helper", "salary", D("100.00"), join_date=date(2026, 4, 16))
        fund(self.treasury, "500.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual(run.salaries_due, D("50.00"))  # 15 of 30 days
        self.assertEqual(self.bal(self.helper), D("50.00"))

    def test_someone_who_joined_before_the_month_gets_the_full_salary(self):
        member(self.helper, self.store, "helper", "salary", D("100.00"), join_date=date(2026, 1, 5))
        fund(self.treasury, "500.00")
        services.run_payroll(self.store, self.period)
        self.assertEqual(self.bal(self.helper), D("100.00"))

    def test_someone_who_joins_after_the_month_is_not_paid_for_it(self):
        member(self.helper, self.store, "helper", "salary", D("100.00"), join_date=date(2026, 5, 2))
        fund(self.treasury, "500.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual(run.salaries_due, D("0.00"))
        self.assertEqual(self.bal(self.helper), D("0.00"))

    def test_commission_staff_and_unset_salaries_are_not_on_payroll(self):
        member(self.helper, self.store, "helper", "percentage", D("10.00"))
        member(self.manager, self.store, "manager", "salary", None)
        fund(self.treasury, "100.00")
        run = services.run_payroll(self.store, self.period)
        self.assertEqual(run.salaries_due, D("0.00"))

    def test_ledger_descriptions_name_the_month(self):
        self.staff()
        fund(self.treasury, "200.00")
        services.run_payroll(self.store, self.period)
        entry = LedgerEntry.objects.get(account__user=self.helper)
        self.assertIn("April 2026", entry.description)
        self.assertEqual(entry.store_id, self.store.pk)

    # -- the automatic month-end trigger ------------------------------------

    def test_finished_months_are_paid_automatically_but_the_current_one_is_not(self):
        self.staff()
        fund(self.treasury, "200.00")
        self.assertEqual(services.close_due_payrolls(), 0)  # still the same month
        self.assertEqual(self.bal(self.owner_a), D("0.00"))
        self.assertEqual(services.close_due_payrolls(now=first_of_next_month_plus()), 1)
        self.assertEqual(self.bal(self.owner_a), D("90.00"))
        self.assertEqual(services.close_due_payrolls(now=first_of_next_month_plus()), 0)

    def test_every_missed_month_is_caught_up_once(self):
        self.staff()
        fund(self.treasury, "200.00")
        three_months_on = aware(services.next_month(services.next_month(services.next_month(timezone.now().date().replace(day=1)))) + timedelta(days=3))
        self.assertEqual(services.close_due_payrolls(now=three_months_on), 3)
        self.assertEqual(PayrollRun.objects.filter(store=self.store).count(), 3)
        self.assertEqual(services.close_due_payrolls(now=three_months_on), 0)

    def test_a_store_that_never_earned_anything_is_skipped(self):
        self.staff()
        services.close_due_payrolls(now=first_of_next_month_plus())
        self.assertEqual(PayrollRun.objects.count(), 0)

    def test_can_be_limited_to_some_stores(self):
        other = make_store("Other")
        fund(services.store_account(other), "50.00")
        fund(self.treasury, "50.00")
        done = services.close_due_payrolls(stores=[other], now=first_of_next_month_plus())
        self.assertEqual(done, 1)
        self.assertFalse(PayrollRun.objects.filter(store=self.store).exists())

    def test_run_payroll_now_pays_the_current_month_early(self):
        self.staff()
        fund(self.treasury, "200.00")
        self.assertEqual(services.run_payroll_now(), 1)
        self.assertEqual(self.bal(self.owner_a), D("90.00"))
        self.assertEqual(services.run_payroll_now(), 0)  # already paid

    def test_management_command(self):
        self.staff()
        fund(self.treasury, "200.00")
        call_command("run_payroll", stdout=StringIO())
        self.assertEqual(PayrollRun.objects.count(), 0)  # nothing finished yet
        call_command("run_payroll", "--current", stdout=StringIO())
        self.assertEqual(PayrollRun.objects.count(), 1)
        self.assertEqual(self.bal(self.owner_b), D("60.00"))


# ===========================================================================
# 8. Pages
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class WalletViewTests(TestCase):
    def setUp(self):
        self.user = make_user("member")
        self.wallet = services.user_account(self.user)
        self.url = reverse("payments:wallet")

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_new_member_sees_an_empty_wallet(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["account"].balance, D("0.00"))
        self.assertContains(response, "No money movements yet")

    def test_wallet_is_created_on_first_visit(self):
        newcomer = make_user("newcomer")
        self.client.force_login(newcomer)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertTrue(Account.objects.filter(user=newcomer).exists())

    def test_shows_balance_earned_withdrawn_and_history(self):
        fund(self.wallet, "80.00", kind="delivery_trip")
        services.withdraw(self.user, "30")
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["account"].balance, D("50.00"))
        self.assertEqual(response.context["totals"]["earned"], D("80.00"))
        self.assertEqual(response.context["totals"]["withdrawn"], D("30.00"))
        self.assertEqual(len(response.context["page_obj"]), 2)
        self.assertContains(response, "$50.00")
        self.assertContains(response, "Withdrawal")
        self.assertContains(response, "Delivery trip")

    def test_history_is_newest_first(self):
        fund(self.wallet, "10.00", kind="packing")
        fund(self.wallet, "20.00", kind="delivery_trip")
        self.client.force_login(self.user)
        amounts = [e.amount for e in self.client.get(self.url).context["page_obj"]]
        self.assertEqual(amounts, [D("20.00"), D("10.00")])

    def test_history_is_paginated(self):
        for _ in range(25):
            fund(self.wallet, "1.00")
        self.client.force_login(self.user)
        first = self.client.get(self.url)
        self.assertEqual(len(first.context["page_obj"]), 20)
        second = self.client.get(self.url + "?page=2")
        self.assertEqual(len(second.context["page_obj"]), 5)

    def test_members_only_ever_see_their_own_money(self):
        other = make_user("other")
        fund(services.user_account(other), "999.00")
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["account"].balance, D("0.00"))
        self.assertNotContains(response, "999.00")

    def test_withdraw_form_is_disabled_with_no_balance_and_enabled_with_some(self):
        self.client.force_login(self.user)
        self.assertRegex(
            self.client.get(self.url).content.decode(), r'<input[^>]*id="withdraw-amount"[^>]*disabled'
        )
        fund(self.wallet, "5.00")
        self.assertNotRegex(
            self.client.get(self.url).content.decode(), r'<input[^>]*id="withdraw-amount"[^>]*disabled'
        )

    def test_only_superusers_are_offered_the_platform_link(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(self.url), reverse("payments:platform"))
        root = make_user("root", is_staff=True, is_superuser=True)
        self.client.force_login(root)
        self.assertContains(self.client.get(self.url), reverse("payments:platform"))

    def test_opening_the_wallet_pays_a_finished_months_payroll(self):
        store = make_store()
        member(self.user, store, "owner", "percentage", D("100.00"))
        fund(services.store_account(store), "100.00")
        # pretend the money was earned last month
        LedgerEntry.objects.update(created_at=timezone.now() - timedelta(days=45))
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["account"].balance, D("100.00"))


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class WithdrawViewTests(TestCase):
    def setUp(self):
        self.user = make_user("member")
        fund(services.user_account(self.user), "40.00")
        self.url = reverse("payments:withdraw")

    def messages(self, response):
        return [str(m) for m in response.wsgi_request._messages]

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.post(self.url, {"amount": "5"}).status_code, 302)
        self.assertEqual(services.wallet_balance(self.user), D("40.00"))

    def test_get_does_nothing(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("payments:wallet"), fetch_redirect_response=False)
        self.assertEqual(services.wallet_balance(self.user), D("40.00"))

    def test_withdrawing_subtracts_exactly_that_amount(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"amount": "15.50"})
        self.assertRedirects(response, reverse("payments:wallet"), fetch_redirect_response=False)
        self.assertEqual(services.wallet_balance(self.user), D("24.50"))
        self.assertTrue(any("15.50" in m for m in self.messages(response)))

    def test_over_withdrawing_is_refused(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"amount": "40.01"})
        self.assertEqual(services.wallet_balance(self.user), D("40.00"))
        self.assertTrue(any("available balance" in m for m in self.messages(response)))

    def test_garbage_is_refused(self):
        self.client.force_login(self.user)
        for raw in ("", "abc", "-3", "0"):
            self.client.post(self.url, {"amount": raw})
        self.client.post(self.url, {})
        self.assertEqual(services.wallet_balance(self.user), D("40.00"))

    def test_you_can_only_withdraw_from_your_own_wallet(self):
        victim = make_user("victim")
        fund(services.user_account(victim), "500.00")
        self.client.force_login(self.user)
        self.client.post(self.url, {"amount": "100", "user": victim.pk})
        self.assertEqual(services.wallet_balance(victim), D("500.00"))
        self.assertEqual(services.wallet_balance(self.user), D("40.00"))


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class PlatformViewTests(TestCase):
    def setUp(self):
        self.url = reverse("payments:platform")
        self.root = make_user("root", is_staff=True, is_superuser=True)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_ordinary_members_are_forbidden(self):
        self.client.force_login(make_user("member"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_who_are_not_superusers_are_forbidden(self):
        self.client.force_login(make_user("clerk", is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_superuser_sees_the_platform_account(self):
        self.client.force_login(self.root)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform account")
        self.assertContains(response, "No order has been delivered yet.")

    def test_a_negative_balance_is_shown_not_hidden(self):
        platform = services.platform_account()
        Account.objects.filter(pk=platform.pk).update(balance=D("-12.00"))
        self.client.force_login(self.root)
        self.assertContains(self.client.get(self.url), "&minus;$12.00")

    def test_run_payroll_button_requires_a_superuser_and_post(self):
        run_url = reverse("payments:run_payroll_now")
        self.client.force_login(make_user("member"))
        self.assertEqual(self.client.post(run_url).status_code, 403)
        self.client.force_login(self.root)
        self.assertRedirects(self.client.get(run_url), self.url, fetch_redirect_response=False)

    def test_run_payroll_button_pays_every_store(self):
        store = make_store()
        owner = make_user("the-owner")
        member(owner, store, "owner", "percentage", D("100.00"))
        fund(services.store_account(store), "70.00")
        self.client.force_login(self.root)
        response = self.client.post(reverse("payments:run_payroll_now"))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(services.wallet_balance(owner), D("70.00"))
        # clicking again has nothing left to do
        self.client.post(reverse("payments:run_payroll_now"))
        self.assertEqual(PayrollRun.objects.count(), 1)

    def test_admin_pages_render_and_are_read_only(self):
        wallet = services.user_account(make_user("w"))
        fund(wallet, "5.00")
        self.client.force_login(self.root)
        for name in ("account", "ledgerentry", "ordersettlement", "payrollrun"):
            self.assertEqual(self.client.get(reverse(f"admin:payments_{name}_changelist")).status_code, 200, name)
            self.assertEqual(self.client.get(reverse(f"admin:payments_{name}_add")).status_code, 403, name)


class PlatformPageWithOrdersTests(DeliveryTestCase):
    def test_superuser_sees_the_one_percent_per_order_and_worker_payouts(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.pack(order)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        self.complete(claim, self.courier2)

        self.client.force_login(make_user("root", is_staff=True, is_superuser=True))
        response = self.client.get(reverse("payments:platform"))
        self.assertEqual(response.context["summary"]["fees_total"], D("1.00"))
        self.assertEqual(response.context["summary"]["balance"], D("1.00"))
        self.assertEqual([x.order_id for x in response.context["settlements"]], [order.pk])
        self.assertContains(response, f"#{order.pk}")
        self.assertContains(response, "wh-staff")  # the warehouse worker who was paid
        self.assertContains(response, "+$1.00")  # the 1% fee on that order

    def test_in_transit_orders_explain_the_temporary_dip(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.client.force_login(make_user("root", is_staff=True, is_superuser=True))
        response = self.client.get(reverse("payments:platform"))
        self.assertContains(response, "advanced for orders still in transit")
        self.assertEqual(response.context["summary"]["balance"], D("-20.00"))


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class StoreOverviewMoneyTests(TestCase):
    def setUp(self):
        self.store = make_store("Overview Shop")
        self.owner = make_user("owner")
        member(self.owner, self.store, "owner", "percentage", D("100.00"))
        self.helper = make_user("helper")
        member(self.helper, self.store, "helper", "percentage", D("10.00"))
        self.manager = make_user("manager")
        member(self.manager, self.store, "manager", "salary", D("500.00"))
        self.url = reverse("fulfillment:store_overview", args=[self.store.pk])

    def test_shows_what_each_staff_member_earned_at_this_store_and_the_treasury(self):
        fund(services.user_account(self.helper), "25.00", kind="commission")
        LedgerEntry.objects.filter(account__user=self.helper).update(store=self.store)
        other = make_store("Other")  # earnings at another store must not leak in
        fund(services.user_account(self.helper), "99.00", kind="commission")
        LedgerEntry.objects.filter(account__user=self.helper, store__isnull=True).update(store=other)
        fund(services.store_account(self.store), "300.00")

        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        earned = {m.user.username: m.earned_here for m in response.context["staff_wallets"]}
        self.assertEqual(earned, {"helper": D("25.00"), "manager": D("0.00")})
        self.assertEqual([m.user.username for m in response.context["staff_wallets"]], ["helper", "manager"])
        self.assertEqual(response.context["treasury_balance"], D("300.00"))

    def test_treasury_defaults_to_zero_and_payroll_history_is_listed(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).context["treasury_balance"], D("0.00"))
        PayrollRun.objects.create(store=self.store, period=date(2026, 3, 1), salaries_due=D("500"), salaries_paid=D("200"))
        response = self.client.get(self.url)
        self.assertContains(response, "March 2026")
        self.assertContains(response, "of $500.00 due")


# ===========================================================================
# 9. Checkout: the shopper pays the delivery & handling fee
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class CheckoutFeeTests(TestCase):
    def setUp(self):
        self.shopper = make_user("buyer")
        self.client.force_login(self.shopper)
        self.cart = Cart.objects.get(user=self.shopper)

    def add(self, store, price="100.00", qty=1):
        product = make_product(store, price=D(price))
        product.current_stock = 10
        product.save()
        CartItem.objects.create(cart=self.cart, product=product, quantity=qty)
        self.cart.total_price = (self.cart.total_price or D("0")) + D(price) * qty
        self.cart.save()

    def test_one_store_checkout_shows_the_fee_and_total(self):
        self.add(make_store())
        response = self.client.get(reverse("checkout"))
        self.assertEqual(response.context["shipping_fee"], shipping_fee(1))
        self.assertEqual(response.context["total"], D("100.00") + shipping_fee(1))
        self.assertContains(response, "Delivery &amp; handling")
        self.assertNotContains(response, "Free")

    def test_fee_grows_with_the_number_of_stores_not_items(self):
        store_a, store_b = make_store(), make_store()
        self.add(store_a)
        self.add(store_a, qty=3)
        self.add(store_b)
        response = self.client.get(reverse("checkout"))
        self.assertEqual(response.context["shipping_fee"], shipping_fee(2))

    def test_placing_the_order_stores_products_plus_fee(self):
        store_a, store_b = make_store(), make_store()
        self.add(store_a)
        self.add(store_b, price="50.00")
        response = self.client.post(
            reverse("place_order"), {"shipping_address": "1 Nile St, Cairo", "payment_type": "cash"}
        )
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.total_price, D("150.00") + shipping_fee(2))
        self.assertEqual(order.store_orders.count(), 2)

    def test_the_courier_will_be_asked_for_exactly_what_checkout_showed(self):
        self.add(make_store())
        self.client.post(reverse("place_order"), {"shipping_address": "1 Nile St, Cairo", "payment_type": "cash"})
        order = Order.objects.get()
        from delivery import services as delivery_services

        cod = delivery_services._cod_total(order, list(order.store_orders.all()))
        self.assertEqual(cod, order.total_price)
