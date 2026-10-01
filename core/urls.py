from django.urls import path #importo la funcion path para definir las urls
from . import views #importo las vistas del app core

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard')
]