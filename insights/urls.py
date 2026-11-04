from django.urls import path

from insights import views

urlpatterns = [
    path('', views.impact, name='impact'),
    path('data.json', views.data_json, name='impact-data'),
    path('export.csv', views.export_csv, name='impact-export'),
    path(
        'district/<str:name>/', views.district_detail,
        name='impact-district',
    ),
]
