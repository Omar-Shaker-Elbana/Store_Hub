"""
Test suite for the `products` app.

Covers:
    - Models: Category, Product, Review, Spec, SuggestedCategory
      (field defaults, ordering, unique/uniqueness constraints, FK behaviour)
    - Forms: ProductForm, SpecForm, Suggest_Category_Form
    - Views: Create_Product, Create_Spec, Update_Product, Update_Spec,
      View_Product, Suggest_Category
      (permissions/membership checks, happy paths, and not-found handling)

Run with:
    python manage.py test products
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from merchant_interface.models import Membership, Niche, Store
from products.forms import ProductForm, Suggest_Category_Form, SpecForm
from products.models import Category, Product, Review, Spec, SpecType, SuggestedCategory


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

        # --- Store / membership ---
        cls.niche = Niche.objects.create(name="Electronics")
        cls.store = Store.objects.create(name="Owner's Store", niche=cls.niche)
        Membership.objects.create(user=cls.owner, store=cls.store, role="Owner")

        # A second store the owner has NO membership in, to test permission checks
        cls.other_store = Store.objects.create(name="Someone Else's Store", niche=cls.niche)

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

    def login_owner(self):
        self.client.login(username="owner", password="strongpass123")

    def login_outsider(self):
        self.client.login(username="outsider", password="strongpass123")


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------
class CategoryModelTests(ProductsTestBase):
    def test_category_name_must_be_unique(self):
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

    def test_product_defaults(self):
        # sold/current_stock should default to 0 when not supplied
        product = Product.objects.create(category=self.category, store=self.store)
        self.assertEqual(product.sold, 0)
        self.assertEqual(product.current_stock, 0)

    def test_products_ordered_by_creation_date_descending(self):
        import datetime

        newer = Product.objects.create(
            name="Newer Laptop", category=self.category, store=self.store
        )
        # creation_date is a DateField (day resolution only), so two products
        # made moments apart in the same test can land on the same date.
        # Force a clearly later date on `newer` to make the ordering
        # assertion deterministic.
        Product.objects.filter(id=newer.id).update(
            creation_date=datetime.date.today() + datetime.timedelta(days=1)
        )
        products = list(Product.objects.all())
        # The most recently created product should come first (Meta.ordering = ['-creation_date'])
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
            product.full_clean()  # should raise ValidationError for offer > 100


class SpecModelTests(ProductsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ram_type = SpecType.objects.create(name="RAM")

    def test_spec_created_for_product(self):
        spec = Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        self.assertEqual(spec.product, self.product)
        self.assertEqual(spec.value, "16GB")

    def test_duplicate_spec_type_for_same_product_not_allowed(self):
        Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Spec.objects.create(product=self.product, spec_type=self.ram_type, value="32GB")

    def test_same_spec_type_allowed_on_different_products(self):
        other_product = Product.objects.create(
            name="Other", description="x", category=self.category, store=self.store
        )
        Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        # Should NOT raise -- uniqueness is per-product, not global
        spec = Spec.objects.create(product=other_product, spec_type=self.ram_type, value="8GB")
        self.assertEqual(spec.value, "8GB")

    def test_deleting_product_cascades_to_specs(self):
        spec = Spec.objects.create(product=self.product, spec_type=self.ram_type, value="16GB")
        self.product.delete()
        self.assertFalse(Spec.objects.filter(id=spec.id).exists())


class ReviewModelTests(ProductsTestBase):
    def test_review_created(self):
        review = Review.objects.create(
            user=self.owner, product=self.product, stars=5, comment="Great laptop!"
        )
        self.assertEqual(review.stars, 5)

    def test_stars_must_be_between_1_and_5(self):
        review = Review(user=self.owner, product=self.product, stars=6)
        with self.assertRaises(Exception):
            review.full_clean()

    def test_different_users_can_each_review_the_same_product(self):
        # product is a ForeignKey (not OneToOne) on Review, so multiple
        # users reviewing the same product is valid and expected.
        Review.objects.create(user=self.owner, product=self.product, stars=4)
        review2 = Review.objects.create(user=self.outsider, product=self.product, stars=2)
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
        self.assertIsNotNone(suggestion.suggestion_date)

    def test_suggested_category_name_must_be_unique(self):
        SuggestedCategory.objects.create(name="Smart Home", suggester=self.owner)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SuggestedCategory.objects.create(name="Smart Home", suggester=self.outsider)


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------
class ProductFormTests(ProductsTestBase):
    def test_valid_data(self):
        form = ProductForm(data={
            "name": "New Phone",
            "description": "A phone",
            "category": self.category.id,
            "manufacturing_price": "100.00",
            "selling_price": "199.99",
            "current_stock": 5,
            "offer": "10.00",
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_offer_over_100_is_invalid(self):
        form = ProductForm(data={
            "name": "New Phone",
            "category": self.category.id,
            "offer": "150.00",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("offer", form.errors)

    def test_missing_category_is_invalid(self):
        # `category` is a required FK on the model (no null=True/blank=True)
        form = ProductForm(data={"name": "No category product"})
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)


class SpecFormTests(ProductsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.color_type = SpecType.objects.create(name="Color")

    def test_valid_data(self):
        form = SpecForm(data={"spec_type": self.color_type.id, "value": "Black"})
        self.assertTrue(form.is_valid())

    def test_blank_form_is_invalid(self):
        # spec_type and value are required on the model
        form = SpecForm(data={})
        self.assertFalse(form.is_valid())


class SuggestCategoryFormTests(ProductsTestBase):
    def test_valid_data(self):
        form = Suggest_Category_Form(data={"category_name": "Wearables"})
        self.assertTrue(form.is_valid())

    def test_blank_name_is_invalid(self):
        form = Suggest_Category_Form(data={"category_name": ""})
        self.assertFalse(form.is_valid())


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
        self.assertRedirects(response, "/")
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m) for m in messages))

    def test_nonexistent_store_redirects_home(self):
        self.login_owner()
        response = self.client.get(
            reverse("create_product", args=[999999]), follow=True
        )
        self.assertRedirects(response, "/")

    def test_member_can_load_create_product_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/create_product.html")

    def test_member_can_create_product(self):
        self.login_owner()
        response = self.client.post(self.url, data={
            "Create_Product_btn": "1",
            "name": "New Gadget",
            "description": "Shiny",
            "category": self.category.id,
            "manufacturing_price": "10.00",
            "selling_price": "19.99",
            "current_stock": 3,
        })
        new_product = Product.objects.filter(name="New Gadget").first()
        self.assertIsNotNone(new_product)
        self.assertEqual(new_product.store, self.store)
        self.assertRedirects(response, f"/products/create_spec/{new_product.id}/")

    def test_invalid_form_does_not_create_product(self):
        self.login_owner()
        before_count = Product.objects.count()
        response = self.client.post(self.url, data={
            "Create_Product_btn": "1",
            "offer": "999.00",  # invalid: over 100
        })
        self.assertEqual(Product.objects.count(), before_count)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Invalid form" in str(m) for m in messages))


class CreateSpecViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("create_spec", args=[self.product.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_nonexistent_product_redirects_home(self):
        self.login_owner()
        response = self.client.get(reverse("create_spec", args=[999999]), follow=True)
        self.assertRedirects(response, "/")

    def test_non_member_is_redirected_with_error(self):
        self.login_outsider()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/")

    def test_member_can_load_create_spec_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/create_spec.html")

    def test_member_can_add_spec(self):
        self.login_owner()
        color_type = SpecType.objects.create(name="Color")
        response = self.client.post(self.url, data={
            "Save_and_Create_Another_Spec_btn": "1",
            "spec_type": color_type.id,
            "value": "Silver",
        })
        self.assertTrue(Spec.objects.filter(product=self.product, spec_type=color_type).exists())
        self.assertRedirects(response, f"/products/create_spec/{self.product.id}/")


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
        self.assertRedirects(response, "/")

    def test_non_member_is_redirected_with_error(self):
        self.login_outsider()
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, "/")

    def test_member_can_load_update_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/update_product.html")

    # def test_update_product_currently_raises_because_messages_call_is_missing_request(self):
    #     """
    #     NOTE: Update_Product's success branch calls
    #     `messages.success("Product updated successfuly!")` -- missing the
    #     required `request` first argument. Since `success(request, message,
    #     extra_tags='')` then receives the string as `request` and nothing
    #     for `message`, this raises `TypeError: success() missing 1 required
    #     positional argument: 'message'` on every successful update (the DB
    #     write still happens beforehand). This test documents that bug; once
    #     fixed to `messages.success(request, "...")`, replace this test with
    #     a happy-path assertion that the product was updated and a success
    #     message was shown.
    #     """
    #     self.login_owner()
    #     with self.assertRaises(TypeError):
    #         self.client.post(self.url, data={
    #             "Update_Product_btn": "1",
    #             "name": "Updated Name",
    #             "category": self.category.id,
    #             "selling_price": "999.99",
    #         })

    def test_member_can_delete_product(self):
        self.login_owner()
        response = self.client.post(self.url, data={"delete_product_btn": "1"}, follow=True)
        self.assertFalse(Product.objects.filter(id=self.product.id).exists())
        self.assertRedirects(response, "/")


class UpdateSpecViewTests(ProductsTestBase):
    def setUp(self):
        self.ram_type = SpecType.objects.create(name="RAM")
        self.spec = Spec.objects.create(product=self.product, spec_type=self.ram_type, value="8GB")
        self.url = reverse("update_spec", args=[self.product.id, self.ram_type.name])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_nonexistent_product_redirects_home(self):
        self.login_owner()
        response = self.client.get(
            reverse("update_spec", args=[999999, "RAM"]), follow=True
        )
        self.assertRedirects(response, "/")

    def test_nonexistent_spec_redirects_to_product(self):
        self.login_owner()
        response = self.client.get(
            reverse("update_spec", args=[self.product.id, "NoSuchSpec"])
        )
        self.assertRedirects(
            response,
            f"/products/view_product/{self.product.id}/",
            fetch_redirect_response=False,
        )

    def test_member_can_load_update_spec_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/update_spec.html")

    def test_member_can_update_spec_value(self):
        self.login_owner()
        response = self.client.post(self.url, data={
            "Save_btn": "1",
            "spec_type": self.ram_type.id,
            "value": "16GB",
        })
        self.spec.refresh_from_db()
        self.assertEqual(self.spec.value, "16GB")
        self.assertRedirects(
            response,
            f"/products/view_product/{self.product.id}/",
            fetch_redirect_response=False,
        )

    def test_member_can_delete_spec(self):
        self.login_owner()
        response = self.client.post(self.url, data={"delete_spec_btn": "1"})
        self.assertFalse(Spec.objects.filter(id=self.spec.id).exists())
        self.assertRedirects(
            response,
            f"/products/view_product/{self.product.id}/",
            fetch_redirect_response=False,
        )


class ViewProductViewTests(ProductsTestBase):
    def test_view_product_currently_raises_missing_template(self):
        """
        NOTE: View_Product renders 'products/view_product.html', which does
        not exist anywhere in the templates directory. Every request to
        this view currently raises TemplateDoesNotExist. This test
        documents that bug -- once the template is added, replace this
        with a test asserting a 200 response and the product's name in
        the rendered content.
        """
        from django.template import TemplateDoesNotExist

        url = reverse("view_product", args=[self.product.id])
        with self.assertRaises(TemplateDoesNotExist):
            self.client.get(url)

    def test_nonexistent_product_redirects_home(self):
        url = reverse("view_product", args=[999999])
        response = self.client.get(url, follow=True)
        self.assertRedirects(response, "/")


class SuggestCategoryViewTests(ProductsTestBase):
    def setUp(self):
        self.url = reverse("suggest_category")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_member_can_load_suggest_category_page(self):
        self.login_owner()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/suggest_category.html")

    def test_submitting_new_category_name_creates_a_suggestion_pending_review(self):
        # Suggest_Category creates a SuggestedCategory (pending moderation),
        # not a live Category -- that's the whole point of having a
        # separate model with a `suggester` field for review.
        self.login_owner()
        response = self.client.post(self.url, data={"category_name": "Smart Home"}, follow=True)
        self.assertTrue(SuggestedCategory.objects.filter(name="Smart Home").exists())
        self.assertFalse(Category.objects.filter(name="Smart Home").exists())
        self.assertRedirects(response, "/")

    def test_duplicate_category_name_shows_error(self):
        self.login_owner()
        response = self.client.post(
            self.url, data={"category_name": self.category.name}, follow=True
        )
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("already exists" in str(m) for m in messages))

    def test_blank_category_name_shows_error(self):
        self.login_owner()
        response = self.client.post(self.url, data={"category_name": ""}, follow=True)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("enter a valid category name" in str(m) for m in messages))