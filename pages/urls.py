from django.urls import path

from . import seo, views


urlpatterns = [
    path('', views.index, name="home"),
    path('learn/', views.learn, name='learn'),
    path('about/', views.about, name='about'),
    path('faq/', views.faq, name='faq'),
    path('offline/', seo.offline, name='offline'),
]
