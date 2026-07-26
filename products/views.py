from django.shortcuts import render, redirect
from .models import Product, Spec, SuggestedCategory, Category
from .forms import ProductForm, SpecForm, Suggest_Category_Form
from django.contrib import messages
from merchant_interface.models import Membership, Store
from django.contrib.auth.decorators import login_required
from orders.models import Cart, CartItem, Wishlist, WishlistItem

# Create your views here.

@login_required
def Create_Product(request, current_store_id):
    current_store = Store.objects.filter(id=current_store_id).first()
    if not current_store:
        messages.error(request, "Store not found!")
        return redirect('/')

    if not Membership.objects.filter(store=current_store, user=request.user).exists():
        messages.error(request, "You don't have permission to access this page!")
        return redirect('/')

    if request.method == "POST":
        product_form = ProductForm(request.POST, request.FILES)

        if "Create_Product_btn" in request.POST:
            if product_form.is_valid():
                new_product = product_form.save(commit=False)
                new_product.store = current_store
                new_product.save()
                messages.success(request, "Product added successfuly, now lets add some specifications to it!")
                return redirect(f'/products/create_spec/{new_product.id}/')

            else:
                messages.error(request, "Invalid form!")

    else:
        product_form = ProductForm()
        
    context = {
        'product_form' : product_form,
    }

    return render(request, 'products/create_product.html', context)

@login_required
def Create_Spec(request, product_id):
    product = Product.objects.filter(id=product_id).first()
    if not product:
        messages.error(request, "Product not found!")
        return redirect('/')
    
    current_store = product.store
    if not Membership.objects.filter(store=current_store, user=request.user).exists():
        messages.error(request, "You don't have permission to access this page!")
        return redirect('/')

    specs = Spec.objects.filter(product=product).select_related('spec_type')

    if request.method == "POST":
        spec_form = SpecForm(request.POST)

        if "Save_and_Create_Another_Spec_btn" in request.POST:
            if spec_form.is_valid():
                new_spec = spec_form.save(commit=False)
                new_spec.product_id = product_id
                new_spec.save()
                messages.success(request,"Specification added successfuly!")
                return redirect(f'/products/create_spec/{product_id}/')

            else:
                messages.error(request, "Invalid form!")

        else:
            return redirect('/')

    else:
        spec_form = SpecForm()
        
    context = {
        'spec_form' : spec_form,
        'specs' : specs,
    }

    return render(request, 'products/create_spec.html', context)

@login_required
def Update_Product(request, product_id):
    old_product = Product.objects.filter(id=product_id).first()
    if not old_product:
        messages.error(request, "Product not found!")
        return redirect('/')
    
    current_store = old_product.store
    if not Membership.objects.filter(store=current_store, user=request.user).exists():
        messages.error(request, "You don't have permission to access this page!")
        return redirect('/')
    

    specs = Spec.objects.filter(product=old_product).select_related('spec_type')
    
    if request.method == "POST":
        update_product_form = ProductForm(
            request.POST, request.FILES, instance=old_product)
        
        if "Update_Product_btn" in request.POST:
            if update_product_form.is_valid():
                update_product_form.save()
                messages.success(request, "Product updated successfully!")
                return redirect(f'/products/view_product/{product_id}/')

            else:
                messages.error(request, "Invalid form!")

        if 'delete_product_btn' in request.POST:
            old_product.delete()
            messages.success(request, "Product deleted successfuly!")
            return redirect('/')

    else:
        update_product_form = ProductForm(instance=old_product)
        
    context = {
        'update_product_form' : update_product_form,
        'specs' : specs,
    }

    return render(request, 'products/update_product.html', context)

@login_required
def Update_Spec(request, product_id, spec_name):
    old_product = Product.objects.filter(id=product_id).first()
    if not old_product:
        messages.error(request, "Product not found!")
        return redirect('/')

    current_store = old_product.store
    if not Membership.objects.filter(store=current_store, user=request.user).exists():
        messages.error(request, "You don't have permission to access this page!")
        return redirect('/')

    specs = Spec.objects.filter(product=old_product).select_related('spec_type')
    spec = Spec.objects.filter(product=old_product, spec_type__name=spec_name).first()
    if not spec:
        messages.error(request, "Specification not found!")
        return redirect(f'/products/view_product/{product_id}/')

    if request.method == "POST":
        spec_form = SpecForm(request.POST, instance=spec)

        if "Save_and_Create_Another_Spec_btn" in request.POST or "Save_btn" in request.POST:
            if spec_form.is_valid():
                spec_form.save()
                messages.success(request, "Specification updated successfuly!")
                if "Save_and_Create_Another_Spec_btn" in request.POST:
                    return redirect(f'/products/create_spec/{product_id}/')
                    
                return redirect(f'/products/view_product/{product_id}/')

            else:
                messages.error(request, "Invalid form!")

        elif 'delete_spec_btn' in request.POST:
            spec.delete()
            messages.success(request, "Specification deleted successfuly!")
            return redirect(f'/products/view_product/{product_id}/')

    else:
        spec_form = SpecForm(instance=spec)
        
    context = {
        'update_spec_form' : spec_form,
        'specs' : specs,
    }

    return render(request, 'products/update_spec.html', context)

from shopper_interface.models import RecentlyViewed
from shopper_interface.recommendations import get_related_products

def View_Product(request, product_id):
    product = Product.objects.select_related('category', 'store').filter(id=product_id).first()
    if not product:
        messages.error(request, "Product not found!")
        return redirect('/')

    if request.user.is_authenticated:
        rv, _ = RecentlyViewed.objects.get_or_create(user=request.user, product=product)
        rv.save()  # bumps viewed_at + triggers the 'view' Interaction signal

    specs = Spec.objects.filter(product=product).select_related('spec_type')

    context = {
        'product': product,
        'specs': specs,
        'related_products': get_related_products(product, limit=8),
    }

    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "Please log in first.")
            return redirect('/accounts/login/')

        if "add_to_cart_btn" in request.POST:
            cart, _ = Cart.objects.get_or_create(user=request.user)
            cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
            if not created:
                cart_item.quantity += 1
                cart_item.save()
            messages.success(request, f"{product.name} added to cart!")

        elif "add_to_wishlist_btn" in request.POST:
            wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
            WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)
            messages.success(request, f"{product.name} added to wishlist!")

        return redirect('view_product', product_id=product.id)

    return render(request, 'products/view_product.html', context)

@login_required
def Suggest_Category(request):
    if request.method == "POST":
        form = Suggest_Category_Form(request.POST)
        if form.is_valid():
            category_name = form.cleaned_data['category_name']
            if not Category.objects.filter(name=category_name).exists():
                SuggestedCategory.objects.create(name=category_name, suggester=request.user)
                messages.success(request, "Category suggested successfully!")
            else:
                messages.error(request, "Category already exists!")
        else:
            messages.error(request, "Please enter a valid category name!")
        return redirect('/')

    return render(request, 'products/suggest_category.html', {'form': Suggest_Category_Form()})