from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import CambiarClaveForm, ClaveNuevaForm, ClaveOlvidadaForm

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),
    path('inicio/', views.inicio_segun_rol, name='inicio_segun_rol'),

    # Recuperar la contraseña por correo (vistas de Django con plantillas propias)
    path('clave/olvide/', auth_views.PasswordResetView.as_view(
        template_name='users/clave_olvidada.html',
        form_class=ClaveOlvidadaForm,
        email_template_name='users/email_clave.txt',
        subject_template_name='users/email_clave_asunto.txt',
        success_url=reverse_lazy('clave_enviada'),
    ), name='clave_olvidada'),
    path('clave/enviada/', auth_views.PasswordResetDoneView.as_view(
        template_name='users/clave_enviada.html'), name='clave_enviada'),
    path('clave/nueva/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='users/clave_nueva.html',
        form_class=ClaveNuevaForm,
        success_url=reverse_lazy('clave_lista'),
    ), name='clave_nueva'),
    path('clave/lista/', auth_views.PasswordResetCompleteView.as_view(
        template_name='users/clave_lista.html'), name='clave_lista'),

    # Cambiar la contraseña estando logueado
    path('clave/cambiar/', views.cambiar_clave, name='clave_cambiar'),
]
