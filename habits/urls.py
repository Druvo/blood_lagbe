from django.urls import path

from habits import views

app_name = 'habits'

urlpatterns = [
    path('', views.today, name='today'),
    path('share/<int:request_pk>/', views.share, name='share'),
    path('availability/', views.availability, name='availability'),
    path('fact/', views.read_fact, name='fact'),
    path('quiz/', views.quiz, name='quiz'),
    path('titles/', views.titles_page, name='titles'),
    path('settings/', views.settings_page, name='settings'),
    path('unsubscribe/<str:token>/', views.unsubscribe, name='unsubscribe'),
    path('year/', views.year_default, name='year'),
    path('year/<int:year>/', views.year_detail, name='year-detail'),
    path('year/<int:year>/card/', views.year_card, name='year-card'),
    path(
        'year/<int:year>/card/<int:user_id>/', views.year_card_user,
        name='year-card-user',
    ),
]
