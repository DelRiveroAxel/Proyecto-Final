from django.urls import path #importo la funcion path para definir las urls
from . import views #importo las vistas del archivo views.py

#defino las urls de la aplicacion users
urlpatterns = [ 
    path('login/', views.login_view, name='login'), #url para la vista de login
    path('logout/', views.logout_view, name='logout'), #url para la vista de logout
]