from django.shortcuts import render

from shopper_interface.recommendations import get_recommendations_for_user

# Create your views here.

def home(request):
    recommended_products = get_recommendations_for_user(request.user)

    return render(request,"shopper_interface/home.html",{"recommended_products": recommended_products,})