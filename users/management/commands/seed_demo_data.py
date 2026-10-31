"""
Management command: seed_demo_data

Populates a fresh StoreHub database with a realistic slice of activity:
merchants (store owners + staff), couriers, warehouse staff and shoppers,
plus stores, products, carts/wishlists, orders and reviews — so you can
scroll through the site as if real people were using it.

INSTALL
    Drop this file at:
        users/management/commands/seed_demo_data.py
    (create the users/management/ and users/management/commands/ folders,
    each with an empty __init__.py, if they don't already exist)

RUN
    python manage.py seed_demo_data
    python manage.py seed_demo_data --reset   # wipe previous demo data first

All demo users share one password per role (printed at the end and saved
to demo_credentials.txt in the project root). Everything is idempotent
with --reset: re-running without it will refuse to double-seed.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from delivery.models import CourierWallet, StorePickupLocation, Warehouse
from merchant_interface.models import Membership, Niche, Store
from orders.models import Order, OrderItem, StoreOrder
from products.models import Category, Product, Review
from shopper_interface.models import Cart, CartItem, Wishlist, WishlistItem
from users.models import Profile

User = get_user_model()

DEMO_EMAIL_DOMAIN = "storehub.test"

PASSWORDS = {
    "admin": "Admin123!",
    "merchant": "Merchant123!",
    "staff": "Staff123!",
    "courier": "Courier123!",
    "warehouse": "Warehouse123!",
    "shopper": "Shopper123!",
}

FIRST_NAMES = [
    "Omar", "Laila", "Hassan", "Mona", "Youssef", "Sara", "Ahmed", "Nour",
    "Karim", "Dina", "Tarek", "Heba", "Mostafa", "Rania", "Khaled", "Yasmin",
    "Amr", "Salma", "Mahmoud", "Farida", "Hady", "Jana", "Sherif", "Mariam",
    "Fady", "Aya", "Ziad", "Reem", "Adham", "Nada",
]
LAST_NAMES = [
    "Elbana", "Hassan", "Mostafa", "Said", "Fathy", "Nabil", "Kamel",
    "Shaker", "Adel", "Farouk", "Gaber", "Hamed", "Ibrahim", "Khalil",
    "Mansour", "Nasser", "Rashad", "Sabry", "Tawfik", "Youssef",
]

REVIEW_COMMENTS = [
    "Exactly as described, fast shipping.",
    "Good value for the price, would buy again.",
    "Packaging could be better but the product itself is solid.",
    "Not bad, works as expected.",
    "Really happy with this, better than I thought.",
    "Took a while to arrive but quality is great.",
    "Decent quality, a bit overpriced.",
    "Perfect, exactly what I needed.",
]

# niche name -> list of category names relevant to it
NICHE_CATEGORIES = {
    "Electronics": ["Phones & Tablets", "Laptops & Computers", "Audio", "Wearables", "Accessories"],
    "Fashion": ["Men's Clothing", "Women's Clothing", "Shoes", "Accessories"],
    "Grocery": ["Snacks", "Beverages", "Dairy", "Produce"],
    "Home & Kitchen": ["Furniture", "Kitchenware", "Home Decor"],
    "Beauty & Personal Care": ["Skincare", "Makeup", "Haircare"],
    "Sports & Outdoors": ["Fitness Equipment", "Outdoor Gear", "Shoes"],
    "Books & Stationery": ["Fiction Books", "Notebooks & Stationery"],
    "Toys & Games": ["Board Games", "Action Figures"],
}

# category name -> list of product-name templates
CATEGORY_PRODUCTS = {
    "Phones & Tablets": ["Nova X Smartphone", "AirView 10\" Tablet", "Pulse Mini Phone", "Zenith Flip Phone", "Orbit Tab 8\""],
    "Laptops & Computers": ["Stratus 14 Laptop", "CoreBook Pro 15", "LiteBook Air 13", "Vector Gaming Laptop", "WorkMate Desktop PC"],
    "Audio": ["Echo Buds Wireless", "BassLine Headphones", "SoundBar 2.1", "ClearCall Earbuds", "Vibe Bluetooth Speaker"],
    "Wearables": ["PulseFit Smartwatch", "TrackBand Fitness Tracker", "ViewRing Smart Ring"],
    "Accessories": ["FastCharge Power Bank", "GripCase Phone Cover", "WeaveStrap Watch Band", "TravelPouch Organizer"],
    "Men's Clothing": ["Classic Fit Shirt", "Slim Chino Pants", "Heritage Denim Jacket", "Everyday Crew Tee"],
    "Women's Clothing": ["Flowy Midi Dress", "High-Waist Jeans", "Cropped Knit Sweater", "Linen Blouse"],
    "Shoes": ["Trailblazer Sneakers", "ClassicStep Loafers", "RunFast Trainers", "UrbanWalk Boots"],
    "Snacks": ["Crunchy Sea Salt Chips", "Roasted Mixed Nuts", "Dark Chocolate Bar", "Honey Granola Bites"],
    "Beverages": ["Cold Brew Coffee Can", "Sparkling Citrus Water", "Green Tea Pack", "Berry Smoothie Bottle"],
    "Dairy": ["Full Cream Milk 1L", "Greek Yogurt Tub", "Aged Cheddar Block", "Butter Pack 250g"],
    "Produce": ["Organic Tomatoes 1kg", "Fresh Spinach Bunch", "Avocado Pack of 4", "Mixed Berries Box"],
    "Furniture": ["Oakline Coffee Table", "Nordic Lounge Chair", "FoldAway Desk", "Velvet Accent Sofa"],
    "Kitchenware": ["NonStick Pan Set", "Stainless Steel Knife Set", "Glass Storage Containers", "Electric Kettle"],
    "Home Decor": ["Woven Area Rug", "Ceramic Table Vase", "Linen Throw Pillow", "Wall Art Canvas Set"],
    "Skincare": ["Hydrating Face Serum", "Vitamin C Moisturizer", "Gentle Clay Cleanser", "SPF 50 Sunscreen"],
    "Makeup": ["Matte Liquid Lipstick", "Dewy Foundation", "Volumizing Mascara", "Eyeshadow Palette"],
    "Haircare": ["Repair Shampoo", "Argan Oil Conditioner", "Curl Defining Cream", "Heat Protect Spray"],
    "Fitness Equipment": ["Adjustable Dumbbell Set", "Yoga Mat Pro", "Resistance Bands Kit", "Foldable Treadmill"],
    "Outdoor Gear": ["3-Person Camping Tent", "Insulated Water Bottle", "Trail Backpack 30L", "Portable Camp Stove"],
    "Fiction Books": ["The Last Horizon", "Shadows of Cairo", "Whispers in the Nile", "Beyond the Desert Line"],
    "Notebooks & Stationery": ["Hardcover Dot Journal", "Gel Pen Set of 12", "Desk Organizer Tray", "Sticky Notes Pack"],
    "Board Games": ["Strategy Quest Board Game", "Family Trivia Night", "Castle Builders Game", "Word Rush Card Game"],
    "Action Figures": ["Galaxy Guardian Figure", "Mini Hero Collector Set", "Robo Warrior Figure"],
}

# per-niche lists of (store_name, description)
STORE_NAMES = {
    "Electronics": ["ByteHub Electronics", "CircuitPoint", "GadgetNest"],
    "Fashion": ["Thread & Co.", "UrbanWear Studio", "StyleLane"],
    "Grocery": ["GreenCart Grocers", "FreshRoot Market"],
    "Home & Kitchen": ["HearthHome", "CozyNest Living"],
    "Beauty & Personal Care": ["GlowBar Cosmetics", "PureSkin Essentials"],
    "Sports & Outdoors": ["PeakGear Sports", "TrailBound Outfitters"],
    "Books & Stationery": ["InkWell Books"],
    "Toys & Games": ["PlayBox Toys"],
}


def rand_name():
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def make_user(username, first, last, is_staff=False, is_superuser=False, password=None):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@{DEMO_EMAIL_DOMAIN}",
        password=password,
        first_name=first,
        last_name=last,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )
    return user


class Command(BaseCommand):
    help = "Seed the database with demo merchants, couriers, shoppers, stores, products and orders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help=f"Delete existing demo users (email ending @{DEMO_EMAIL_DOMAIN}) and the admin user first.",
        )
        parser.add_argument("--shoppers", type=int, default=10, help="Number of shopper accounts to create.")
        parser.add_argument("--couriers", type=int, default=4, help="Number of courier accounts to create.")

    def handle(self, *args, **options):
        reset = options["reset"]
        n_shoppers = options["shoppers"]
        n_couriers = options["couriers"]

        if reset:
            deleted, _ = User.objects.filter(email__iendswith=f"@{DEMO_EMAIL_DOMAIN}").delete()
            admin_deleted, _ = User.objects.filter(username="admin").delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted + admin_deleted} previous demo users."))

        if User.objects.filter(username="admin").exists() or User.objects.filter(
            email__iendswith=f"@{DEMO_EMAIL_DOMAIN}"
        ).exists():
            self.stderr.write(self.style.ERROR(
                "Demo data already present. Re-run with --reset to wipe and reseed."
            ))
            return

        with transaction.atomic():
            summary = self._seed(n_shoppers, n_couriers)

        self._print_and_save_summary(summary)

    # ------------------------------------------------------------------

    def _seed(self, n_shoppers, n_couriers):
        self.stdout.write("Creating admin...")
        admin = make_user("admin", "Site", "Admin", is_staff=True, is_superuser=True, password=PASSWORDS["admin"])

        self.stdout.write("Creating niches and categories...")
        niches = {name: Niche.objects.create(name=name) for name in NICHE_CATEGORIES}
        categories = {}
        for cat_names in NICHE_CATEGORIES.values():
            for cname in cat_names:
                if cname not in categories:
                    categories[cname] = Category.objects.create(name=cname)

        self.stdout.write("Creating warehouse...")
        warehouse = Warehouse.objects.create(
            name="Central Warehouse",
            address="12 Industrial Zone Rd, Cairo, Egypt",
            phone="+20 100 000 0001",
            is_default=True,
            is_active=True,
        )

        warehouse_staff = []
        for i in range(1, 3):
            first, last = rand_name()
            u = make_user(f"warehouse{i}", first, last, password=PASSWORDS["warehouse"])
            Profile.objects.filter(user=u).update(is_warehouse_staff=True)
            warehouse_staff.append(u)

        self.stdout.write("Creating merchants, stores, staff and products...")
        merchants = []
        staff_users = []
        stores = []
        products = []
        store_index = 1

        for niche_name, store_names in STORE_NAMES.items():
            niche = niches[niche_name]
            cat_pool = [categories[c] for c in NICHE_CATEGORIES[niche_name]]

            for store_name in store_names:
                first, last = rand_name()
                owner_username = f"merchant{store_index}"
                owner = make_user(owner_username, first, last, password=PASSWORDS["merchant"])
                Profile.objects.filter(user=owner).update(is_merchant=True)
                merchants.append(owner)

                store = Store.objects.create(
                    name=store_name,
                    description=f"{store_name} — your go-to spot for {niche_name.lower()}.",
                    niche=niche,
                    nationality="Egypt",
                    enabled=True,
                )
                stores.append(store)

                Membership.objects.create(
                    user=owner,
                    role="owner",
                    store=store,
                    join_date=timezone.now().date(),
                    wage_type="percentage",
                    wage=Decimal("100.00"),
                )

                StorePickupLocation.objects.create(
                    store=store,
                    address=f"{store_index} Market Street, Cairo, Egypt",
                    phone=f"+20 100 100 {store_index:04d}",
                    notes="Ring the shop bell for pickup.",
                )

                # one helper on roughly half the stores
                if store_index % 2 == 0:
                    sfirst, slast = rand_name()
                    staff_username = f"staff{store_index}"
                    staffer = make_user(staff_username, sfirst, slast, password=PASSWORDS["staff"])
                    Profile.objects.filter(user=staffer).update(is_merchant=True)
                    Membership.objects.create(
                        user=staffer,
                        role=random.choice(["helper", "manager"]),
                        store=store,
                        join_date=timezone.now().date(),
                        wage_type="salary",
                        wage=Decimal("4000.00"),
                    )
                    staff_users.append(staffer)

                # products for this store
                n_products = random.randint(6, 9)
                for _ in range(n_products):
                    category = random.choice(cat_pool)
                    name = random.choice(CATEGORY_PRODUCTS[category.name])
                    price = Decimal(random.randint(80, 4000))
                    cost = (price * Decimal(random.uniform(0.5, 0.75))).quantize(Decimal("0.01"))
                    offer = random.choice([None, None, None, Decimal("10.00"), Decimal("15.00"), Decimal("25.00")])
                    product = Product.objects.create(
                        name=name,
                        description=f"{name} from {store_name}. Quality checked before shipping.",
                        category=category,
                        store=store,
                        manufacturing_price=cost,
                        selling_price=price,
                        is_active=True,
                        current_stock=random.randint(5, 150),
                        sold=random.randint(0, 60),
                        offer=offer,
                    )
                    products.append(product)

                store_index += 1

        self.stdout.write("Creating couriers...")
        couriers = []
        for i in range(1, n_couriers + 1):
            first, last = rand_name()
            u = make_user(f"courier{i}", first, last, password=PASSWORDS["courier"])
            Profile.objects.filter(user=u).update(is_delivery_person=True)
            CourierWallet.objects.create(user=u, balance=Decimal(random.randint(0, 500)))
            couriers.append(u)

        self.stdout.write("Creating shoppers...")
        shoppers = []
        egypt_cities = ["Cairo", "Giza", "Alexandria", "Mansoura", "Tanta", "Gharbia"]
        for i in range(1, n_shoppers + 1):
            first, last = rand_name()
            u = make_user(f"shopper{i}", first, last, password=PASSWORDS["shopper"])
            Profile.objects.filter(user=u).update(
                country="Egypt",
                address1=f"{random.randint(1, 200)} {random.choice(['Main', 'Nile', 'Tahrir', 'Gamaa'])} St.",
                address3=random.choice(egypt_cities),
            )
            shoppers.append(u)

        self.stdout.write("Simulating carts, wishlists, orders and reviews...")
        order_statuses = ["Pending", "Processing", "Shipped", "At Warehouse", "Out for Delivery", "Delivered"]

        for shopper in shoppers:
            cart, _ = Cart.objects.get_or_create(user=shopper)
            wishlist, _ = Wishlist.objects.get_or_create(user=shopper)

            # a few things sitting in the cart, untouched
            for product in random.sample(products, k=random.randint(0, 3)):
                CartItem.objects.get_or_create(
                    cart=cart, product=product, defaults={"quantity": random.randint(1, 3)}
                )

            # a wishlist of things they're eyeing
            for product in random.sample(products, k=random.randint(0, 4)):
                WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)

            # 0-2 past orders per shopper, possibly spanning multiple stores
            for _ in range(random.randint(0, 2)):
                chosen_products = random.sample(products, k=random.randint(1, 4))
                by_store = {}
                for p in chosen_products:
                    by_store.setdefault(p.store, []).append(p)

                order = Order.objects.create(
                    user=shopper,
                    shipping_address=f"{shopper.profile.address1}, {shopper.profile.address3 or 'Cairo'}, Egypt",
                    payment_type="cash",
                    status=random.choice(order_statuses),
                )
                order_total = Decimal("0.00")

                for store, store_products in by_store.items():
                    status = order.status
                    delivery_person = (
                        random.choice(couriers) if status in ("Out for Delivery", "Delivered") else None
                    )
                    store_order = StoreOrder.objects.create(
                        order=order,
                        store=store,
                        delivery_person=delivery_person,
                        status=status,
                    )
                    subtotal = Decimal("0.00")
                    for product in store_products:
                        qty = random.randint(1, 3)
                        price = product.discounted_price or product.selling_price or Decimal("0.00")
                        OrderItem.objects.create(
                            store_order=store_order,
                            product=product,
                            quantity=qty,
                            price_at_purchase=price,
                        )
                        subtotal += price * qty

                        if status == "Delivered" and random.random() < 0.6:
                            Review.objects.get_or_create(
                                user=shopper,
                                product=product,
                                defaults={
                                    "stars": random.randint(3, 5),
                                    "comment": random.choice(REVIEW_COMMENTS),
                                },
                            )

                    store_order.subtotal = subtotal
                    store_order.save(update_fields=["subtotal"])
                    order_total += subtotal

                order.total_price = order_total
                order.save(update_fields=["total_price"])

        return {
            "admin": admin,
            "merchants": merchants,
            "staff": staff_users,
            "warehouse_staff": warehouse_staff,
            "couriers": couriers,
            "shoppers": shoppers,
            "stores": stores,
            "products": products,
        }

    # ------------------------------------------------------------------

    def _print_and_save_summary(self, summary):
        lines = []
        lines.append("=" * 70)
        lines.append("STOREHUB DEMO DATA SEEDED")
        lines.append("=" * 70)
        lines.append("")
        lines.append(f"Niches: {Niche.objects.count()}  |  Categories: {Category.objects.count()}")
        lines.append(f"Stores: {len(summary['stores'])}  |  Products: {len(summary['products'])}")
        lines.append(f"Orders: {Order.objects.count()}  |  Reviews: {Review.objects.count()}")
        lines.append("")
        lines.append("-" * 70)
        lines.append("LOGIN CREDENTIALS  (same password for every account in a role)")
        lines.append("-" * 70)
        lines.append(f"Admin / Django admin   -> username: admin          password: {PASSWORDS['admin']}")
        lines.append(f"Merchants (store owners) -> password: {PASSWORDS['merchant']}")
        for u in summary["merchants"]:
            store = Membership.objects.get(user=u, role="owner").store
            lines.append(f"    {u.username:<14} -> owns \"{store.name}\" ({store.niche.name})")
        if summary["staff"]:
            lines.append(f"Store staff (helper/manager) -> password: {PASSWORDS['staff']}")
            for u in summary["staff"]:
                m = Membership.objects.filter(user=u).first()
                lines.append(f"    {u.username:<14} -> {m.role} at \"{m.store.name}\"")
        lines.append(f"Warehouse staff -> password: {PASSWORDS['warehouse']}")
        for u in summary["warehouse_staff"]:
            lines.append(f"    {u.username}")
        lines.append(f"Couriers (delivery) -> password: {PASSWORDS['courier']}")
        for u in summary["couriers"]:
            lines.append(f"    {u.username}")
        lines.append(f"Shoppers -> password: {PASSWORDS['shopper']}")
        for u in summary["shoppers"]:
            lines.append(f"    {u.username}")
        lines.append("")
        lines.append("All emails are <username>@storehub.test")
        lines.append("=" * 70)

        text = "\n".join(lines)
        self.stdout.write(self.style.SUCCESS(text))

        with open("demo_credentials.txt", "w") as f:
            f.write(text + "\n")
        self.stdout.write(self.style.SUCCESS("\nAlso saved to demo_credentials.txt"))