from django.urls import path

from atlas import views

app_name = 'atlas'

urlpatterns = [
    path('', views.map_view, name='map'),
    path('gap/', views.gap_view, name='gap'),
    path('divisions/', views.divisions_view, name='divisions'),
    path('data.csv', views.data_csv, name='data-csv'),
    path('d/<slug:slug>/', views.district_view, name='district'),
]
