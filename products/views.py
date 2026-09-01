from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render

from merchant_interface.models import Membership, Store
from orders.models import Cart, CartItem, OrderItem, Wishlist, WishlistItem
from shopper_interface.models import RecentlyViewed
from shopper_interface.recommendations import get_related_products

from .forms import (ProductForm, ProductImageFormSet, Review_Form, SpecFormSet,
                    Suggest_Category_Form)
from .models import (Category, Product, Review, Spec, SpecType,
                     SuggestedCategory)

# Create your views here.


@login_required
def Create_Product(request, current_store_id):
    current_store = Store.objects.filter(id=current_store_id).first()
    if not current_store:
        messages.error(request, "Store not found!")
        return redirect("/")

    membership = Membership.objects.filter(
        store=current_store, user=request.user
    ).first()
    if not membership or membership.role not in ("owner", "manager"):
        messages.error(request, "You don't have permission to access this page!")
        return redirect("/")

    if request.method == "POST":
        if "Suggest_Category_btn" in request.POST:
            suggest_category_form = Suggest_Category_Form(request.POST)
            product_form = ProductForm()

            if suggest_category_form.is_valid():
                SuggestedCategory.objects.create(
                    name=suggest_category_form.cleaned_data["category_name"],
                    suggester=request.user,
                )
                messages.success(request, "Category suggested successfully!")
                return redirect(f"/products/create_product/{current_store_id}/")

            messages.error(request, "Please enter a valid category name!")

        else:
            product_form = ProductForm(request.POST, request.FILES)
            suggest_category_form = Suggest_Category_Form()

            if "Create_Product_btn" in request.POST:
                if product_form.is_valid():
                    new_product = product_form.save(commit=False)
                    new_product.store = current_store
                    new_product.save()
                    messages.success(
                        request,
                        "Product added successfuly, now lets add some specifications to it!",
                    )
                    return redirect(f"/products/manage_specs/{new_product.id}/")

                else:
                    messages.error(request, "Invalid form!")

    else:
        product_form = ProductForm()
        suggest_category_form = Suggest_Category_Form()

    context = {
        "product_form": product_form,
        "suggest_category_form": suggest_category_form,
    }

    return render(request, "products/create_product.html", context)


@login_required
def spec_type_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    if query:
        matches = SpecType.objects.filter(name__icontains=query).order_by("name")[:8]
        results = [{"id": st.id, "name": st.name} for st in matches]
    return JsonResponse({"results": results})


@login_required
def Manage_Specs(request, product_id):
    product = Product.objects.filter(id=product_id).first()
    if not product:
        messages.error(request, "Product not found!")
        return redirect("/")

    membership = Membership.objects.filter(
        store=product.store, user=request.user
    ).first()
    if not membership or membership.role not in ("owner", "manager"):
        messages.error(request, "You don't have permission to access this page!")
        return redirect("/")

    if request.method == "POST":
        formset = SpecFormSet(request.POST, instance=product)
        if formset.is_valid():
            formset.save()
            messages.success(
                request, "Specifications saved! Now let's add some images."
            )
            return redirect(f"/products/manage_images/{product_id}/")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        formset = SpecFormSet(instance=product)

    context = {"formset": formset, "product": product}
    return render(request, "products/manage_specs.html", context)


@login_required
def Update_Product(request, product_id):
    old_product = Product.objects.filter(id=product_id).first()
    if not old_product:
        messages.error(request, "Product not found!")
        return redirect("/")

    current_store = old_product.store
    membership = Membership.objects.filter(
        store=current_store, user=request.user
    ).first()
    if not membership or membership.role not in ("owner", "manager"):
        messages.error(request, "You don't have permission to access this page!")
        return redirect("/")

    specs = Spec.objects.filter(product=old_product).select_related("spec_type")
    primary_image = old_product.images.filter(is_primary=True).first()

    if request.method == "POST":
        update_product_form = ProductForm(
            request.POST, request.FILES, instance=old_product
        )

        if "Update_Product_btn" in request.POST:
            if update_product_form.is_valid():
                update_product_form.save()
                messages.success(request, "Product updated successfully!")
                return redirect(f"/products/view_product/{product_id}/")

            else:
                messages.error(request, "Invalid form!")

        if "delete_product_btn" in request.POST:
            old_product.is_active = False
            old_product.save(update_fields=["is_active"])
            messages.success(request, "Product deactivated successfully!")
            return redirect(f"/merchant/store/{current_store.id}/")

        if "reactivate_product_btn" in request.POST:
            old_product.is_active = True
            old_product.save(update_fields=["is_active"])
            messages.success(request, "Product reactivated!")
            return redirect(f"/products/view_product/{product_id}/")

    else:
        update_product_form = ProductForm(instance=old_product)

    context = {
        "update_product_form": update_product_form,
        "specs": specs,
        "primary_image": primary_image,
    }

    return render(request, "products/update_product.html", context)


def View_Product(request, product_id):
    product = (
        Product.active.select_related("category", "store").filter(id=product_id).first()
    )
    if not product:
        messages.error(request, "Product not found!")
        return redirect("/")

    if request.user.is_authenticated:
        rv, created = RecentlyViewed.objects.get_or_create(
            user=request.user, product=product
        )
        if not created:
            rv.save()  # auto_now=True bumps viewed_at + triggers the 'view' Interaction signal

    specs = Spec.objects.filter(product=product).select_related("spec_type")

    primary_image = (
        product.images.filter(is_primary=True).first() or product.images.first()
    )

    has_purchased = False
    existing_review = None
    review_form = None
    if request.user.is_authenticated:
        has_purchased = OrderItem.objects.filter(
            order__user=request.user,
            order__status="Delivered",
            product=product,
        ).exists()
        if has_purchased:
            existing_review = Review.objects.filter(
                user=request.user, product=product
            ).first()
            review_form = Review_Form(instance=existing_review)

    reviews = (
        Review.objects.filter(product=product)
        .select_related("user")
        .order_by("-creation_date")
    )

    context = {
        "product": product,
        "specs": specs,
        "related_products": get_related_products(product, limit=8),
        "primary_image": primary_image,
        "has_purchased": has_purchased,
        "review_form": review_form,
        "reviews": reviews,
    }

    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "Please log in to do that.")
            return redirect("view_product", product_id=product.id)

        if "add_to_cart_btn" in request.POST:
            cart, _ = Cart.objects.get_or_create(user=request.user)
            cart_item, created = CartItem.objects.get_or_create(
                cart=cart, product=product
            )
            desired_qty = cart_item.quantity if created else cart_item.quantity + 1

            if product.current_stock is None or desired_qty > product.current_stock:
                stock_display = product.current_stock or 0
                messages.error(request, f"Only {stock_display} left in stock.")
                if created:
                    cart_item.delete()  # don't leave a 0/invalid cart row behind
            else:
                cart_item.quantity = desired_qty
                cart_item.save()
                messages.success(request, f"{product.name} added to cart!")

        elif "add_to_wishlist_btn" in request.POST:
            wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
            WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)
            messages.success(request, f"{product.name} added to wishlist!")

        elif "submit_review_btn" in request.POST:
            if not has_purchased:
                messages.error(
                    request, "You can only review products you've purchased."
                )
            else:
                review_form = Review_Form(request.POST, instance=existing_review)
                if review_form.is_valid():
                    review = review_form.save(commit=False)
                    review.user = request.user
                    review.product = product
                    review.save()
                    messages.success(request, "Review saved!")
                else:
                    messages.error(request, "Invalid review!")

        return redirect("view_product", product_id=product.id)

    return render(request, "products/view_product.html", context)


@login_required
def Manage_Product_Images(request, product_id):
    product = Product.objects.filter(id=product_id).first()
    if not product:
        messages.error(request, "Product not found!")
        return redirect("/")
    membership = Membership.objects.filter(
        store=product.store, user=request.user
    ).first()
    if not membership or membership.role not in ("owner", "manager"):
        messages.error(request, "You don't have permission to access this page!")
        return redirect("/")

    if request.method == "POST":
        formset = ProductImageFormSet(request.POST, request.FILES, instance=product)
        if formset.is_valid():
            primary_count = sum(
                1
                for f in formset.forms
                if f.cleaned_data.get("is_primary") and not f.cleaned_data.get("DELETE")
            )
            if primary_count > 1:
                messages.error(request, "Only one image can be primary.")
            else:
                formset.save()

                if not product.images.filter(is_primary=True).exists():
                    first_image = product.images.order_by("order").first()
                    if first_image:
                        first_image.is_primary = True
                        first_image.save(update_fields=["is_primary"])

                messages.success(request, "Images updated!")
                return redirect(f"/products/view_product/{product_id}/")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        formset = ProductImageFormSet(instance=product)

    return render(
        request, "products/manage_images.html", {"formset": formset, "product": product}
    )


@login_required
def Review_Suggested_Categories(request):
    if not request.user.is_staff:
        messages.error(request, "You don't have permission to access this page!")
        return redirect("/")

    if request.method == "POST":
        suggestion = SuggestedCategory.objects.filter(
            id=request.POST.get("suggestion_id")
        ).first()
        if suggestion and "approve_btn" in request.POST:
            Category.objects.get_or_create(name=suggestion.name)
            suggestion.status = "approved"
            suggestion.save(update_fields=["status"])
        elif suggestion and "reject_btn" in request.POST:
            suggestion.status = "rejected"
            suggestion.save(update_fields=["status"])
        return redirect("review_suggested_categories")

    pending = SuggestedCategory.objects.filter(status="pending")
    return render(request, "products/review_suggestions.html", {"pending": pending})


# @login_required
# def View_Product(request, product_id):
#     product = (
#         Product.active.select_related("category", "store").filter(id=product_id).first()
#     )
#     if not product:
#         messages.error(request, "Product not found!")
#         return redirect("/")

#     if request.user.is_authenticated:
#         rv, created = RecentlyViewed.objects.get_or_create(
#             user=request.user, product=product
#         )
#         if not created:
#             rv.save()  # auto_now=True bumps viewed_at + triggers the 'view' Interaction signal

#     specs = Spec.objects.filter(product=product).select_related("spec_type")

#     primary_image = (
#         product.images.filter(is_primary=True).first() or product.images.first()
#     )

#     has_purchased = False
#     existing_review = None
#     review_form = None
#     if request.user.is_authenticated:
#         has_purchased = OrderItem.objects.filter(
#             order__user=request.user,
#             order__status="Delivered",
#             product=product,
#         ).exists()
#         if has_purchased:
#             existing_review = Review.objects.filter(
#                 user=request.user, product=product
#             ).first()
#             review_form = Review_Form(instance=existing_review)

#     reviews = (
#         Review.objects.filter(product=product)
#         .select_related("user")
#         .order_by("-creation_date")
#     )

#     context = {
#         "product": product,
#         "specs": specs,
#         "related_products": get_related_products(product, limit=8),
#         "primary_image": primary_image,
#         "has_purchased": has_purchased,
#         "review_form": review_form,
#         "reviews": reviews,
#     }

#     if request.method == "POST":
#         if not request.user.is_authenticated:
#             messages.error(request, "Please log in to do that.")
#             return redirect("view_product", product_id=product.id)

#         if "add_to_cart_btn" in request.POST:
#             with transaction.atomic():
#                 locked_product = Product.objects.select_for_update().get(id=product.id)

#                 cart, _ = Cart.objects.get_or_create(user=request.user)
#                 cart_item, created = CartItem.objects.get_or_create(
#                     cart=cart, product=locked_product
#                 )
#                 desired_qty = cart_item.quantity if created else cart_item.quantity + 1

#                 if (
#                     locked_product.current_stock is None
#                     or desired_qty > locked_product.current_stock
#                 ):
#                     stock_display = locked_product.current_stock or 0
#                     messages.error(request, f"Only {stock_display} left in stock.")
#                     if created:
#                         cart_item.delete()  # don't leave a 0/invalid cart row behind
#                 else:
#                     cart_item.quantity = desired_qty
#                     cart_item.save()
#                     messages.success(request, f"{product.name} added to cart!")

#         elif "add_to_wishlist_btn" in request.POST:
#             wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
#             WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)
#             messages.success(request, f"{product.name} added to wishlist!")

#         elif "submit_review_btn" in request.POST:
#             if not has_purchased:
#                 messages.error(
#                     request, "You can only review products you've purchased."
#                 )
#             else:
#                 review_form = Review_Form(request.POST, instance=existing_review)
#                 if review_form.is_valid():
#                     review = review_form.save(commit=False)
#                     review.user = request.user
#                     review.product = product
#                     review.save()
#                     messages.success(request, "Review saved!")
#                 else:
#                     messages.error(request, "Invalid review!")

#         return redirect("view_product", product_id=product.id)

#     return render(request, "products/view_product.html", context)
