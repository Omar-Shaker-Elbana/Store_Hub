# from django.shortcuts import render, redirect
# from django.contrib import messages
# from .models import Order, OrderItem, Cart, CartItem, Wishlist, WishlistItem
# import forms

# # Create your views here.

# def Cart_view(request):
#     current_cart = Cart.objects.filter(user = request.user).first()
#     items = CartItem.objects.filter(cart = current_cart)
#     for item in items:
#         curent_product = item.product
#         if curent_product.current_stock < item.quantity:
#             item.quantity = curent_product.current_stock
#             messages.error(request, f"{item.product.name} has only {item.product.current_stock} in stock!")
    
#     if request.method == "POST":
#         form = forms.Cart_Item_Form(request.POST, instance=request.user.)

#         if form.is_valid():
#             form.save()
            
#         if "remove_btn" in request.POST:
#             item_id = request.POST.get('item_id')
#             item = CartItem.objects.get(id=item_id)
#             item.delete()
#             messages.success(request, "Item removed!")

#     else:
#         form = forms.Cart_Item_Form(instance = )

#     context = {'items': items,
#                'form' : form}
#     return render(request, 'orders/cart.html', context)

# def Wishlist_view(request):
#     items = Wishlist.objects.filter(user = request.user)
#     return render(request, 'orders/wishlist.html', {'items': items})

# def Place_Order_View(request):
#     current_cart = Cart.objects.get(user = request.user)
#     items = CartItem.objects.filter(cart = current_cart)
#     order_list = []
#     for item in items:
#         item_product = item.product
#         if item_product.current_stock < item.quantity:
#             messages.error(request,f"{item.product.name} has only {item.product.current_stock} in stock!")
#             return redirect('/cart/mycart')
#         order_list.append(OrderItem.objects.create(product=item_product, quantity = item.quantity))

#     current_order = Order.objects.create(user=request.user, total = request.user.cart.total_price)
#     for item in order_list:

