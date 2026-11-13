from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import CambiarClaveForm, LoginForm, RegistroForm


def _destino(request, user):
    """A dónde mandar al usuario después de entrar."""
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return 'panel:inicio' if user.is_staff else 'tienda:catalogo'


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_destino(request, request.user))

    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():                  # AuthenticationForm ya autenticó al usuario
            user = form.get_user()
            login(request, user)
            if not form.cleaned_data.get('remember'):
                request.session.set_expiry(0)  # se cierra al cerrar el navegador
            return redirect(_destino(request, user))
    else:
        form = LoginForm(request)            # GET: formulario vacío

    return render(request, 'users/login.html', {'form': form, 'next': request.GET.get('next', '')})


def registro_view(request):
    if request.user.is_authenticated:
        return redirect('tienda:catalogo')

    if request.method == 'POST':
        form = RegistroForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'¡Bienvenido/a, {user.first_name}! Ya podés hacer tus pedidos.')
            return redirect(_destino(request, user))
    else:
        form = RegistroForm()

    return render(request, 'users/registro.html', {'form': form, 'next': request.GET.get('next', '')})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('tienda:catalogo')


@login_required
def inicio_segun_rol(request):
    return redirect('panel:inicio' if request.user.is_staff else 'tienda:catalogo')


@login_required
def cambiar_clave(request):
    form = CambiarClaveForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)   # no lo deslogueamos
        messages.success(request, 'Listo, tu contraseña se cambió.')
        return redirect('panel:inicio' if request.user.is_staff else 'tienda:mis_pedidos')
    return render(request, 'users/clave_cambiar.html', {'form': form})
