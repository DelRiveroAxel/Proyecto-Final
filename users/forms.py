from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class LoginForm(AuthenticationForm):
    """Formulario de login. AuthenticationForm ya valida usuario y contraseña."""

    username = forms.CharField(
        label='Usuario o correo',
        widget=forms.TextInput(attrs={
            'class': 'ik-input', 'placeholder': 'ejemplo@iurko.com', 'autofocus': True,
            'autocomplete': 'username',
        }),
    )
    password = forms.CharField(
        label='Contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'ik-input', 'placeholder': '••••••••', 'autocomplete': 'current-password',
            'id': 'password',
        }),
    )
    remember = forms.BooleanField(label='Recordar en este dispositivo', required=False)

    error_messages = {
        'invalid_login': 'Usuario o contraseña incorrectos.',
        'inactive': 'Esta cuenta está desactivada.',
    }

    def clean_username(self):
        # Permite entrar con el correo además del nombre de usuario
        dato = self.cleaned_data['username'].strip()
        if '@' in dato:
            u = User.objects.filter(email__iexact=dato).first()
            if u:
                return u.username
        return dato


class RegistroForm(UserCreationForm):
    """Alta de cliente: crea el User y su perfil de cliente."""

    first_name = forms.CharField(label='Nombre', max_length=150)
    last_name = forms.CharField(label='Apellido', max_length=150)
    email = forms.EmailField(label='Correo electrónico')
    telefono = forms.CharField(label='Teléfono / WhatsApp', max_length=30)
    direccion = forms.CharField(label='Dirección', max_length=200, required=False)
    localidad = forms.CharField(label='Localidad', max_length=100, required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'username']
        labels = {'username': 'Nombre de usuario'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'ik-input')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya hay una cuenta con ese correo.')
        return email

    def save(self, commit=True):
        from tienda.models import Cliente

        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
            Cliente.objects.create(
                user=user,
                telefono=self.cleaned_data['telefono'],
                direccion=self.cleaned_data.get('direccion', ''),
                localidad=self.cleaned_data.get('localidad', ''),
            )
        return user


# ---------------------------------------------------------------------------
# Recuperar / cambiar contraseña (los formularios de Django, con nuestro estilo)
# ---------------------------------------------------------------------------
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm  # noqa: E402


class _ConEstilo:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'ik-input')


class ClaveOlvidadaForm(_ConEstilo, PasswordResetForm):
    email = forms.EmailField(
        label='Correo electrónico', max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True,
                                       'placeholder': 'tu-correo@ejemplo.com'}),
    )

    def get_users(self, email):
        # Igual que Django pero sin distinguir mayúsculas y sin usuarios sin clave usable
        return (u for u in User._default_manager.filter(email__iexact=email.strip(), is_active=True)
                if u.has_usable_password())


class ClaveNuevaForm(_ConEstilo, SetPasswordForm):
    pass


class CambiarClaveForm(_ConEstilo, PasswordChangeForm):
    pass
