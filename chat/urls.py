from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('start/<int:user_id>/', views.start_conversation, name='start_conversation'),
    path('conversation/<int:conversation_id>/', views.conversation_detail, name='conversation_detail'),
    path('conversation/<int:conversation_id>/attachment/', views.send_direct_attachment, name='send_direct_attachment'),
    path('store/<int:store_id>/announcements/', views.store_announcements, name='store_announcements'),
    path('store/<int:store_id>/announcements/post/', views.post_announcement, name='post_announcement'),
]
