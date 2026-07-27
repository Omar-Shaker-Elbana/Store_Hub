from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from .models import Order, OrderItem, Cart, CartItem, Wishlist, WishlistItem
from . import forms
from shopper_interface.recommendations import get_frequently_bought_together

@login_required
def Cart_view(request):
    current_cart, _ = Cart.objects.get_or_create(user=request.user)
    items = CartItem.objects.filter(cart=current_cart)

    for item in items:
        current_product = item.product
        if current_product.current_stock < item.quantity:
            item.quantity = current_product.current_stock
            item.save()
            messages.error(request, f"{item.product.name} has only {item.product.current_stock} in stock!")

    if request.method == "POST":
        item_id = request.POST.get('item_id')
        cart_item = CartItem.objects.filter(id=item_id, cart=current_cart).first()

        if "remove_btn" in request.POST:
            if cart_item:
                cart_item.delete()
                messages.success(request, "Item removed!")
        elif cart_item:
            form = forms.Cart_Item_Form(request.POST, instance=cart_item)
            if form.is_valid():
                form.save()

        return redirect('mycart')

    total = sum((item.quantity * item.product.selling_price) for item in items if item.product.selling_price)
    current_cart.total_price = total
    current_cart.save()

    seen = {item.product_id for item in items}
    suggestions = []
    for item in items:
        for p in get_frequently_bought_together(item.product, limit=3):
            if p.id not in seen:
                suggestions.append(p)
                seen.add(p.id)
        if len(suggestions) >= 6:
            break

    form = forms.Cart_Item_Form()
    context = {'items': items, 'form': form, 'suggestions': suggestions[:6]}
    return render(request, 'orders/cart.html', context)


@login_required
def Wishlist_view(request):
    wishlist, _ = Wishlist.objects.get_or_create(user=request.user)

    if request.method == "POST" and "remove_btn" in request.POST:
        item_id = request.POST.get('item_id')
        WishlistItem.objects.filter(id=item_id, wishlist=wishlist).delete()
        messages.success(request, "Removed from wishlist!")
        return redirect('wishlist')

    items = WishlistItem.objects.filter(wishlist=wishlist)
    return render(request, 'orders/wishlist.html', {'items': items})

@login_required
@transaction.atomic
def Place_Order_View(request):
    current_cart = Cart.objects.filter(user=request.user).first()
    items = CartItem.objects.filter(cart=current_cart) if current_cart else CartItem.objects.none()

    if not items.exists():
        messages.error(request, "Your cart is empty!")
        return redirect('mycart')

    for item in items:
        if item.product.current_stock < item.quantity:
            messages.error(request, f"{item.product.name} has only {item.product.current_stock} in stock!")
            return redirect('mycart')

    current_order = Order.objects.create(
        user=request.user,
        total_price=current_cart.total_price,
        shipping_address=request.POST.get('shipping_address', ''),
    )

    for item in items:
        OrderItem.objects.create(order=current_order, product=item.product, quantity=item.quantity)
        item.product.current_stock -= item.quantity
        item.product.sold = (item.product.sold or 0) + item.quantity
        item.product.save()

    items.delete()
    current_cart.total_price = 0
    current_cart.save()

    messages.success(request, "Order placed successfully!")
    return redirect('mycart')