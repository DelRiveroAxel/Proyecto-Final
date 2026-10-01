from django import forms #importo la clase formularios de django
from django.contrib.auth.forms import AuthenticationForm #clase de autenticacion de django
from .models import User #clase de usuario que cree en models.py

class loginForm(AuthenticationForm): #creo la clase para el formulario de login que hereda de la clase AuthenticationForm
    
    username = forms.CharField(
        label='Usuario', 
        widget=forms.TextInput(attrs={'class':'form-control', 'placeholder': 'Ingrese su usuario'
        })) #creo el campo de usuario con un label y un widget de tipo TextInput con una clase de bootstrap

    password = forms.CharField(
            label='Contraseña',
            widget=forms.PasswordInput(attrs={'class':'form-control', 'placeholder': 'Ingrese su contraseña'
            })) #creo el campo de contraseña con un label y un widget de tipo PasswordInput con una clase de bootstrap

    class Meta:
        model = User #le indico que el modelo que voy a usar es el modelo de usuario que cree en models.py
        fields = ['username', 'password'] #le indico los campos que voy a usar en el formulario

        