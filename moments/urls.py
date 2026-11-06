from django.urls import path

from moments import views

app_name = 'moments'

urlpatterns = [
    path('request/<int:pk>/pulse/', views.pulse, name='pulse'),
    path('request/<int:pk>/thank/', views.thank_send, name='thank-send'),
    path('thanks/', views.thanks_wall, name='thanks'),
    path('note/<int:pk>/hide/', views.note_hide, name='note-hide'),
    path('update/<int:pk>/dismiss/', views.update_dismiss, name='update-dismiss'),
    path('prefs/', views.prefs, name='prefs'),
    path('freeze/', views.freeze, name='freeze'),
    path('off/<str:token>/', views.reminder_off, name='reminder-off'),
]
