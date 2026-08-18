"""
Test suite for the `products` app.

Covers:
    - Models: Category, Product, Product_Image, Review, Spec, SuggestedCategory
    - Forms: ProductForm, SpecForm, SpecFormSet, Review_Form,
      Suggest_Category_Form, ProductImageFormSet
    - Views: Create_Product (incl. category suggestion flow), spec_type_search,
      Manage_Specs, Update_Product, View_Product (cart/wishlist/reviews),
      Manage_Product_Images, Review_Suggested_Categories

NOTE: Order/OrderItem/Cart/CartItem/Wishlist/WishlistItem field names are
inferred from their usage in products/views.py (orders.models,
merchant_interface.models). Adjust field names below if your actual models
differ.

Run with:
    python manage.py test products
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from merchant_interface.models import Membership, Niche, Store
from orders.models import (Cart, CartItem, Order, OrderItem, Wishlist,
                           WishlistItem)
from products.forms import (ProductForm, ProductImageFormSet, Review_Form,
                            SpecForm, SpecFormSet, Suggest_Category_Form)
from products.models import (Category, Product, Product_Image, Review, Spec,
                             SpecType, SuggestedCategory)

# A minimal valid 1x1 transparent GIF, used anywhere Product_Image.image needs
# real (small) file bytes.
TINY_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x05\x04\x04"
    b"\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44"
    b"\x01\x00\x3b"
)


def make_image_file(name="test.gif"):
    return SimpleUploadedFile(name, TINY_GIF, content_type="image/gif")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
class ProductsTestBase(TestCase):
    """Common fixtures reused by every test class in this file."""

    @classmethod
    def setUpTestData(cls):
        # --- Users ---
        cls.owner = User.objects.create_user(
            username="owner", password="strongpass123", email="owner@example.com"
        )
        cls.outsider = User.objects.create_user(
            username="outsider", password="strongpass123", email="outsider@example.com"
        )
        cls.shopper = User.objects.create_user(
            username="shopper", password="strongpass123", email="shopper@example.com"
        )
        cls.staff_user = User.objects.create_user(
            username="staffer",
            password="strongpass123",
            email="staff@example.com",
            is_staff=True,
        )

        # --- Store / membership ---
        cls.niche = Niche.objects.create(name="Electronics")
        cls.store = Store.objects.create(name="Owner's Store", niche=cls.niche)
        Membership.objects.create(user=cls.owner, store=cls.store, role="Owner")

        # A second store the owner has NO membership in, to test permission checks
        cls.other_store = Store.objects.create(
            name="Someone Else's Store", niche=cls.niche
        )

        # --- Catalog data ---
        cls.category = Category.objects.create(name="Laptops")

        cls.product = Product.objects.create(
            name="ThinkPad X1",
            description="A solid business laptop",
            category=cls.category,
            manufacturing_price=Decimal("500.00"),
            selling_price=Decimal("899.99"),
            current_stock=10,
            store=cls.store,
        )

        # Every product needs at least one image for the storefront view /
        # the image-management flow to make sense.
        cls.primary_image = Product_Image.objects.create(
            product=cls.product, image=make_image_file(), is_primary=True, order=1
        )

    def login_owner(self):
        self.client.login(username="owner", password="strongpass123")

    def login_outsider(self):
        self.client.login(username="outsider", password="strongpass123")

    def login_shopper(self):
        self.client.login(username="shopper", password="strongpass123")

    def login_staff(self):
        self.client.login(username="staffer", password="strongpass123")

    def mark_shopper_as_purchaser(self):
        """Creates a delivered order/order-item so `has_purchased` is True."""
        order = Order.objects.create(
            user=self.shopper,
            status="Delivered",
            total_price=self.product.selling_price,
        )
        OrderItem.objects.create(order=order, product=self.product, quantity=1)
        return order


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------
class CategoryModelTests(ProductsTestBase):
    def test_category_name_must_be_unique_at_root_level(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(name="Laptops")

    def test_category_can_be_created_with_just_a_name(self):
        cat = Category.objects.create(name="Tablets")
        self.assertEqual(cat.name, "Tablets")


class ProductModelTests(ProductsTestBase):
    def test_product_created_with_expected_fields(self):
        self.assertEqual(self.product.name, "ThinkPad X1")
        self.assertEqual(self.product.store, self.store)
        self.assertEqual(self.product.category, self.category)
        self.assertEqual(self.product.current_stock, 10)
        self.assertTrue(self.product.is_active)

    def test_product_defaults(self):
        product = Product.objects.create(category=self.category, store=self.store)
        self.assertEqual(product.sold, 0)
        self.assertEqual(product.current_stock, 0)

    def test_products_ordered_by_creation_date_descending(self):
        import datetime

        newer = Product.objects.create(
            name="Newer Laptop", category=self.category, store=self.store
        )
        Product.objects.filter(id=newer.id).update(
            creation_date=datetime.date.today() + datetime.timedelta(days=1)
        )
        products = list(Product.objects.all())
        self.assertEqual(products[0].id, newer.id)

    def test_deleting_store_cascades_to_products(self):
        store = Store.objects.create(name="Temp Store", niche=self.niche)
        product = Product.objects.create(category=self.category, store=store)
        store.delete()
        self.assertFalse(Product.objects.filter(id=product.id).exists())

    def test_deleting_category_in_use_is_protected(self):
        from django.db.models.deletion import ProtectedError

        with self.assertRaises(ProtectedError):
            self.category.delete()

    def test_offer_must_be_between_0_and_100(self):
        product = Product(
            name="Bad offer",
            category=self.category,
            store=self.store,
            offer=Decimal("150.00"),
        )
        with self.assertRaises(Exception):
            product.full_clean()

    def test_active_manager_excludes_inactive_products(self):
        Product.objects.create(
            name="Discontinued",
            category=self.category,
            store=self.store,
            is_active=False,
        )
        active_names = set(Product.active.values_list("name", flat=True))
        self.assertIn("ThinkPad X1", active_names)
        self.assertNotIn("Discontinued", active_names)

    def test_discounted_price_with_offer(self):
        self.product.offer = Decimal("10.00")
        self.product.save()
        self.assertEqual(self.product.discounted_price, Decimal("809.991"))

    def test_discounted_price_without_offer_returns_selling_price(self):
        self.assertIsNone(self.product.offer)
        self.assertEqual(self.product.discounted_price, self.product.selling_price)


class ProductImageModelTests(ProductsTestBase):
    def test_image_created_and_linked_to_product(self):
        img = Product_Image.objects.create(
            product=self.product, image=make_image_file("second.gif"), order=2
        )
        self.assertEqual(img.product, self.product)
        self.assertFalse(img.is_primary)

    def test_images_ordered_by_order_field(self):
        Product_Image.objects.create(
            product=self.product, image=make_image_file("a.gif"), order=5
        )
        Product_Image.objects.create(
            product=self.product, image=make_image_file("b.gif"), order=0
        )
        orders = list(self.product.images.values_list("order", flat=True))
        self.assertEqual(orders, sorted(orders))

    def test_deleting_product_cascades_to_images(self):
        img_id = self.primary_image.id
        self.product.delete()
        self.assertFalse(Product_Image.objects.filter(id=img_id).exists())


class SpecModelTests(ProductsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ram_type = SpecType.objects.create(name="RAM")

    def test_spec_created_for_product(self):
        spec = Spec.objects.create(
            product=self.product, spec_type=self.ram_type, value="16GB"
        )
        self.assertEqual(spec.product, self.product)
        self.assertEqual(spec.value, "16GB")

    def test_duplicate_spec_type_for_same_product_not_allowed(self):
        Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Spec.objects.create(
                    product=self.product, spec_type=self.ram_type, value="32GB"
                )

    def test_same_spec_type_allowed_on_different_products(self):
        other_product = Product.objects.create(
            name="Other", description="x", category=self.category, store=self.store
        )
        Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        spec = Spec.objects.create(
            product=other_product, spec_type=self.ram_type, value="8GB"
        )
        self.assertEqual(spec.value, "8GB")

    def test_deleting_product_cascades_to_specs(self):
        spec = Spec.objects.create(
            product=self.product, spec_type=self.ram_type, value="16GB"
        )
        self.product.delete()
        self.assertFalse(Spec.objects.filter(id=spec.id).exists())


class ReviewModelTests(ProductsTestBase):
    def test_review_created(self):
        review = Review.objects.create(
            user=self.owner, product=self.product, stars=5, comment="Great laptop!"
        )
        self.assertEqual(review.stars, 5)
        self.assertIsNotNone(review.creation_date)

    def test_stars_must_be_between_1_and_5(self):
        review = Review(user=self.owner, product=self.product, stars=6)
        with self.assertRaises(Exception):
            review.full_clean()

    def test_different_users_can_each_review_the_same_product(self):
        Review.objects.create(user=self.owner, product=self.product, stars=4)
        review2 = Review.objects.create(
            user=self.outsider, product=self.product, stars=2
        )
        self.assertEqual(Review.objects.filter(product=self.product).count(), 2)
        self.assertEqual(review2.stars, 2)

    def test_same_user_reviewing_same_product_twice_fails(self):
        Review.objects.create(user=self.owner, product=self.product, stars=4)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Review.objects.create(user=self.owner, product=self.product, stars=5)


class SuggestedCategoryModelTests(ProductsTestBase):
    def test_suggested_category_created(self):
        suggestion = SuggestedCategory.objects.create(
            name="Smart Home", suggester=self.owner
        )
        self.assertEqual(suggestion.name, "Smart Home")
        self.assertEqual(suggestion.status, "pending")
        self.assertIsNotNone(suggestion.suggestion_date)

    def test_suggested_category_name_must_be_unique(self):
        SuggestedCategory.objects.create(name="Smart Home", suggester=self.owner)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SuggestedCategory.objects.create(
                    name="Smart Home", suggester=self.outsider
                )


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------
class ProductFormTests(ProductsTestBase):
    def test_valid_data(self):
        form = ProductForm(
            data={
                "name": "New Phone",
                "description": "A phone",
                "category": self.category.id,
                "manufacturing_price": "100.00",
                "selling_price": "199.99",
                "current_stock": 5,
                "offer": "10.00",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_offer_over_100_is_invalid(self):
        form = ProductForm(
            data={"name": "New Phone", "category": self.category.id, "offer": "150.00"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("offer", form.errors)

    def test_missing_category_is_invalid(self):
        form = ProductForm(data={"name": "No category product"})
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)

    def test_selling_price_below_manufacturing_price_is_invalid(self):
        form = ProductForm(
            data={
                "name": "Underpriced",
                "category": self.category.id,
                "manufacturing_price": "100.00",
                "selling_price": "50.00",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("selling_price", form.errors)


class SpecFormTests(ProductsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.color_type = SpecType.objects.create(name="Color")

    def test_valid_data_creates_new_spec_type_from_free_text(self):
        form = SpecForm(data={"spec_type": "Material", "value": "Aluminum"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["spec_type"].name, "Material")
        self.assertTrue(SpecType.objects.filter(name="Material").exists())

    def test_matches_existing_spec_type_case_insensitively(self):
        form = SpecForm(data={"spec_type": "color", "value": "Black"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["spec_type"], self.color_type)

    def test_close_match_is_rejected_in_favor_of_existing_name(self):
        form = SpecForm(data={"spec_type": "Colour", "value": "Black"})
        self.assertFalse(form.is_valid())
        self.assertIn("spec_type", form.errors)

    def test_blank_spec_type_is_invalid(self):
        form = SpecForm(data={"spec_type": "   ", "value": "Black"})
        self.assertFalse(form.is_valid())
        self.assertIn("spec_type", form.errors)


class SpecFormSetTests(ProductsTestBase):
    def management_data(self, total, initial=0):
        return {
            "specs-TOTAL_FORMS": str(total),
            "specs-INITIAL_FORMS": str(initial),
            "specs-MIN_NUM_FORMS": "0",
            "specs-MAX_NUM_FORMS": "1000",
        }

    def test_duplicate_spec_type_within_same_submission_is_rejected(self):
        data = self.management_data(total=2)
        data.update(
            {
                "specs-0-spec_type": "Color",
                "specs-0-value": "Black",
                "specs-1-spec_type": "color",  # same type, different case
                "specs-1-value": "Silver",
            }
        )
        formset = SpecFormSet(data, instance=self.product)
        self.assertFalse(formset.is_valid())

    def test_two_different_spec_types_in_one_submission_is_valid(self):
        data = self.management_data(total=2)
        data.update(
            {
                "specs-0-spec_type": "Color",
                "specs-0-value": "Black",
                "specs-1-spec_type": "RAM",
                "specs-1-value": "16GB",
            }
        )
        formset = SpecFormSet(data, instance=self.product)
        self.assertTrue(formset.is_valid(), formset.errors)


class ReviewFormTests(ProductsTestBase):
    def test_valid_data(self):
        form = Review_Form(data={"stars": 4, "comment": "Pretty good"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_stars_out_of_range_is_invalid(self):
        form = Review_Form(data={"stars": 7, "comment": "Too many stars"})
        self.assertFalse(form.is_valid())


class SuggestCategoryFormTests(ProductsTestBase):
    def test_valid_data(self):
        form = Suggest_Category_Form(data={"category_name": "Wearables"})
        self.assertTrue(form.is_valid())

    def test_blank_name_is_invalid(self):
        form = Suggest_Category_Form(data={"category_name": ""})
        self.assertFalse(form.is_valid())

    def test_existing_category_name_is_invalid(self):
        form = Suggest_Category_Form(data={"category_name": "Laptops"})
        self.assertFalse(form.is_valid())

    def test_already_suggested_name_is_invalid(self):
        SuggestedCategory.objects.create(name="Wearables", suggester=self.owner)
        form = Suggest_Category_Form(data={"category_name": "Wearables"})
        self.assertFalse(form.is_valid())


class ProductImageFormSetTests(ProductsTestBase):
    def management_data(self, total, initial=0):
        return {
            "images-TOTAL_FORMS": str(total),
            "images-INITIAL_FORMS": str(initial),
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "20",
        }

    def test_at_least_one_surviving_image_required(self):
        data = self.management_data(total=1)
        data.update({"images-0-order": "1"})
        formset = ProductImageFormSet(
            data, {}, instance=Product(category=self.category, store=self.store)
        )
        self.assertFalse(formset.is_valid())

    def test_new_row_with_image_is_valid(self):
        data = self.management_data(total=1)
        data.update({"images-0-order": "1", "images-0-is_primary": "on"})
        files = {"images-0-image": make_image_file()}
        formset = ProductImageFormSet(
            data, files, instance=Product(category=self.category, store=self.store)
        )
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_deleting_the_only_image_without_replacement_is_invalid(self):
        data = self.management_data(total=1, initial=1)
        data.update(
            {
                "images-0-id": str(self.primary_image.id),
                "images-0-order": "1",
                "images-0-DELETE": "on",
            }
        )
        formset = ProductImageFormSet(data, {}, instance=self.product)
        self.assertFalse(formset.is_valid())


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------
class CreateProductViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("create_product", args=[self.store.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_non_member_is_redirected_with_error(self):
        self.login_outsider()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/shopper/")
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m) for m in messages))

    def test_nonexistent_store_redirects_home(self):
        self.login_owner()
        response = self.client.get(
            reverse("create_product", args=[999999]), follow=True
        )
        self.assertRedirects(response, "/shopper/")

    def test_member_can_load_create_product_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/create_product.html")

    def test_member_can_create_product_and_is_sent_to_manage_specs(self):
        self.login_owner()
        response = self.client.post(
            self.url,
            data={
                "Create_Product_btn": "1",
                "name": "New Gadget",
                "description": "Shiny",
                "category": self.category.id,
                "manufacturing_price": "10.00",
                "selling_price": "19.99",
                "current_stock": 3,
            },
        )
        new_product = Product.objects.filter(name="New Gadget").first()
        self.assertIsNotNone(new_product)
        self.assertEqual(new_product.store, self.store)
        self.assertRedirects(response, f"/products/manage_specs/{new_product.id}/")

    def test_invalid_form_does_not_create_product(self):
        self.login_owner()
        before_count = Product.objects.count()
        response = self.client.post(
            self.url, data={"Create_Product_btn": "1", "offer": "999.00"}
        )
        self.assertEqual(Product.objects.count(), before_count)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Invalid form" in str(m) for m in messages))

    def test_suggesting_a_new_category_creates_pending_suggestion(self):
        self.login_owner()
        response = self.client.post(
            self.url,
            data={"Suggest_Category_btn": "1", "category_name": "Smart Home"},
        )
        self.assertTrue(SuggestedCategory.objects.filter(name="Smart Home").exists())
        self.assertFalse(Category.objects.filter(name="Smart Home").exists())
        self.assertRedirects(response, self.url)

    def test_suggesting_a_duplicate_category_name_shows_error(self):
        self.login_owner()
        response = self.client.post(
            self.url, data={"Suggest_Category_btn": "1", "category_name": "Laptops"}
        )
        self.assertEqual(response.status_code, 200)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("valid category name" in str(m) for m in messages))


class SpecTypeSearchViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("spec_type_search")
        SpecType.objects.create(name="RAM")
        SpecType.objects.create(name="Storage")

    def test_requires_login(self):
        response = self.client.get(self.url, {"q": "ram"})
        self.assertEqual(response.status_code, 302)

    def test_returns_case_insensitive_matches(self):
        self.login_owner()
        response = self.client.get(self.url, {"q": "am"})
        self.assertEqual(response.status_code, 200)
        names = [r["name"] for r in response.json()["results"]]
        self.assertIn("RAM", names)
        self.assertNotIn("Storage", names)

    def test_blank_query_returns_no_results(self):
        self.login_owner()
        response = self.client.get(self.url, {"q": ""})
        self.assertEqual(response.json()["results"], [])


class ManageSpecsViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("manage_specs", args=[self.product.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

        def test_non_member_is_redirected_with_error(self):
            self.login_outsider()
            response = self.client.get(self.url, follow=True)
            self.assertRedirects(response, "/shopper/")

    def test_member_can_load_manage_specs_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/manage_specs.html")

    def test_member_can_add_spec_and_is_sent_to_manage_images(self):
        self.login_owner()
        response = self.client.post(
            self.url,
            data={
                "specs-TOTAL_FORMS": "1",
                "specs-INITIAL_FORMS": "0",
                "specs-MIN_NUM_FORMS": "0",
                "specs-MAX_NUM_FORMS": "1000",
                "specs-0-spec_type": "Color",
                "specs-0-value": "Silver",
            },
        )
        self.assertTrue(
            Spec.objects.filter(product=self.product, value="Silver").exists()
        )
        self.assertRedirects(response, f"/products/manage_images/{self.product.id}/")


class UpdateProductViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("update_product", args=[self.product.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_nonexistent_product_redirects_home(self):
        self.login_owner()
        response = self.client.get(
            reverse("update_product", args=[999999]), follow=True
        )
        self.assertRedirects(response, "/shopper/")

    def test_non_member_is_redirected_with_error(self):
        self.login_outsider()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/shopper/")

    def test_member_can_load_update_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/update_product.html")

    def test_member_can_update_product(self):
        self.login_owner()
        response = self.client.post(
            self.url,
            data={
                "Update_Product_btn": "1",
                "name": "Updated Name",
                "category": self.category.id,
                "selling_price": "999.99",
            },
            follow=True,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated Name")
        self.assertRedirects(response, f"/products/view_product/{self.product.id}/")

    def test_member_can_deactivate_product(self):
        self.login_owner()
        response = self.client.post(
            self.url, data={"delete_product_btn": "1"}, follow=True
        )
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        # deactivated products are excluded from Product.active, but the row
        # itself still exists
        self.assertTrue(Product.objects.filter(id=self.product.id).exists())

    def test_member_can_reactivate_product(self):
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        self.login_owner()
        response = self.client.post(
            self.url, data={"reactivate_product_btn": "1"}, follow=True
        )
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        self.assertRedirects(response, f"/products/view_product/{self.product.id}/")


class ViewProductViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("view_product", args=[self.product.id])

    def test_nonexistent_product_redirects_home(self):
        response = self.client.get(reverse("view_product", args=[999999]), follow=True)
        self.assertRedirects(response, "/")

    def test_inactive_product_is_treated_as_not_found(self):
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/")

    def test_anonymous_user_can_view_active_product(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/view_product.html")
        self.assertContains(response, "ThinkPad X1")

    def test_reviews_are_shown_newest_first(self):
        older = Review.objects.create(
            user=self.owner, product=self.product, stars=3, comment="Just okay"
        )
        Review.objects.filter(id=older.id).update(creation_date="2020-01-01")
        newer = Review.objects.create(
            user=self.outsider, product=self.product, stars=5, comment="Loved it"
        )
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertLess(content.index("Loved it"), content.index("Just okay"))

    def test_anonymous_post_redirects_with_error(self):
        response = self.client.post(
            self.url, data={"add_to_cart_btn": "1"}, follow=True
        )
        self.assertRedirects(response, self.url)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("log in" in str(m).lower() for m in messages))

    def test_authenticated_user_can_add_to_cart(self):
        self.login_shopper()
        response = self.client.post(
            self.url, data={"add_to_cart_btn": "1"}, follow=True
        )
        cart = Cart.objects.get(user=self.shopper)
        item = CartItem.objects.get(cart=cart, product=self.product)
        self.assertEqual(item.quantity, 1)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("added to cart" in str(m).lower() for m in messages))

    def test_adding_to_cart_twice_increments_quantity(self):
        self.login_shopper()
        self.client.post(self.url, data={"add_to_cart_btn": "1"})
        self.client.post(self.url, data={"add_to_cart_btn": "1"})
        cart = Cart.objects.get(user=self.shopper)
        item = CartItem.objects.get(cart=cart, product=self.product)
        self.assertEqual(item.quantity, 2)

    def test_add_to_cart_respects_stock_limit(self):
        self.product.current_stock = 1
        self.product.save(update_fields=["current_stock"])
        self.login_shopper()
        self.client.post(self.url, data={"add_to_cart_btn": "1"})  # qty -> 1, ok
        response = self.client.post(
            self.url, data={"add_to_cart_btn": "1"}, follow=True
        )  # qty -> 2, over stock
        cart = Cart.objects.get(user=self.shopper)
        item = CartItem.objects.get(cart=cart, product=self.product)
        self.assertEqual(item.quantity, 1)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("left in stock" in str(m) for m in messages))

    def test_authenticated_user_can_add_to_wishlist(self):
        self.login_shopper()
        response = self.client.post(
            self.url, data={"add_to_wishlist_btn": "1"}, follow=True
        )
        wishlist = Wishlist.objects.get(user=self.shopper)
        self.assertTrue(
            WishlistItem.objects.filter(
                wishlist=wishlist, product=self.product
            ).exists()
        )
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("added to wishlist" in str(m).lower() for m in messages))

    def test_review_rejected_for_user_who_has_not_purchased(self):
        self.login_shopper()
        response = self.client.post(
            self.url,
            data={"submit_review_btn": "1", "stars": "5", "comment": "Great!"},
            follow=True,
        )
        self.assertFalse(
            Review.objects.filter(user=self.shopper, product=self.product).exists()
        )
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("purchased" in str(m).lower() for m in messages))

    def test_review_accepted_for_user_who_has_purchased(self):
        self.mark_shopper_as_purchaser()
        self.login_shopper()
        response = self.client.post(
            self.url,
            data={"submit_review_btn": "1", "stars": "5", "comment": "Great!"},
            follow=True,
        )
        review = Review.objects.filter(user=self.shopper, product=self.product).first()
        self.assertIsNotNone(review)
        self.assertEqual(review.stars, 5)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("saved" in str(m).lower() for m in messages))

    def test_purchaser_can_update_their_existing_review(self):
        self.mark_shopper_as_purchaser()
        Review.objects.create(
            user=self.shopper, product=self.product, stars=2, comment="Meh"
        )
        self.login_shopper()
        self.client.post(
            self.url,
            data={"submit_review_btn": "1", "stars": "5", "comment": "Changed my mind"},
        )
        review = Review.objects.get(user=self.shopper, product=self.product)
        self.assertEqual(review.stars, 5)
        self.assertEqual(review.comment, "Changed my mind")
        self.assertEqual(
            Review.objects.filter(user=self.shopper, product=self.product).count(), 1
        )


class ManageProductImagesViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("manage_product_images", args=[self.product.id])

    def management_data(self, total, initial):
        return {
            "images-TOTAL_FORMS": str(total),
            "images-INITIAL_FORMS": str(initial),
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "20",
        }

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_non_member_is_redirected_with_error(self):
        self.login_outsider()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/shopper/")

    def test_member_can_load_manage_images_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/manage_images.html")

    def test_member_can_add_a_second_image(self):
        self.login_owner()
        data = self.management_data(total=2, initial=1)
        data.update(
            {
                "images-0-id": str(self.primary_image.id),
                "images-0-order": "1",
                "images-0-is_primary": "on",
                "images-1-order": "2",
            }
        )
        files = {"images-1-image": make_image_file("second.gif")}
        response = self.client.post(self.url, data={**data, **files}, follow=True)
        self.assertEqual(self.product.images.count(), 2)
        self.assertRedirects(response, f"/products/view_product/{self.product.id}/")

    def test_more_than_one_primary_image_is_rejected(self):
        second = Product_Image.objects.create(
            product=self.product, image=make_image_file("second.gif"), order=2
        )
        self.login_owner()
        data = self.management_data(total=2, initial=2)
        data.update(
            {
                "images-0-id": str(self.primary_image.id),
                "images-0-order": "1",
                "images-0-is_primary": "on",
                "images-1-id": str(second.id),
                "images-1-order": "2",
                "images-1-is_primary": "on",
            }
        )
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 200)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Only one image can be primary" in str(m) for m in messages)
        )

    def test_deleting_the_only_image_is_rejected_by_the_formset(self):
        self.login_owner()
        data = self.management_data(total=1, initial=1)
        data.update(
            {
                "images-0-id": str(self.primary_image.id),
                "images-0-order": "1",
                "images-0-DELETE": "on",
            }
        )
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product_Image.objects.filter(id=self.primary_image.id).exists())
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("fix the errors" in str(m).lower() for m in messages))


class ReviewSuggestedCategoriesViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("review_suggested_categories")
        self.suggestion = SuggestedCategory.objects.create(
            name="Smart Home", suggester=self.owner
        )

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_non_staff_is_redirected_with_error(self):
        self.login_owner()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/shopper/")
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m) for m in messages))

    def test_staff_can_load_page(self):
        self.login_staff()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/review_suggestions.html")

    def test_staff_can_approve_suggestion(self):
        self.login_staff()
        self.client.post(
            self.url, data={"suggestion_id": self.suggestion.id, "approve_btn": "1"}
        )
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, "approved")
        self.assertTrue(Category.objects.filter(name="Smart Home").exists())

    def test_staff_can_reject_suggestion(self):
        self.login_staff()
        self.client.post(
            self.url, data={"suggestion_id": self.suggestion.id, "reject_btn": "1"}
        )
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, "rejected")
        self.assertFalse(Category.objects.filter(name="Smart Home").exists())
