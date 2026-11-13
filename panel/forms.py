from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory, modelformset_factory
from django.utils import timezone

from produccion.models import (CompraMateriaPrima, IngredienteReceta, LoteMateriaPrima, Proveedor, Receta,
                               TipoMateriaPrima, UnidadMedida)


def _sin_ceros(valor):
    """Decimal('9.000') -> '9' · Decimal('0.030') -> '0.03' (para que no parezca 9 mil)."""
    if isinstance(valor, Decimal):
        return format(valor.normalize(), 'f')
    return valor


class Estilado:
    """Agrega las clases del CSS de Iurko a todos los campos y muestra los decimales sin ceros de más."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instancia = getattr(self, 'instance', None)
        for nombre, f in self.fields.items():
            if isinstance(f, forms.DecimalField):
                f.localize = False
                f.widget.is_localized = False
                actual = self.initial.get(nombre)
                if actual is None and instancia is not None and getattr(instancia, 'pk', None):
                    actual = getattr(instancia, nombre, None)
                if isinstance(actual, Decimal):
                    self.initial[nombre] = _sin_ceros(actual)
            if isinstance(f, forms.ModelChoiceField) and f.empty_label is not None:
                f.empty_label = 'Elegí una opción…'
            w = f.widget
            if isinstance(w, (forms.CheckboxInput, forms.ClearableFileInput, forms.FileInput)):
                continue
            clase = 'ik-select' if isinstance(w, forms.Select) else 'ik-textarea' if isinstance(w, forms.Textarea) else 'ik-input'
            w.attrs.setdefault('class', clase)


class VariasImagenesInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class VariasImagenesField(forms.ImageField):
    """Campo para subir varias fotos de una vez (cada una se valida como imagen, máx. 5 MB)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', VariasImagenesInput(attrs={'accept': 'image/*', 'multiple': True}))
        kwargs.setdefault('required', False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        archivos = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        limpias = []
        for a in archivos:
            img = super().clean(a, initial)
            if img and img.size > 5 * 1024 * 1024:
                raise forms.ValidationError(f'{img.name} pesa más de 5 MB.')
            if img:
                limpias.append(img)
        return limpias


class FotosForm(forms.Form):
    fotos = VariasImagenesField(label='Agregar fotos')


class TandaForm(Estilado, forms.Form):
    receta = forms.ModelChoiceField(queryset=Receta.objects.all(), empty_label='Elegí la receta…')
    fecha_produccion = forms.DateField(label='Fecha de producción', initial=timezone.localdate,
                                       widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    kg_producidos = forms.DecimalField(label='Kilos producidos', min_value=Decimal('0.001'), max_digits=10,
                                       decimal_places=3, widget=forms.NumberInput(attrs={'step': '0.001', 'placeholder': 'Ej.: 80'}))
    cantidad_piezas = forms.IntegerField(label='Cantidad de piezas', min_value=1, max_value=9999,
                                         widget=forms.NumberInput(attrs={'placeholder': 'Ej.: 160'}))
    dias_estacionamiento = forms.IntegerField(
        label='Días de estacionamiento', required=False, min_value=0, max_value=1000,
        help_text='Desde la fecha de producción hasta que se puede vender. Vacío = los que dice la receta.',
        widget=forms.NumberInput(attrs={'placeholder': 'Los de la receta'}))
    dias_vencimiento = forms.IntegerField(
        label='Se vence a los (días)', required=False, min_value=1, max_value=3650,
        help_text='Desde la fecha de producción hasta "consumir antes de". Vacío = lo que dice la receta.',
        widget=forms.NumberInput(attrs={'placeholder': 'Los de la receta'}))
    descontar_insumos = forms.BooleanField(label='Descontar las materias primas del stock (FIFO)', required=False,
                                           initial=True)
    observaciones = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_fecha_produccion(self):
        f = self.cleaned_data['fecha_produccion']
        if f > timezone.localdate():
            raise forms.ValidationError('La fecha de producción no puede ser futura.')
        return f


class PausaForm(Estilado, forms.Form):
    motivo = forms.CharField(label='Motivo', widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Ej.: control de calidad, olor, temperatura de cámara…'}))


class TipoMateriaPrimaForm(Estilado, forms.ModelForm):
    class Meta:
        model = TipoMateriaPrima
        fields = ['nombre', 'unidad', 'stock_minimo']
        help_texts = {'unidad': 'Carnes en kg, condimentos en g, etiquetas y bolsitas en u (unidad).'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unidad'].label_from_instance = lambda u: f'{u.nombre} ({u.abreviatura})'


class CompraForm(Estilado, forms.ModelForm):
    vendedor = forms.CharField(label='Nombre del vendedor', max_length=150,
                               widget=forms.TextInput(attrs={'list': 'vendedores', 'placeholder': 'Ej.: Frigorífico Oberá', 'autocomplete': 'off'}))

    class Meta:
        model = CompraMateriaPrima
        fields = ['fecha', 'tipo_comprobante', 'numero_comprobante', 'comprobante', 'observaciones']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'observaciones': forms.Textarea(attrs={'rows': 2}),
                   'comprobante': forms.FileInput(attrs={'accept': '.pdf,.jpg,.jpeg,.png,.webp'})}

    def clean_comprobante(self):
        f = self.cleaned_data['comprobante']
        if f and f.size > 5 * 1024 * 1024:
            raise forms.ValidationError('El archivo pesa más de 5 MB.')
        return f

    def clean_fecha(self):
        f = self.cleaned_data['fecha']
        if f > timezone.localdate():
            raise forms.ValidationError('La fecha de compra no puede ser futura.')
        return f

    def save(self, commit=True):
        compra = super().save(commit=False)
        compra.proveedor, _ = Proveedor.objects.get_or_create(nombre=self.cleaned_data['vendedor'].strip())
        if commit:
            compra.save()
        return compra


class LoteForm(Estilado, forms.ModelForm):
    class Meta:
        model = LoteMateriaPrima
        fields = ['tipo', 'cantidad', 'precio_unitario', 'lote', 'vencimiento']
        widgets = {'vencimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'cantidad': forms.NumberInput(attrs={'step': '0.001'}),
                   'precio_unitario': forms.NumberInput(attrs={'step': '0.01'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo'].queryset = TipoMateriaPrima.objects.filter(activo=True).select_related('unidad')
        self.fields['tipo'].empty_label = 'Elegí la materia prima…'
        self.fields['tipo'].label_from_instance = lambda t: f'{t.nombre} ({t.unidad})'

    def save(self, commit=True):
        lote = super().save(commit=False)
        if not lote.pk:
            lote.cantidad_disponible = lote.cantidad
        if commit:
            lote.save()
        return lote


LoteFormSet = inlineformset_factory(CompraMateriaPrima, LoteMateriaPrima, form=LoteForm, extra=0, min_num=1,
                                    validate_min=True, can_delete=True)


class RecetaForm(Estilado, forms.ModelForm):
    fotos_extra = VariasImagenesField(label='Más fotos')

    class Meta:
        model = Receta
        fields = ['nombre', 'categoria', 'subtitulo', 'etiqueta', 'descripcion', 'foto', 'precio_kg',
                  'peso_nominal_g', 'presentacion', 'rendimiento_base_kg', 'dias_estacionamiento',
                  'dias_vencimiento', 'dias_aviso_vencimiento', 'stock_minimo_piezas', 'publicada']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3}),
                   'foto': forms.FileInput(attrs={'accept': 'image/*'})}
        labels = {'categoria': 'Categoría', 'subtitulo': 'Subtítulo', 'descripcion': 'Descripción',
                  'presentacion': 'Presentación', 'precio_kg': 'Precio de venta por kg ($)',
                  'foto': 'Foto principal'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dias_aviso_vencimiento'].required = False
        self.fields['dias_vencimiento'].widget.attrs['placeholder'] = 'Sin vencimiento'

    def clean_dias_aviso_vencimiento(self):
        v = self.cleaned_data.get('dias_aviso_vencimiento')
        return 7 if v in (None, '') else v


class IngredienteForm(Estilado, forms.ModelForm):
    class Meta:
        model = IngredienteReceta
        fields = ['tipo', 'cantidad']
        widgets = {'cantidad': forms.NumberInput(attrs={'step': '0.001'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo'].empty_label = 'Elegí la materia prima…'
        self.fields['tipo'].label_from_instance = lambda t: f'{t.nombre} ({t.unidad})'

    # La repetición se controla en el formset (así se puede quitar un ingrediente y volver a agregarlo
    # en otro renglón en el mismo guardado). La restricción de la base igual queda como respaldo.
    def validate_unique(self):
        pass

    def validate_constraints(self):
        pass


class IngredientesBaseFormSet(forms.BaseInlineFormSet):
    def validate_unique(self):
        pass                      # se reemplaza por el control de abajo, con un mensaje más claro

    def clean(self):
        super().clean()
        vistos = set()
        for f in self.forms:
            if not hasattr(f, 'cleaned_data') or self._should_delete_form(f) or not f.cleaned_data.get('tipo'):
                continue
            tipo = f.cleaned_data['tipo']
            if tipo.pk in vistos:
                raise forms.ValidationError(f'{tipo.nombre} está dos veces en la receta.')
            vistos.add(tipo.pk)


IngredienteFormSet = inlineformset_factory(Receta, IngredienteReceta, form=IngredienteForm,
                                           formset=IngredientesBaseFormSet, extra=1, can_delete=True)


class UnidadForm(Estilado, forms.ModelForm):
    class Meta:
        model = UnidadMedida
        fields = ['nombre', 'abreviatura']
        labels = {'nombre': 'Nombre (ej.: Docena)', 'abreviatura': 'Abreviatura (ej.: doc)'}


class NosotrosForm(Estilado, forms.ModelForm):
    class Meta:
        from core.models import PaginaNosotros
        model = PaginaNosotros
        exclude = ['actualizado']
        widgets = {'historia': forms.Textarea(attrs={'rows': 7}), 'mision': forms.Textarea(attrs={'rows': 3}),
                   'valores': forms.Textarea(attrs={'rows': 4}), 'equipo': forms.Textarea(attrs={'rows': 4}),
                   'foto': forms.FileInput(attrs={'accept': 'image/*'})}
        help_texts = {'equipo': 'Una persona por línea: Nombre — Rol', 'bajada': 'La frase corta debajo del título.',
                      'anio_fundacion': 'Se muestra en el sello "Desde …" de la tienda.'}


class PreguntaForm(Estilado, forms.ModelForm):
    class Meta:
        from core.models import PreguntaFrecuente
        model = PreguntaFrecuente
        fields = ['pregunta', 'respuesta', 'orden', 'visible']
        widgets = {'respuesta': forms.Textarea(attrs={'rows': 3})}


def preguntas_formset():
    from core.models import PreguntaFrecuente
    return modelformset_factory(PreguntaFrecuente, form=PreguntaForm, extra=1, can_delete=True)


class SlideForm(Estilado, forms.ModelForm):
    class Meta:
        from core.models import SlideInicio
        model = SlideInicio
        fields = ['receta', 'etiqueta', 'icono', 'titulo', 'texto', 'imagen', 'boton_texto', 'boton_url', 'activo']
        widgets = {'texto': forms.Textarea(attrs={'rows': 3, 'maxlength': 200}),
                   'imagen': forms.FileInput(attrs={'accept': 'image/*'}),
                   'titulo': forms.TextInput(attrs={'maxlength': 60}),
                   'etiqueta': forms.TextInput(attrs={'maxlength': 40})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['receta'].empty_label = 'Ninguno (solo texto)'
        self.fields['receta'].required = False

    def clean_imagen(self):
        img = self.cleaned_data.get('imagen')
        if img and hasattr(img, 'size') and img.size > 5 * 1024 * 1024:
            raise forms.ValidationError('La foto pesa más de 5 MB.')
        return img

    def clean_boton_url(self):
        url = self.cleaned_data.get('boton_url', '').strip()
        if url and not (url.startswith('/') or url.startswith('https://') or url.startswith('http://')):
            raise forms.ValidationError('Tiene que empezar con / (una página de la tienda) o con https://')
        return url


class SelloForm(Estilado, forms.ModelForm):
    class Meta:
        from core.models import PaginaNosotros
        model = PaginaNosotros
        fields = ['mostrar_sello', 'anio_fundacion', 'sello_linea']
        labels = {'anio_fundacion': 'Año (Desde …)'}
