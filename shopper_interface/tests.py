"""
Test suite for the `shopper_interface` app.

Covers:
    - Models: Cart, CartItem, Wishlist, WishlistItem, RecentlyViewed,
      StoreFollow, Interaction, RecentSearch, SearchTrend
    - Forms: CartItemForm (including its server-side stock cap)
    - Views: home, home_feed_page, quick_add_to_cart, quick_add_to_wishlist,
      update_cart_item, remove_cart_item, Cart_view, Wishlist_view,
      search_view, search_results_page

NOTE: this app currently duplicates Cart/CartItem/Wishlist/WishlistItem with
`orders`, and users/models.py + shopper_interface/signals.py still import
Cart/CartItem/WishlistItem from `orders.models`. Until those imports point at
`shopper_interface.models`, User creation (and therefore setUpTestData below)
will raise ImportError. See chat notes.

`get_home_feed_page` and `get_frequently_bought_together` are patched by
their import path *inside this views module* (not their defining module),
per the standard mock.patch rule for functions imported with `from x import y`.

Run with:
    python manage.py test shopper_interface
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from merchant_interface.models import Niche, Store
from products.models import Category, Product
from shopper_interface.forms import CartItemForm
from shopper_interface.models import (Cart, CartItem, Interaction,
                                       RecentlyViewed, RecentSearch,
                                       SearchTrend, StoreFollow, Wishlist,
                                       WishlistItem)


class ShopperInterfaceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="shopper", password="strongpass123", email="shopper@example.com"
        )
        cls.other_user = User.objects.create_user(
            username="other", password="strongpass123", email="other@example.com"
        )

        cls.niche = Niche.objects.create(name="Electronics")
        cls.store = Store.objects.create(name="Test Store", niche=cls.niche)
        cls.disabled_store = Store.objects.create(
            name="Disabled Store", niche=cls.niche, enabled=False
        )
        cls.category = Category.objects.create(name="Laptops")

        cls.product = Product.objects.create(
            name="ThinkPad X1",
            category=cls.category,
            store=cls.store,
            selling_price=Decimal("100.00"),
            current_stock=10,
        )
        cls.product2 = Product.objects.create(
            name="Mouse",
            category=cls.category,
            store=cls.store,
            selling_price=Decimal("20.00"),
            current_stock=5,
        )
        cls.discounted_product = Product.objects.create(
            name="Headphones",
            category=cls.category,
            store=cls.store,
            selling_price=Decimal("100.00"),
            offer=Decimal("20.00"),  # -> discounted_price == 80.00
            current_stock=10,
        )
        cls.out_of_stock_product = Product.objects.create(
            name="Keyboard",
            category=cls.category,
            store=cls.store,
            selling_price=Decimal("50.00"),
            current_stock=0,
        )
        cls.inactive_product = Product.objects.create(
            name="Old Monitor",
            category=cls.category,
            store=cls.store,
            selling_price=Decimal("30.00"),
            current_stock=10,
            is_active=False,
        )
        cls.disabled_store_product = Product.objects.create(
            name="Discontinued Item",
            category=cls.category,
            store=cls.disabled_store,
            selling_price=Decimal("40.00"),
            current_stock=10,
        )

    def login(self):
        self.client.login(username="shopper", password="strongpass123")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CartModelTests(ShopperInterfaceTestBase):
    def test_cart_auto_created_for_new_user(self):
        # A post_save signal on User creates a Cart automatically (requires
        # users/models.py to import Cart from shopper_interface.models).
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_cart_total_price_defaults_to_zero(self):
        cart = Cart.objects.get(user=self.user)
        self.assertEqual(cart.total_price, Decimal("0.00"))

    def test_cart_str(self):
        cart = Cart.objects.get(user=self.user)
        self.assertEqual(str(cart), f"{self.user}'s cart")

    def test_user_can_only_have_one_cart(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cart.objects.create(user=self.user)


class CartItemModelTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.cart = Cart.objects.get(user=self.user)

    def test_cart_item_quantity_defaults_to_one(self):
        item = CartItem.objects.create(cart=self.cart, product=self.product)
        self.assertEqual(item.quantity, 1)

    def test_cart_item_str(self):
        item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=2
        )
        self.assertEqual(str(item), f"2 x {self.product} ({self.cart.user})")

    def test_same_product_cannot_be_added_twice_to_same_cart(self):
        CartItem.objects.create(cart=self.cart, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CartItem.objects.create(cart=self.cart, product=self.product)

    def test_same_product_allowed_in_different_carts(self):
        other_cart = Cart.objects.get(user=self.other_user)
        CartItem.objects.create(cart=self.cart, product=self.product)
        item = CartItem.objects.create(cart=other_cart, product=self.product)
        self.assertEqual(item.product, self.product)

    def test_deleting_cart_cascades_to_items(self):
        item = CartItem.objects.create(cart=self.cart, product=self.product)
        self.cart.delete()
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())

    def test_deleting_product_in_a_cart_is_protected(self):
        CartItem.objects.create(cart=self.cart, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.product.delete()

    def test_cart_item_product_can_be_null(self):
        # AbstractCollectionItem allows a null product; update_cart_item /
        # Cart_view both have to tolerate this without crashing.
        item = CartItem.objects.create(cart=self.cart, product=None, quantity=1)
        self.assertIsNone(item.product)


class WishlistModelTests(ShopperInterfaceTestBase):
    def test_wishlist_str(self):
        wishlist = Wishlist.objects.create(user=self.user)
        self.assertEqual(str(wishlist), f"{self.user}'s wishlist")

    def test_user_can_only_have_one_wishlist(self):
        Wishlist.objects.create(user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wishlist.objects.create(user=self.user)


class WishlistItemModelTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.wishlist = Wishlist.objects.create(user=self.user)

    def test_wishlist_item_str(self):
        item = WishlistItem.objects.create(
            wishlist=self.wishlist, product=self.product
        )
        self.assertEqual(str(item), f"{self.product} ({self.wishlist.user})")

    def test_same_product_cannot_be_added_twice_to_same_wishlist(self):
        WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WishlistItem.objects.create(
                    wishlist=self.wishlist, product=self.product
                )

    def test_deleting_wishlist_cascades_to_items(self):
        item = WishlistItem.objects.create(
            wishlist=self.wishlist, product=self.product
        )
        self.wishlist.delete()
        self.assertFalse(WishlistItem.objects.filter(id=item.id).exists())

    def test_deleting_product_in_a_wishlist_is_protected(self):
        WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.product.delete()


class RecentlyViewedModelTests(ShopperInterfaceTestBase):
    def test_str(self):
        rv = RecentlyViewed.objects.create(user=self.user, product=self.product)
        self.assertEqual(str(rv), f"{self.user} viewed {self.product}")

    def test_unique_per_user_and_product(self):
        RecentlyViewed.objects.create(user=self.user, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecentlyViewed.objects.create(user=self.user, product=self.product)

    def test_ordered_most_recent_first(self):
        first = RecentlyViewed.objects.create(user=self.user, product=self.product)
        second = RecentlyViewed.objects.create(user=self.user, product=self.product2)
        ordered = list(RecentlyViewed.objects.filter(user=self.user))
        self.assertEqual(ordered, [second, first])

    def test_user_is_set_null_when_user_deleted(self):
        rv = RecentlyViewed.objects.create(user=self.other_user, product=self.product)
        self.other_user.delete()
        rv.refresh_from_db()
        self.assertIsNone(rv.user)

    def test_product_is_set_null_when_product_deleted(self):
        throwaway = Product.objects.create(
            name="Temp", category=self.category, store=self.store,
            selling_price=Decimal("5.00"),
        )
        rv = RecentlyViewed.objects.create(user=self.user, product=throwaway)
        throwaway.delete()
        rv.refresh_from_db()
        self.assertIsNone(rv.product)


class StoreFollowModelTests(ShopperInterfaceTestBase):
    def test_str(self):
        follow = StoreFollow.objects.create(user=self.user, store=self.store)
        self.assertEqual(str(follow), f"{self.user} follows {self.store}")

    def test_unique_per_user_and_store(self):
        StoreFollow.objects.create(user=self.user, store=self.store)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StoreFollow.objects.create(user=self.user, store=self.store)

    def test_same_user_can_follow_different_stores(self):
        StoreFollow.objects.create(user=self.user, store=self.store)
        follow2 = StoreFollow.objects.create(user=self.user, store=self.disabled_store)
        self.assertEqual(StoreFollow.objects.filter(user=self.user).count(), 2)
        self.assertEqual(follow2.store, self.disabled_store)


class InteractionModelTests(ShopperInterfaceTestBase):
    def test_create_interaction(self):
        interaction = Interaction.objects.create(
            user=self.user, product=self.product, action="view", weight=1
        )
        self.assertEqual(interaction.action, "view")

    def test_deleting_user_cascades(self):
        interaction = Interaction.objects.create(
            user=self.other_user, product=self.product, action="cart", weight=5
        )
        self.other_user.delete()
        self.assertFalse(Interaction.objects.filter(id=interaction.id).exists())

    def test_deleting_product_cascades(self):
        throwaway = Product.objects.create(
            name="Temp2", category=self.category, store=self.store,
            selling_price=Decimal("5.00"),
        )
        interaction = Interaction.objects.create(
            user=self.user, product=throwaway, action="view", weight=1
        )
        throwaway.delete()
        self.assertFalse(Interaction.objects.filter(id=interaction.id).exists())


class RecentSearchModelTests(ShopperInterfaceTestBase):
    def test_unique_per_user_and_normalized_query(self):
        RecentSearch.objects.create(
            user=self.user, query_text="Laptops", normalized_query="laptops"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecentSearch.objects.create(
                    user=self.user, query_text="LAPTOPS", normalized_query="laptops"
                )

    def test_ordered_most_recent_first(self):
        first = RecentSearch.objects.create(
            user=self.user, query_text="mouse", normalized_query="mouse"
        )
        second = RecentSearch.objects.create(
            user=self.user, query_text="keyboard", normalized_query="keyboard"
        )
        ordered = list(RecentSearch.objects.filter(user=self.user))
        self.assertEqual(ordered, [second, first])


class SearchTrendModelTests(ShopperInterfaceTestBase):
    def test_unique_normalized_query(self):
        SearchTrend.objects.create(
            normalized_query="laptops", display_query="Laptops", hit_count=5
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SearchTrend.objects.create(
                    normalized_query="laptops", display_query="Laptops", hit_count=1
                )

    def test_ordered_by_hit_count_desc(self):
        low = SearchTrend.objects.create(
            normalized_query="mouse", display_query="Mouse", hit_count=2
        )
        high = SearchTrend.objects.create(
            normalized_query="laptops", display_query="Laptops", hit_count=50
        )
        self.assertEqual(list(SearchTrend.objects.all()), [high, low])


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class CartItemFormTests(ShopperInterfaceTestBase):
    def test_form_only_exposes_quantity_field(self):
        form = CartItemForm(product=self.product)
        self.assertEqual(list(form.fields.keys()), ["quantity"])

    def test_valid_with_quantity_within_stock(self):
        form = CartItemForm(data={"quantity": 3}, product=self.product)
        self.assertTrue(form.is_valid())

    def test_invalid_with_zero_or_negative_quantity(self):
        form = CartItemForm(data={"quantity": 0}, product=self.product)
        self.assertFalse(form.is_valid())
        form = CartItemForm(data={"quantity": -1}, product=self.product)
        self.assertFalse(form.is_valid())

    def test_invalid_when_quantity_exceeds_stock(self):
        # self.product has current_stock=10
        form = CartItemForm(data={"quantity": 11}, product=self.product)
        self.assertFalse(form.is_valid())
        self.assertIn("Only 10 left in stock.", form.errors["quantity"])

    def test_valid_at_exactly_the_stock_boundary(self):
        form = CartItemForm(data={"quantity": 10}, product=self.product)
        self.assertTrue(form.is_valid())

    def test_falls_back_to_instance_product_when_none_passed_explicitly(self):
        cart = Cart.objects.get(user=self.user)
        item = CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        form = CartItemForm(data={"quantity": 999}, instance=item)
        self.assertFalse(form.is_valid())
        self.assertIn("Only 10 left in stock.", form.errors["quantity"])

    def test_no_stock_check_crash_when_product_is_none(self):
        # product=None (e.g. a stale/orphaned cart line) shouldn't raise —
        # getattr(None, "current_stock", None) is None, so the cap is skipped.
        form = CartItemForm(data={"quantity": 5}, product=None)
        self.assertTrue(form.is_valid())

    def test_blank_quantity_is_accepted_due_to_model_blank_true(self):
        # KNOWN QUIRK: CartItem.quantity has blank=True, so the ModelForm
        # field isn't required. An empty submission clears cleaned_data to
        # None, clean_quantity() short-circuits on None, and .save() would
        # persist quantity=None on an existing item. Flagging via this test
        # rather than silently relying on it.
        form = CartItemForm(data={}, product=self.product)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data.get("quantity"))


# ---------------------------------------------------------------------------
# Views: home / feed
# ---------------------------------------------------------------------------


class HomeViewTests(ShopperInterfaceTestBase):
    @patch("shopper_interface.views.get_home_feed_page")
    def test_home_renders_with_feed_context(self, mock_feed):
        mock_feed.return_value = {
            "products": [self.product, self.product2],
            "stores": [self.store],
            "has_more": True,
            "start": 0,
            "total": 20,
        }
        response = self.client.get(reverse("shopper_interface:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shopper_interface/home.html")
        self.assertEqual(response.context["recommended_products"], [self.product, self.product2])
        self.assertEqual(response.context["recommended_stores"], [self.store])
        self.assertTrue(response.context["has_more"])
        self.assertEqual(response.context["next_page"], 2)
        self.assertEqual(response.context["feed_start_index"], 0)
        self.assertEqual(response.context["feed_total_length"], 20)
        mock_feed.assert_called_once()
        args, kwargs = mock_feed.call_args
        self.assertEqual(kwargs.get("page", args[1] if len(args) > 1 else None), 1)

    @patch("shopper_interface.views.get_home_feed_page")
    def test_home_works_for_anonymous_user(self, mock_feed):
        mock_feed.return_value = {
            "products": [], "stores": [], "has_more": False, "start": 0, "total": 0,
        }
        response = self.client.get(reverse("shopper_interface:home"))
        self.assertEqual(response.status_code, 200)


class HomeFeedPageViewTests(ShopperInterfaceTestBase):
    @patch("shopper_interface.views.get_home_feed_page")
    def test_default_page_is_one(self, mock_feed):
        mock_feed.return_value = {
            "products": [], "stores": [], "has_more": False, "start": 0, "total": 0,
        }
        self.client.get(reverse("shopper_interface:home_feed_page"))
        _, kwargs = mock_feed.call_args
        self.assertEqual(kwargs.get("page"), 1)

    @patch("shopper_interface.views.get_home_feed_page")
    def test_uses_page_query_param(self, mock_feed):
        mock_feed.return_value = {
            "products": [], "stores": [], "has_more": True, "start": 20, "total": 50,
        }
        response = self.client.get(reverse("shopper_interface:home_feed_page"), {"page": 3})
        _, kwargs = mock_feed.call_args
        self.assertEqual(kwargs.get("page"), 3)
        self.assertEqual(response.context["next_page"], 4)
        self.assertTemplateUsed(response, "shopper_interface/_home_feed_page.html")

    @patch("shopper_interface.views.get_home_feed_page")
    def test_non_integer_page_falls_back_to_one(self, mock_feed):
        mock_feed.return_value = {
            "products": [], "stores": [], "has_more": False, "start": 0, "total": 0,
        }
        self.client.get(reverse("shopper_interface:home_feed_page"), {"page": "abc"})
        _, kwargs = mock_feed.call_args
        self.assertEqual(kwargs.get("page"), 1)

    @patch("shopper_interface.views.get_home_feed_page")
    def test_zero_or_negative_page_is_clamped_to_one(self, mock_feed):
        mock_feed.return_value = {
            "products": [], "stores": [], "has_more": False, "start": 0, "total": 0,
        }
        self.client.get(reverse("shopper_interface:home_feed_page"), {"page": -5})
        _, kwargs = mock_feed.call_args
        self.assertEqual(kwargs.get("page"), 1)


# ---------------------------------------------------------------------------
# Views: quick add (AJAX)
# ---------------------------------------------------------------------------


class QuickAddToCartViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.url = reverse(
            "shopper_interface:quick_add_to_cart", args=[self.product.id]
        )
        self.cart = Cart.objects.get(user=self.user)

    def test_requires_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        self.login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_product_not_found(self):
        self.login()
        bad_url = reverse("shopper_interface:quick_add_to_cart", args=[999999])
        response = self.client.post(bad_url)
        self.assertEqual(response.status_code, 404)

    def test_adds_new_item_with_quantity_one(self):
        self.login()
        response = self.client.post(self.url)
        data = response.json()
        self.assertTrue(data["success"])
        item = CartItem.objects.get(cart=self.cart, product=self.product)
        self.assertEqual(item.quantity, 1)

    def test_calling_again_increments_existing_item(self):
        self.login()
        self.client.post(self.url)
        self.client.post(self.url)
        item = CartItem.objects.get(cart=self.cart, product=self.product)
        self.assertEqual(item.quantity, 2)

    def test_out_of_stock_product_is_rejected_and_new_item_deleted(self):
        self.login()
        url = reverse(
            "shopper_interface:quick_add_to_cart", args=[self.out_of_stock_product.id]
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            CartItem.objects.filter(cart=self.cart, product=self.out_of_stock_product).exists()
        )

    def test_existing_item_at_stock_limit_is_not_deleted_or_changed_on_failed_add(self):
        self.login()
        # product2 has current_stock=5; pre-seed the item at 5
        item = CartItem.objects.create(cart=self.cart, product=self.product2, quantity=5)
        url = reverse("shopper_interface:quick_add_to_cart", args=[self.product2.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 5)  # unchanged, and NOT deleted
        self.assertTrue(CartItem.objects.filter(id=item.id).exists())


class QuickAddToWishlistViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.url = reverse(
            "shopper_interface:quick_add_to_wishlist", args=[self.product.id]
        )

    def test_requires_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        self.login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_product_not_found(self):
        self.login()
        bad_url = reverse("shopper_interface:quick_add_to_wishlist", args=[999999])
        response = self.client.post(bad_url)
        self.assertEqual(response.status_code, 404)

    def test_adds_new_item(self):
        self.login()
        response = self.client.post(self.url)
        data = response.json()
        self.assertTrue(data["success"])
        wishlist = Wishlist.objects.get(user=self.user)
        self.assertTrue(WishlistItem.objects.filter(wishlist=wishlist, product=self.product).exists())

    def test_calling_again_reports_already_present_without_duplicating(self):
        self.login()
        self.client.post(self.url)
        response = self.client.post(self.url)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("already in your wishlist", data["message"])
        wishlist = Wishlist.objects.get(user=self.user)
        self.assertEqual(
            WishlistItem.objects.filter(wishlist=wishlist, product=self.product).count(), 1
        )


# ---------------------------------------------------------------------------
# Views: update / remove cart item (AJAX)
# ---------------------------------------------------------------------------


class UpdateCartItemViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.cart = Cart.objects.get(user=self.user)
        self.item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        self.url = reverse("shopper_interface:update_cart_item", args=[self.item.id])

    def test_requires_login(self):
        response = self.client.post(self.url, {"quantity": 3})
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        self.login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_item_not_found_for_other_user(self):
        self.login()
        other_cart = Cart.objects.get(user=self.other_user)
        other_item = CartItem.objects.create(
            cart=other_cart, product=self.product2, quantity=1
        )
        url = reverse("shopper_interface:update_cart_item", args=[other_item.id])
        response = self.client.post(url, {"quantity": 2})
        self.assertEqual(response.status_code, 404)

    def test_invalid_quantity_string(self):
        self.login()
        response = self.client.post(self.url, {"quantity": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_quantity_below_one_rejected(self):
        self.login()
        response = self.client.post(self.url, {"quantity": 0})
        self.assertEqual(response.status_code, 400)

    def test_quantity_above_stock_rejected_with_max_quantity(self):
        self.login()
        response = self.client.post(self.url, {"quantity": 999})
        data = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["max_quantity"], 10)

    def test_successful_update_recalculates_totals(self):
        self.login()
        response = self.client.post(self.url, {"quantity": 5})
        data = response.json()
        self.assertTrue(data["success"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 5)
        self.assertEqual(data["line_subtotal"], 500.0)  # 5 * 100.00
        self.assertEqual(data["cart_subtotal"], 500.0)
        self.assertEqual(data["item_count"], 1)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.total_price, Decimal("500.00"))

    def test_uses_discounted_price_when_present(self):
        self.login()
        item = CartItem.objects.create(
            cart=self.cart, product=self.discounted_product, quantity=1
        )
        url = reverse("shopper_interface:update_cart_item", args=[item.id])
        response = self.client.post(url, {"quantity": 2})
        data = response.json()
        # discounted_price = 80.00; existing self.item (qty 2 @ 100) also
        # contributes 200 to the cart subtotal
        self.assertEqual(data["line_subtotal"], 160.0)
        self.assertEqual(data["cart_subtotal"], 360.0)

    def test_null_product_on_item_treated_as_zero_stock_no_crash(self):
        self.login()
        orphan = CartItem.objects.create(cart=self.cart, product=None, quantity=1)
        url = reverse("shopper_interface:update_cart_item", args=[orphan.id])
        response = self.client.post(url, {"quantity": 1})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get("max_quantity"), 0)


class RemoveCartItemViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.cart = Cart.objects.get(user=self.user)
        self.item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        self.url = reverse("shopper_interface:remove_cart_item", args=[self.item.id])

    def test_requires_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        self.login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_item_not_found_for_other_user(self):
        self.login()
        other_cart = Cart.objects.get(user=self.other_user)
        other_item = CartItem.objects.create(cart=other_cart, product=self.product2)
        url = reverse("shopper_interface:remove_cart_item", args=[other_item.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(CartItem.objects.filter(id=other_item.id).exists())

    def test_removes_item_and_recalculates_total(self):
        self.login()
        second_item = CartItem.objects.create(
            cart=self.cart, product=self.product2, quantity=1
        )
        response = self.client.post(self.url)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(CartItem.objects.filter(id=self.item.id).exists())
        self.assertEqual(data["item_count"], 1)
        self.assertEqual(data["cart_subtotal"], 20.0)  # only product2 left


# ---------------------------------------------------------------------------
# Views: Cart_view / Wishlist_view (full page)
# ---------------------------------------------------------------------------


class CartViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.url = reverse("shopper_interface:cart")
        self.cart = Cart.objects.get(user=self.user)

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_renders_cart_with_items(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shopper_interface/cart.html")
        self.assertContains(response, self.product.name)

    def test_total_uses_discounted_price(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.discounted_product, quantity=2)
        self.client.get(self.url)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.total_price, Decimal("160.00"))  # 2 * 80.00

    def test_get_clamps_item_quantity_to_available_stock(self):
        self.login()
        item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=999
        )
        self.client.get(self.url)
        item.refresh_from_db()
        self.assertEqual(item.quantity, self.product.current_stock)

    def test_inactive_product_flagged_with_error_message(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.inactive_product, quantity=1)
        response = self.client.get(self.url)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("no longer available" in m for m in messages))

    def test_inactive_product_still_counted_in_total_known_bug(self):
        # KNOWN BUG: the per-item loop flags inactive/disabled-store products
        # and tells the shopper they "won't be checked out", but the total
        # calculation right after doesn't exclude them. Documenting current
        # behavior here so a future fix shows up as an intentional test change.
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.inactive_product, quantity=1)
        self.client.get(self.url)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.total_price, Decimal("30.00"))

    def test_disabled_store_product_flagged(self):
        self.login()
        CartItem.objects.create(
            cart=self.cart, product=self.disabled_store_product, quantity=1
        )
        response = self.client.get(self.url)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("no longer available" in m for m in messages))

    def test_post_remove_btn_deletes_item(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(
            self.url, data={"item_id": item.id, "remove_btn": "1"}
        )
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_post_updates_item_quantity_via_form(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(self.url, data={"item_id": item.id, "quantity": 4})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_post_quantity_exceeding_stock_rejected_by_form(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(self.url, data={"item_id": item.id, "quantity": 999})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)  # unchanged
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Only 10 left in stock" in m for m in messages))

    def test_post_cannot_modify_another_users_cart_item(self):
        self.login()
        other_cart = Cart.objects.get(user=self.other_user)
        other_item = CartItem.objects.create(
            cart=other_cart, product=self.product, quantity=1
        )
        response = self.client.post(
            self.url, data={"item_id": other_item.id, "remove_btn": "1"}
        )
        self.assertTrue(CartItem.objects.filter(id=other_item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    @patch("shopper_interface.views.get_frequently_bought_together")
    def test_suggestions_exclude_items_already_in_cart(self, mock_fbt):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        mock_fbt.return_value = [self.product, self.product2]  # self.product already in cart
        response = self.client.get(self.url)
        self.assertIn(self.product2, response.context["suggestions"])
        self.assertNotIn(self.product, response.context["suggestions"])

    @patch("shopper_interface.views.get_frequently_bought_together")
    def test_suggestions_capped_at_six(self, mock_fbt):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        extra_products = [
            Product.objects.create(
                name=f"Extra {i}", category=self.category, store=self.store,
                selling_price=Decimal("10.00"), current_stock=5,
            )
            for i in range(10)
        ]
        mock_fbt.return_value = extra_products
        response = self.client.get(self.url)
        self.assertLessEqual(len(response.context["suggestions"]), 6)


class WishlistViewTests(ShopperInterfaceTestBase):
    def setUp(self):
        self.url = reverse("shopper_interface:wishlist")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_creates_wishlist_and_renders(self):
        self.login()
        self.assertFalse(Wishlist.objects.filter(user=self.user).exists())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shopper_interface/wishlist.html")
        self.assertTrue(Wishlist.objects.filter(user=self.user).exists())

    def test_post_remove_btn_deletes_item_and_redirects(self):
        self.login()
        wishlist, _ = Wishlist.objects.get_or_create(user=self.user)
        item = WishlistItem.objects.create(wishlist=wishlist, product=self.product)
        response = self.client.post(
            self.url, data={"item_id": item.id, "remove_btn": "1"}
        )
        self.assertFalse(WishlistItem.objects.filter(id=item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_post_cannot_remove_another_users_item(self):
        self.login()
        other_wishlist, _ = Wishlist.objects.get_or_create(user=self.other_user)
        other_item = WishlistItem.objects.create(
            wishlist=other_wishlist, product=self.product
        )
        response = self.client.post(
            self.url, data={"item_id": other_item.id, "remove_btn": "1"}
        )
        self.assertTrue(WishlistItem.objects.filter(id=other_item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# Views: search
# ---------------------------------------------------------------------------


class SearchViewTests(ShopperInterfaceTestBase):
    @patch("shopper_interface.views.search_catalog")
    def test_search_calls_catalog_with_stripped_query(self, mock_search):
        mock_search.return_value = {"results": [self.product], "has_more": False}
        response = self.client.get(reverse("shopper_interface:search"), {"q": "  laptop  "})
        mock_search.assert_called_once_with("laptop", page=1)
        self.assertEqual(response.context["query"], "laptop")
        self.assertEqual(list(response.context["results"]), [self.product])
        self.assertTemplateUsed(response, "shopper_interface/search.html")

    @patch("shopper_interface.views.search_catalog")
    def test_search_with_no_query_param(self, mock_search):
        mock_search.return_value = {"results": [], "has_more": False}
        response = self.client.get(reverse("shopper_interface:search"))
        mock_search.assert_called_once_with("", page=1)
        self.assertEqual(response.status_code, 200)


class SearchResultsPageViewTests(ShopperInterfaceTestBase):
    @patch("shopper_interface.views.search_catalog")
    def test_uses_page_query_param(self, mock_search):
        mock_search.return_value = {"results": [], "has_more": True}
        response = self.client.get(
            reverse("shopper_interface:search_results_page"), {"q": "mouse", "page": 2}
        )
        mock_search.assert_called_once_with("mouse", page=2)
        self.assertEqual(response.context["next_page"], 3)
        self.assertTemplateUsed(response, "shopper_interface/_search_results_page.html")

    @patch("shopper_interface.views.search_catalog")
    def test_invalid_page_falls_back_to_one(self, mock_search):
        mock_search.return_value = {"results": [], "has_more": False}
        self.client.get(
            reverse("shopper_interface:search_results_page"), {"q": "mouse", "page": "xyz"}
        )
        mock_search.assert_called_once_with("mouse", page=1)