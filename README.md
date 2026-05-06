<div align="center">

# 🏪 StoreHub

### A multi-merchant e-commerce platform built with Django

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-6.0-092E20?style=flat&logo=django&logoColor=white)](https://djangoproject.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-ready-4169E1?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)
[![Status](https://img.shields.io/badge/Status-In%20Development-orange?style=flat)]()

**StoreHub** lets merchants create and manage online stores while shoppers browse, review, and order — all on one platform.

[Features](#-features) · [Architecture](#-architecture) · [Getting Started](#-getting-started) · [API & Routes](#-url-routes) · [Roadmap](#-roadmap)

</div>

---

## 📌 Overview

StoreHub is a full-stack e-commerce platform designed around a **multi-merchant model** — any user can open a store, invite a team with role-based permissions, and manage their product catalog independently. Shoppers get a unified storefront to discover products across all merchants.

Built with Django's MTV pattern across **8 decoupled apps**, the project emphasizes clean separation of concerns, robust relational schema design, and secure access control at every layer.

---

## ✨ Features

### For Merchants
- **Store management** — create a store with a niche, nationality, and cover image
- **Role-based team access** — invite team members via email and assign Owner, Manager, or Helper roles
- **Product catalog** — full CRUD for products including 3-image upload, pricing, stock, and discount offers
- **Product specifications** — attach key-value spec pairs to any product (e.g. `Color: Black`, `RAM: 16GB`)
- **Store analytics** *(in progress)* — dedicated analytics view per store

### For Shoppers
- **Browse stores** — explore all stores by niche or nationality
- **Product discovery** — view detailed product pages with specs and images
- **Reviews** — leave a star rating and comment on purchased products *(one review per user per product enforced at DB level)*
- **Wishlist / Cart** *(in progress)*

### Platform-wide
- **Email-based authentication** — users register and log in with email, not username
- **Auto-provisioned profiles** — `Profile` and `UserSettings` created automatically on registration via Django signals
- **Category suggestion system** — users can propose new product categories for admin review
- **Niche suggestion system** — same for store niches

---

## 🏗 Architecture

The project is structured as **8 Django apps**, each owning a distinct domain:

```
Online_Store/               ← Django project root (settings, root URLs)
│
├── landing_page/           ← Public homepage
├── users/                  ← Auth, registration, profiles, settings
├── merchant_interface/     ← Store creation, membership, invitations
├── products/               ← Product catalog, specs, reviews, categories
├── shopper_interface/      ← Shopper-facing browsing and cart (WIP)
├── orders/                 ← Order lifecycle management (WIP)
└── notifications/          ← Notification system (WIP)
```

### Key design decisions

| Decision | Implementation |
|---|---|
| Role-based access control | `Membership` model with `Owner / Manager / Helper` choices; permission guards on every sensitive view |
| Signal-driven provisioning | `post_save` signal auto-creates `Profile` + `UserSettings` on user registration |
| Custom auth backend | `EmailBackend` maps email → username so login works with email credentials |
| DB-level integrity | `UniqueConstraint` on `(user, store)` memberships, `(user, product)` reviews, `(product, name)` specs |
| Query performance | Composite `Index` on `(store, category)` in `Product`; `db_index=True` on FK-heavy fields |
| Media handling | `MEDIA_ROOT` + `MEDIA_URL` configured; `ImageField` used across products, stores, and profiles |

---

## 🗄 Data Model

```
User (Django built-in)
 ├── Profile          (1:1)   birthday, picture, gender, country, addresses
 └── UserSettings     (1:1)   theme, language

Store
 ├── Niche            (FK)    store category/niche
 ├── Membership       (FK)    user ↔ store link with role + wage
 ├── MembershipInvitation     pending invites by email
 └── Product          (FK)
      ├── Category     (FK)
      ├── Spec         (FK)   key-value product attributes
      └── Review       (FK)   one per user per product

SuggestedNiche                user-submitted niche proposals
SuggestedCategory             user-submitted category proposals
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- pip
- PostgreSQL *(optional — SQLite works out of the box)*

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-username/storehub.git
cd storehub

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Apply migrations
python manage.py migrate

# 5. Create a superuser (for admin access)
python manage.py createsuperuser

# 6. Run the development server
python manage.py runserver
```

Visit `http://127.0.0.1:8000/` to see the landing page.

### Switching to PostgreSQL

In `Online_Store/settings.py`, replace the `DATABASES` block:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'storehub_db',
        'USER': 'your_db_user',
        'PASSWORD': 'your_db_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Then install the adapter and re-run migrations:

```bash
pip install psycopg2-binary
python manage.py migrate
```

### Environment Variables

Create a `.env` file at the project root and move secrets out of `settings.py`:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
DATABASE_URL=postgresql://user:password@localhost:5432/storehub_db
```

> ⚠️ Never commit your `.env` file or the raw `SECRET_KEY` to version control.

---

## 🗺 URL Routes

| Method | URL | View | Description |
|---|---|---|---|
| GET | `/` | `home` | Landing page |
| GET/POST | `/users/register/` | `register_view` | Register + login (toggled on one page) |
| GET/POST | `/users/profile/` | `profile_view` | View and update profile |
| GET/POST | `/users/change-password/` | `change_password` | Change password |
| GET/POST | `/merchant/create_store/` | `create_store` | Create a new store |
| GET/POST | `/merchant/store/<id>/` | `show_store` | Public store page |
| GET/POST | `/merchant/edit_store/<id>/` | `edit_store` | Edit store details |
| GET/POST | `/merchant/add_members/<id>/` | `add_members` | Manage store team |
| GET | `/merchant/all_my_stores/` | `all_my_stores` | All stores for current user |
| GET/POST | `/products/create_product/<store_id>/` | `Create_Product` | Add a product to a store |
| GET/POST | `/products/view_product/<id>/` | `View_Product` | Product detail page |
| GET/POST | `/products/update_product/<id>/` | `Update_Product` | Edit or delete a product |
| GET/POST | `/products/create_spec/<product_id>/` | `Create_Spec` | Add specs to a product |
| GET/POST | `/products/update_spec/<product_id>/<name>/` | `Update_Spec` | Edit or delete a spec |

---

## 🧪 Running Tests

```bash
python manage.py test
```

> Test coverage is minimal at this stage — expanding test suites is on the roadmap.

---

## 📁 Project Structure

```
storehub/
├── Online_Store/           ← Project config
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── templates/              ← All HTML templates (global template dir)
│   ├── landing/
│   ├── merchant_interface/
│   ├── products/
│   └── users/
│
├── static/                 ← Static files (CSS, JS, images)
├── media/                  ← Uploaded files (gitignored)
│
├── users/
├── merchant_interface/
├── products/
├── shopper_interface/
├── orders/
├── notifications/
├── landing_page/
│
├── manage.py
├── requirements.txt
└── README.md
```

---

## 🛣 Roadmap

- [x] User authentication (register, login, email-based)
- [x] Profile and settings management
- [x] Store creation and management
- [x] Role-based membership with invitation system
- [x] Product catalog with specs and images
- [x] Category and niche suggestion system
- [ ] Shopper cart and checkout flow
- [ ] Order lifecycle (place → confirm → ship → deliver)
- [ ] Notification system (invitations, order updates)
- [ ] Payment gateway integration (Stripe / Paymob)
- [ ] Store analytics dashboard
- [ ] REST API (Django REST Framework)
- [ ] Unit and integration test coverage
- [ ] Production deployment (Railway / Render)

---

## 🤝 Contributing

Contributions are welcome. To get started:

```bash
# Fork the repo, then:
git checkout -b feature/your-feature-name
git commit -m "feat: add your feature"
git push origin feature/your-feature-name
# Open a Pull Request
```

Please follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

---

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">
  Built by <a href="https://linkedin.com/in/your-linkedin">Omar</a> · GIU Cairo · 2025–2026
</div>
