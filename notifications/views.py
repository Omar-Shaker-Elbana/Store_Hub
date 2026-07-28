from django.shortcuts import render
from django.contrib.auth.decorators import login_required
# Create your views here.

@login_required
def notifications(request):
    read_messages = request.user.notifications.filter(is_read=True)
    unread_messages = request.user.notifications.filter(is_read=False)
    context = {
        'read_messages': read_messages,
        'unread_messages': unread_messages,
    }   
    
    return render(request, 'notifications/notifications.html', context)