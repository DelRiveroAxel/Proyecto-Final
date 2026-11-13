from django import forms

from .models import ConsultaContacto


class ContactoForm(forms.ModelForm):
    """Formulario de contacto de la tienda. 'sitio' es una trampa para robots (queda oculto)."""

    sitio = forms.CharField(required=False, widget=forms.TextInput(attrs={'tabindex': '-1', 'autocomplete': 'off'}))

    class Meta:
        model = ConsultaContacto
        fields = ['nombre', 'email', 'telefono', 'mensaje']
        labels = {'nombre': 'Tu nombre', 'email': 'Tu correo', 'telefono': 'Teléfono (opcional)', 'mensaje': 'Tu consulta'}
        widgets = {
            'nombre': forms.TextInput(attrs={'autocomplete': 'name', 'placeholder': 'Nombre y apellido'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'tu-correo@ejemplo.com'}),
            'telefono': forms.TextInput(attrs={'autocomplete': 'tel', 'placeholder': 'Para responderte por WhatsApp'}),
            'mensaje': forms.Textarea(attrs={'rows': 5, 'maxlength': 2000,
                                             'placeholder': 'Contanos qué necesitás: un pedido grande, un evento, una duda…'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, f in self.fields.items():
            if nombre != 'sitio':
                f.widget.attrs.setdefault('class', 'ik-input')

    def clean_mensaje(self):
        m = self.cleaned_data['mensaje'].strip()
        if len(m) < 10:
            raise forms.ValidationError('Contanos un poco más (al menos 10 letras).')
        if len(m) > 2000:
            raise forms.ValidationError('El mensaje es muy largo (máximo 2000 letras).')
        return m

    def es_robot(self):
        return bool(self.cleaned_data.get('sitio'))
