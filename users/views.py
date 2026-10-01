from django.shortcuts import render, redirect #importo la funcion render para renderizar las vistas y redirect para redireccionar a otras vistas
from django.contrib import authenticate, login, logout #importo las funciones de autenticacion de django
from django.contrib.auth.decorators import login_required #importo el decorador login_required para proteger las vistas que requieren autenticacion
from django.contrib import messages #importo la clase messages para mostrar mensajes en las vistas
from .forms import loginForm #importo el formulario de login que cree en forms.py
from .models import UserRole, Role #importo los modelos de usuario y rol que cree en models.py


def login_view(request): #creo la vista para el login
    if request.user.is_authenticated:       #si el usuario ya esta autenticado
        return redirect('dashboard')        #redirecciono a la vista dashboard

    if request.method == 'POST':            #si el metodo de la peticion es POST
        form = loginForm(request, data=request.POST) #creo una instancia del formulario de login con los datos del POST
        if form.is_valid():                   #si el formulario es valido
            username = form.cleaned_data.get('username')    #obtengo el nombre de usuario del formulario
            password = form.cleaned_data.get('password')    #obtengo la contraseña del formulario
            user = authenticate(request, username=username, password=password) #autentico al usuario con los datos del formulario
            
            if user is not None:              #si el usuario existe
                login(request, user)          #inicio sesion con el usuario autenticado
                return redirect('dashboard')   #redirecciono a la vista dashboard

            else:                               #si el usuario no existe
                messages.error(request, 'Usuario o contraseña incorrectos') #muestro un mensaje de error

        else:                                   #si el metodo de la peticion no es POST
            form = loginForm()                 #creo una instancia vacia del formulario de login

        return render(request, 'users/login.html', {'form': form}) #renderizo la vista de login con el formulario

@login_required(login_url='login') #decorador para proteger la vista, si el usuario no esta autenticado lo redirecciona a la vista de login
def  logout_view(request): #creo la vista para el logout
    logout(request) #cierro la sesion del usuario
    messages.success(request, 'Has cerrado sesión correctamente') #muestro un mensaje de exito
    return redirect('login') #redirecciono a la vista de login