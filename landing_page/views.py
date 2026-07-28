from django.shortcuts import redirect, render
# from shopper_interface.recommendations import get_recommendations_for_user

# Create your views here.
def home(request):
    # recommended_products = get_recommendations_for_user(request.user, limit=12)
    if request.user.is_authenticated:
        return redirect('shopper_interface:home')   
    return render(request, 'landing/index.html')