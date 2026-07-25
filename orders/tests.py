"""
Test suite for the `orders` app.

Covers:
    - Models: Cart, CartItem, Wishlist, WishlistItem, Order, OrderItem
      (defaults, __str__, unique constraints, cascade behaviour)
    - Forms: Cart_Item_Form
    - Views: Cart_view, Wishlist_view, Place_Order_View
      (login requirements, stock checks, add/remove/update flows,
      order placement happy path)

Run with:
    python manage.py test orders
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.template import TemplateDoesNotExist
from django.test import TestCase
from django.urls import reverse

from merchant_interface.models import Niche, Store
from orders.forms import Cart_Item_Form
from orders.models import Cart, CartItem, Order, OrderItem, Wishlist, WishlistItem
from products.models import Category, Product
from users.models import Card


class OrdersTestBase(TestCase):
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

    def login(self):
        self.client.login(username="shopper", password="strongpass123")


class CartModelTests(OrdersTestBase):
    def test_cart_auto_created_for_new_user(self):
        # a post_save signal on User creates a Cart automatically
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


class CartItemModelTests(OrdersTestBase):
    def setUp(self):
        self.cart = Cart.objects.get(user=self.user)

    def test_cart_item_quantity_defaults_to_one(self):
        item = CartItem.objects.create(cart=self.cart, product=self.product)
        self.assertEqual(item.quantity, 1)

    def test_cart_item_str(self):
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
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


class WishlistModelTests(OrdersTestBase):
    def test_wishlist_str(self):
        wishlist = Wishlist.objects.create(user=self.user)
        self.assertEqual(str(wishlist), f"{self.user}'s wishlist")

    def test_user_can_only_have_one_wishlist(self):
        Wishlist.objects.create(user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wishlist.objects.create(user=self.user)


class WishlistItemModelTests(OrdersTestBase):
    def setUp(self):
        self.wishlist = Wishlist.objects.create(user=self.user)

    def test_wishlist_item_str(self):
        item = WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)
        self.assertEqual(str(item), f"{self.product} ({self.wishlist.user})")

    def test_same_product_cannot_be_added_twice_to_same_wishlist(self):
        WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)

    def test_deleting_wishlist_cascades_to_items(self):
        item = WishlistItem.objects.create(wishlist=self.wishlist, product=self.product)
        self.wishlist.delete()
        self.assertFalse(WishlistItem.objects.filter(id=item.id).exists())


class OrderModelTests(OrdersTestBase):
    def test_order_status_defaults_to_pending(self):
        order = Order.objects.create(
            user=self.user, total_price=Decimal("100.00"), shipping_address="123 Main St"
        )
        self.assertEqual(order.status, "Pending")

    def test_order_payment_type_optional(self):
        order = Order.objects.create(
            user=self.user, total_price=Decimal("100.00"), shipping_address="123 Main St"
        )
        self.assertIsNone(order.payment_type)

    def test_order_can_be_linked_to_a_card(self):
        card = Card.objects.create(user=self.user, card_num=1234, card_name="Test Card")
        order = Order.objects.create(
            user=self.user,
            total_price=Decimal("100.00"),
            shipping_address="123 Main St",
            payment_type="card",
            card=card,
        )
        self.assertEqual(order.card, card)
        self.assertEqual(order.payment_type, "card")

    def test_deleting_user_cascades_to_orders(self):
        order = Order.objects.create(
            user=self.other_user, total_price=Decimal("50.00"), shipping_address="Somewhere"
        )
        self.other_user.delete()
        self.assertFalse(Order.objects.filter(id=order.id).exists())


class OrderItemModelTests(OrdersTestBase):
    def setUp(self):
        self.order = Order.objects.create(
            user=self.user, total_price=Decimal("100.00"), shipping_address="123 Main St"
        )

    def test_order_item_quantity_defaults_to_one(self):
        item = OrderItem.objects.create(order=self.order, product=self.product)
        self.assertEqual(item.quantity, 1)

    def test_multiple_products_allowed_on_same_order(self):
        OrderItem.objects.create(order=self.order, product=self.product, quantity=2)
        OrderItem.objects.create(order=self.order, product=self.product2, quantity=1)
        self.assertEqual(OrderItem.objects.filter(order=self.order).count(), 2)

    def test_deleting_order_cascades_to_items(self):
        item = OrderItem.objects.create(order=self.order, product=self.product)
        self.order.delete()
        self.assertFalse(OrderItem.objects.filter(id=item.id).exists())


class CartItemFormTests(OrdersTestBase):
    def test_form_only_exposes_quantity_field(self):
        form = Cart_Item_Form()
        self.assertEqual(list(form.fields.keys()), ["quantity"])

    def test_form_valid_with_positive_quantity(self):
        form = Cart_Item_Form(data={"quantity": 3})
        self.assertTrue(form.is_valid())

    def test_form_invalid_with_negative_quantity(self):
        form = Cart_Item_Form(data={"quantity": -1})
        self.assertFalse(form.is_valid())


class CartViewTests(OrdersTestBase):
    def setUp(self):
        self.url = reverse("mycart")
        self.cart = Cart.objects.get(user=self.user)

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_currently_raises_missing_template(self):
        """
        NOTE: Cart_view renders 'orders/cart.html', which does not exist
        anywhere in the templates directory. Every GET request to this
        view currently raises TemplateDoesNotExist. This test documents
        that bug; once the template is added, replace this with a test
        asserting a 200 response and the cart's items in the rendered
        content.
        """
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        with self.assertRaises(TemplateDoesNotExist):
            self.client.get(self.url)

    def test_get_recalculates_total_price(self):
        # Recalculation happens before the template render, so it takes
        # effect even though the render itself fails (see test above).
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        CartItem.objects.create(cart=self.cart, product=self.product2, quantity=1)
        with self.assertRaises(TemplateDoesNotExist):
            self.client.get(self.url)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.total_price, Decimal("220.00"))

    def test_get_clamps_item_quantity_to_available_stock(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=999)
        with self.assertRaises(TemplateDoesNotExist):
            self.client.get(self.url)
        item.refresh_from_db()
        self.assertEqual(item.quantity, self.product.current_stock)

    def test_post_remove_btn_deletes_item(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(
            self.url, data={"item_id": item.id, "remove_btn": "1"}
        )
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_post_updates_item_quantity(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(
            self.url, data={"item_id": item.id, "quantity": 4}
        )
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

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


class WishlistViewTests(OrdersTestBase):
    def setUp(self):
        self.url = reverse("wishlist")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_currently_raises_missing_template(self):
        """
        NOTE: Wishlist_view renders 'orders/wishlist.html', which does not
        exist anywhere in the templates directory. Every GET request to
        this view currently raises TemplateDoesNotExist. This test
        documents that bug; once the template is added, replace this with
        a test asserting a 200 response and the wishlist's items in the
        rendered content.
        """
        self.login()
        with self.assertRaises(TemplateDoesNotExist):
            self.client.get(self.url)

    def test_post_remove_btn_deletes_item_and_redirects(self):
        self.login()
        wishlist, _ = Wishlist.objects.get_or_create(user=self.user)
        item = WishlistItem.objects.create(wishlist=wishlist, product=self.product)
        response = self.client.post(
            self.url, data={"item_id": item.id, "remove_btn": "1"}
        )
        self.assertFalse(WishlistItem.objects.filter(id=item.id).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_post_remove_btn_creates_wishlist_if_missing(self):
        self.login()
        self.assertFalse(Wishlist.objects.filter(user=self.user).exists())
        response = self.client.post(self.url, data={"item_id": 999, "remove_btn": "1"})
        self.assertTrue(Wishlist.objects.filter(user=self.user).exists())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)


class PlaceOrderViewTests(OrdersTestBase):
    def setUp(self):
        self.url = reverse("place_order")
        self.mycart_url = reverse("mycart")
        self.cart = Cart.objects.get(user=self.user)

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_empty_cart_redirects_with_error(self):
        self.login()
        response = self.client.post(self.url, data={"shipping_address": "123 Main St"})
        self.assertRedirects(response, self.mycart_url, fetch_redirect_response=False)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Your cart is empty!", messages)

    def test_insufficient_stock_redirects_with_error(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=100)
        response = self.client.post(self.url, data={"shipping_address": "123 Main St"})
        self.assertRedirects(response, self.mycart_url, fetch_redirect_response=False)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("has only" in m and "in stock" in m for m in messages))
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_placing_order_creates_order_and_order_items(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        CartItem.objects.create(cart=self.cart, product=self.product2, quantity=1)
        self.cart.total_price = Decimal("220.00")
        self.cart.save()

        response = self.client.post(self.url, data={"shipping_address": "123 Main St"})

        order = Order.objects.get(user=self.user)
        self.assertEqual(order.total_price, Decimal("220.00"))
        self.assertEqual(order.shipping_address, "123 Main St")
        self.assertEqual(order.status, "Pending")
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 2)
        self.assertRedirects(response, self.mycart_url, fetch_redirect_response=False)

    def test_placing_order_decrements_stock_and_increments_sold(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=3)
        self.client.post(self.url, data={"shipping_address": "123 Main St"})

        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, 7)
        self.assertEqual(self.product.sold, 3)

    def test_placing_order_clears_the_cart(self):
        self.login()
        item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.client.post(self.url, data={"shipping_address": "123 Main St"})

        self.assertFalse(CartItem.objects.filter(id=item.id).exists())
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.total_price, Decimal("0.00"))

    def test_success_message_shown_on_placing_order(self):
        self.login()
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(self.url, data={"shipping_address": "123 Main St"})
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Order placed successfully!", messages)