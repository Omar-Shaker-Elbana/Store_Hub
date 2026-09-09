from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import redirect, render

from products.models import Product
from .search import search_catalog
from shopper_interface.recommendations import (get_frequently_bought_together,
                                               get_home_feed_page)

from . import forms
from .models import (Cart, CartItem, RecentSearch, SearchTrend, Wishlist, WishlistItem)


def _log_search(user, query):
    """Records a search for personalized history (auth users) and global trending.
    Called once per search on the initial page load, not on infinite-scroll pages."""
    normalized = query.lower().strip()
    if not normalized:
        return

    if user.is_authenticated:
        RecentSearch.objects.update_or_create(
            user=user,
            normalized_query=normalized,
            defaults={"query_text": query},
        )

    trend, _ = SearchTrend.objects.get_or_create(
        normalized_query=normalized,
        defaults={"display_query": query},
    )
    SearchTrend.objects.filter(pk=trend.pk).update(hit_count=F("hit_count") + 1)


def home(request):
    feed = get_home_feed_page(request.user, page=1)

    return render(
        request,
        "shopper_interface/home.html",
        {
            "recommended_products": feed["products"],
            "recommended_stores": feed["stores"],
            "has_more": feed["has_more"],
            "next_page": 2,
            "feed_start_index": feed["start"],
            "feed_total_length": feed["total"],
        },
    )


def home_feed_page(request):
    """AJAX endpoint the home page's infinite scroll calls for subsequent pages."""
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    feed = get_home_feed_page(request.user, page=page)

    return render(
        request,
        "shopper_interface/_home_feed_page.html",
        {
            "recommended_products": feed["products"],
            "recommended_stores": feed["stores"],
            "has_more": feed["has_more"],
            "next_page": page + 1,
            "feed_start_index": feed["start"],
            "feed_total_length": feed["total"],
        },
    )


@login_required
def quick_add_to_cart(request, product_id):
    """AJAX endpoint for the one-click cart button on product cards (home feed, search)."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)

    product = Product.objects.filter(id=product_id).first()
    if not product:
        return JsonResponse({"success": False, "error": "Product not found."}, status=404)

    cart, _ = Cart.objects.get_or_create(user=request.user)
    cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
    desired_qty = cart_item.quantity if created else cart_item.quantity + 1

    if product.current_stock is None or desired_qty > product.current_stock:
        stock_display = product.current_stock or 0
        if created:
            cart_item.delete()
        return JsonResponse(
            {"success": False, "error": f"Only {stock_display} left in stock."}, status=400
        )

    cart_item.quantity = desired_qty
    cart_item.save()
    return JsonResponse({"success": True, "message": f"{product.name} added to cart!"})


@login_required
def quick_add_to_wishlist(request, product_id):
    """AJAX endpoint for the one-click wishlist button on product cards (home feed, search)."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)

    product = Product.objects.filter(id=product_id).first()
    if not product:
        return JsonResponse({"success": False, "error": "Product not found."}, status=404)

    wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
    _, created = WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)

    if not created:
        return JsonResponse(
            {"success": True, "message": f"{product.name} is already in your wishlist."}
        )
    return JsonResponse({"success": True, "message": f"{product.name} added to wishlist!"})


def _recalc_cart_total(cart):
    """Shared by the AJAX cart endpoints: recomputes and saves the cart total,
    honoring each item's discounted price where one applies."""
    items = CartItem.objects.filter(cart=cart).select_related("product")
    subtotal = sum(
        (item.quantity or 0) * (item.product.discounted_price or item.product.selling_price or 0)
        for item in items
        if item.product
    )
    cart.total_price = subtotal
    cart.save(update_fields=["total_price"])
    return subtotal, items.count()


@login_required
def update_cart_item(request, item_id):
    """AJAX endpoint: change a cart line's quantity without reloading the page."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)

    cart_item = CartItem.objects.select_related("product", "cart").filter(
        id=item_id, cart__user=request.user
    ).first()
    if not cart_item:
        return JsonResponse({"success": False, "error": "Item not found."}, status=404)

    try:
        quantity = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid quantity."}, status=400)

    product = cart_item.product
    stock = product.current_stock if product and product.current_stock is not None else 0

    if quantity < 1:
        return JsonResponse({"success": False, "error": "Quantity must be at least 1."}, status=400)
    if quantity > stock:
        return JsonResponse(
            {"success": False, "error": f"Only {stock} left in stock.", "max_quantity": stock},
            status=400,
        )

    cart_item.quantity = quantity
    cart_item.save(update_fields=["quantity"])

    cart_subtotal, item_count = _recalc_cart_total(cart_item.cart)
    unit_price = product.discounted_price or product.selling_price or 0
    line_subtotal = unit_price * quantity

    return JsonResponse({
        "success": True,
        "quantity": quantity,
        "line_subtotal": float(line_subtotal),
        "cart_subtotal": float(cart_subtotal),
        "item_count": item_count,
    })


@login_required
def remove_cart_item(request, item_id):
    """AJAX endpoint: remove a cart line without reloading the page."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)

    cart_item = CartItem.objects.select_related("cart").filter(
        id=item_id, cart__user=request.user
    ).first()
    if not cart_item:
        return JsonResponse({"success": False, "error": "Item not found."}, status=404)

    cart = cart_item.cart
    cart_item.delete()
    cart_subtotal, item_count = _recalc_cart_total(cart)

    return JsonResponse({
        "success": True,
        "cart_subtotal": float(cart_subtotal),
        "item_count": item_count,
    })


@login_required
def Cart_view(request):
    current_cart, _ = Cart.objects.get_or_create(user=request.user)
    items = CartItem.objects.filter(cart=current_cart).select_related("product", "product__store")

    for item in items:
        current_product = item.product
        if current_product is None:
            messages.error(request, "An item in your cart is no longer available.")
            continue
        if not current_product.is_active or not current_product.store.enabled:
            messages.error(
                request,
                f"{current_product.name} is no longer available and won't be checked out.",
            )
            continue
        stock = current_product.current_stock
        if stock is not None and item.quantity is not None and item.quantity > stock:
            item.quantity = stock
            item.save()
            messages.error(
                request,
                f"{current_product.name} has only {stock} in stock!",
            )

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        cart_item = CartItem.objects.filter(id=item_id, cart=current_cart).first()

        if "remove_btn" in request.POST:
            if cart_item:
                cart_item.delete()
                messages.success(request, "Item removed!")
        elif cart_item:
            form = forms.CartItemForm(
                request.POST, instance=cart_item, product=cart_item.product
            )
            if form.is_valid():
                form.save()
            else:
                for error in form.errors.values():
                    messages.error(request, error.as_text())

        return redirect("shopper_interface:cart")

    with transaction.atomic():
        _recalc_cart_total(current_cart)

    seen = {item.product_id for item in items}
    suggestions = []
    for item in items:
        if item.product is None:
            continue
        for p in get_frequently_bought_together(item.product, limit=3):
            if p.id not in seen:
                suggestions.append(p)
                seen.add(p.id)
        if len(suggestions) >= 6:
            break

    form = forms.CartItemForm()
    context = {"items": items, "form": form, "suggestions": suggestions[:6]}
    return render(request, "shopper_interface/cart.html", context)


@login_required
def Wishlist_view(request):
    wishlist, _ = Wishlist.objects.get_or_create(user=request.user)

    if request.method == "POST" and "remove_btn" in request.POST:
        item_id = request.POST.get("item_id")
        WishlistItem.objects.filter(id=item_id, wishlist=wishlist).delete()
        messages.success(request, "Removed from wishlist!")
        return redirect("shopper_interface:wishlist")

    items = WishlistItem.objects.filter(wishlist=wishlist)
    return render(request, "shopper_interface/wishlist.html", {"items": items})

def search_view(request):
    query = request.GET.get("q", "").strip()
    _log_search(request.user, query)
    result = search_catalog(query, page=1)

    return render(
        request,
        "shopper_interface/search.html",
        {
            "query": query,
            "results": result["results"],
            "has_more": result["has_more"],
            "next_page": 2,
        },
    )


def search_results_page(request):
    """AJAX endpoint for infinite scroll on the search results page."""
    query = request.GET.get("q", "").strip()
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    result = search_catalog(query, page=page)

    return render(
        request,
        "shopper_interface/_search_results_page.html",
        {
            "results": result["results"],
            "has_more": result["has_more"],
            "next_page": page + 1,
        },
    )