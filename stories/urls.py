from django.urls import path

from stories import views

urlpatterns = [
    path('', views.story_list, name='stories'),
    path('new/', views.story_create, name='story-create'),
    path('mine/', views.story_mine, name='story-mine'),
    path('<int:pk>/', views.story_detail, name='story-detail'),
    path('<int:pk>/react/', views.story_react, name='story-react'),
    path('<int:pk>/report/', views.story_report, name='story-report'),
    path('<int:pk>/delete/', views.story_delete, name='story-delete'),
]
