from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from merchant_interface.models import Membership, Store
from notifications.models import Notification
from products.models import Product
from shopper_interface.models import Cart, CartItem

from . import forms
from .forms import StoreOrderStatusForm
from .models import Order, OrderItem, StoreOrder


@login_required
def manage_store_orders(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, "Store not found.")
        return redirect("create_store")

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, "You do not have permission to view this store.")
        return redirect("create_store")

    can_manage = user_membership.role in ("owner", "manager")

    store_order_list = (
        StoreOrder.objects.filter(store=store)
        .select_related("order", "order__user", "delivery_person")
        .prefetch_related("items__product")
        .order_by("-order__created_at")
    )
    paginator = Paginator(store_order_list, 12)
    page_number = request.GET.get("page", 1)
    store_orders = paginator.get_page(page_number)

    if request.method == "POST":
        if not can_manage:
            messages.error(
                request, "You do not have permission to update orders for this store."
            )
            return redirect("manage_store_orders", store_id=store.id)

        store_order_id = request.POST.get("store_order_id")
        store_order = StoreOrder.objects.filter(id=store_order_id, store=store).first()
        if not store_order:
            messages.error(request, "Order not found.")
            return redirect("manage_store_orders", store_id=store.id)

        previous_status = store_order.status
        form = StoreOrderStatusForm(request.POST, instance=store_order)

        if form.is_valid():
            with transaction.atomic():
                updated_store_order = form.save()

                # Cancellation needs to give the stock back — nothing in the
                # codebase did this before (flagged as a missing feature).
                if (
                    updated_store_order.status == "Cancelled"
                    and previous_status != "Cancelled"
                ):
                    for order_item in updated_store_order.items.select_related(
                        "product"
                    ):
                        product = order_item.product
                        product.current_stock = (
                            product.current_stock or 0
                        ) + order_item.quantity
                        product.sold = max((product.sold or 0) - order_item.quantity, 0)
                        product.save()

                if updated_store_order.status != previous_status:
                    Notification.objects.create(
                        recipient=updated_store_order.order.user,
                        sender=request.user,
                        message=(
                            f"Your order from {store.name} is now "
                            f"{updated_store_order.status}."
                        ),
                    )
            messages.success(request, "Order status updated!")
        else:
            for error in form.errors.values():
                messages.error(request, error.as_text())

        return redirect("manage_store_orders", store_id=store.id)

    context = {
        "store": store,
        "user_membership": user_membership,
        "can_manage": can_manage,
        "store_orders": store_orders,
    }
    return render(request, "merchant_interface/manage_store_orders.html", context)


@login_required
def checkout_view(request):
    """Review + shipping/payment form. Order creation itself happens in Place_Order_View on submit."""
    current_cart = Cart.objects.filter(user=request.user).first()
    items = list(
        CartItem.objects.select_related("product", "product__store").filter(
            cart=current_cart
        )
        if current_cart
        else CartItem.objects.none()
    )

    if not items:
        messages.error(request, "Your cart is empty!")
        return redirect("shopper_interface:cart")

    subtotal = sum(
        (item.quantity or 0) * (item.product.discounted_price or item.product.selling_price or 0)
        for item in items
        if item.product
    )

    order_form = forms.OrderForm()

    return render(
        request,
        "orders/checkout.html",
        {
            "items": items,
            "subtotal": subtotal,
            "order_form": order_form,
        },
    )


@login_required
@transaction.atomic
def Place_Order_View(request):
    current_cart = Cart.objects.filter(user=request.user).first()
    items = list(
        CartItem.objects.select_related("product", "product__store").filter(
            cart=current_cart
        )
        if current_cart
        else CartItem.objects.none()
    )

    if not items:
        messages.error(request, "Your cart is empty!")
        return redirect("shopper_interface:cart")

    order_form = forms.OrderForm(request.POST)
    if not order_form.is_valid():
        for error in order_form.errors.values():
            messages.error(request, error.as_text())
        return redirect("shopper_interface:cart")

    # Lock every involved product row up front. Two concurrent checkouts on
    # the last unit will now serialize here instead of both passing the
    # stock check (bug #2).
    product_ids = sorted({item.product_id for item in items})
    locked_products = Product.objects.select_for_update().in_bulk(product_ids)

    for item in items:
        product = locked_products[item.product_id]
        if not product.is_active or not product.store.enabled:
            messages.error(request, f"{product.name} is no longer available.")
            return redirect("shopper_interface:cart")
        if product.current_stock < item.quantity:
            messages.error(
                request, f"{product.name} has only {product.current_stock} in stock!"
            )
            return redirect("shopper_interface:cart")

    current_order = Order.objects.create(
        user=request.user,
        total_price=current_cart.total_price,
        **order_form.cleaned_data,
    )

    # Fan the cart out into one StoreOrder per merchant, so each store can
    # later update its own fulfillment status independently.
    store_orders = {}
    for item in items:
        product = locked_products[item.product_id]
        store_order = store_orders.get(product.store_id)
        if store_order is None:
            store_order = StoreOrder.objects.create(
                order=current_order, store=product.store
            )
            store_orders[product.store_id] = store_order

        price = product.discounted_price or product.selling_price
        OrderItem.objects.create(
            store_order=store_order,
            product=product,
            quantity=item.quantity,
            price_at_purchase=price,
        )
        product.current_stock -= item.quantity
        product.sold = (product.sold or 0) + item.quantity
        product.save()

    for store_order in store_orders.values():
        store_order.subtotal = sum(
            oi.price_at_purchase * oi.quantity for oi in store_order.items.all()
        )
        store_order.save()

    CartItem.objects.filter(cart=current_cart).delete()
    current_cart.total_price = 0
    current_cart.save()

    messages.success(request, "Order placed successfully!")
    return redirect("shopper_interface:cart")


@login_required
def orders_history(request):
    """List all past orders for the logged-in user, most recent first."""
    orders = Order.objects.filter(user=request.user).prefetch_related(
        "store_orders__items__product", "store_orders__store"
    )

    paginator = Paginator(orders, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "orders/orders_history.html",
        {
            "page_obj": page_obj,
        },
    )


@login_required
def past_order(request, order_id):
    """Show a single past order, scoped to the requesting user so nobody
    can view another user's order by guessing an id."""
    order = get_object_or_404(
        Order.objects.prefetch_related(
            "store_orders__items__product",
            "store_orders__store",
            "store_orders__delivery_person",
        ),
        pk=order_id,
        user=request.user,
    )

    return render(
        request,
        "orders/past_order.html",
        {
            "order": order,
        },
    )
