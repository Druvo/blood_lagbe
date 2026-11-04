"""URLconf used only by pages.tests to prove the 500 page is served."""
from django.urls import path


def boom(request):
    raise RuntimeError('boom')


urlpatterns = [path('boom/', boom)]
