"""
Producción e inventario de Iurko Chacinados.

Flujo:
  1. El productor carga COMPRAS de materia prima (fecha, vendedor, ticket/factura).
     Cada renglón de la compra es un LOTE con su cantidad disponible.
  2. Una RECETA dice qué ingredientes lleva cada X kg de producto.
  3. Al cargar una TANDA (ej. 80 kg de la receta 1) se descuentan los ingredientes
     de los lotes más viejos (FIFO) y se generan las PIEZAS (ej. 160).
  4. Las piezas se venden en orden FIFO: primero las de la tanda más vieja.

Códigos (solo números):
  Tanda:  DDMMAAAATT        -> 0511202601      (10 dígitos, TT = n.º de tanda de ese día, 01-99)
  Pieza:  DDMMAAAATTNNNN    -> 05112026010021  (14 dígitos, NNNN = n.º de pieza en la tanda)
  El orden FIFO se calcula con la fecha real, NO con el texto del código
  (como texto, "01122026" quedaría antes que "05112026").
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.text import slugify

def almacen_privado():
    """Los tickets y facturas no son públicos: se guardan fuera de /media/.
    Es una función para que la ruta se calcule en cada computadora (no quede fija en la migración)."""
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)
DEC0 = Decimal('0')


def hoy():
    return timezone.localdate()


# ---------------------------------------------------------------------------
# Materias primas
# ---------------------------------------------------------------------------

class UnidadMedida(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    abreviatura = models.CharField(max_length=10, unique=True)

    class Meta:
        db_table = 'unidades_medida'
        verbose_name = 'Unidad de medida'
        verbose_name_plural = 'Unidades de medida'
        ordering = ['nombre']

    def __str__(self):
        return self.abreviatura


class TipoMateriaPrima(models.Model):
    """Ej.: Carne de cerdo, Tocino, Sal, Pimentón, Tripa natural."""

    nombre = models.CharField(max_length=100, unique=True)
    unidad = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    stock_minimo = models.DecimalField(
        'Stock mínimo (aviso)', max_digits=10, decimal_places=3, default=DEC0,
        validators=[MinValueValidator(DEC0)],
    )
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'tipo_materia_prima'
        verbose_name = 'Materia prima'
        verbose_name_plural = 'Materias primas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    @property
    def stock(self):
        """Cantidad disponible sumando todos sus lotes."""
        if hasattr(self, 'stock_anotado'):
            return self.stock_anotado or DEC0
        return self.lotes.aggregate(s=Sum('cantidad_disponible'))['s'] or DEC0

    @property
    def bajo_minimo(self):
        return self.stock_minimo > 0 and self.stock < self.stock_minimo


class Proveedor(models.Model):
    """El vendedor de la materia prima (frigorífico, distribuidora, etc.)."""

    nombre = models.CharField(max_length=150, unique=True)
    cuit = models.CharField('CUIT', max_length=20, blank=True)
    telefono = models.CharField(max_length=30, blank=True)

    class Meta:
        db_table = 'proveedores'
        verbose_name_plural = 'Proveedores'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


def ruta_comprobante(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    return f'comprobantes/{instance.fecha:%Y/%m}/compra_{instance.fecha:%Y%m%d}_{timezone.now():%H%M%S}.{ext}'


class CompraMateriaPrima(models.Model):
    """Una compra = un ticket o factura, con uno o más renglones (lotes)."""

    TIPOS_COMPROBANTE = [('TICKET', 'Ticket'), ('FACTURA', 'Factura'), ('REMITO', 'Remito'), ('OTRO', 'Otro')]

    fecha = models.DateField('Fecha de compra', default=hoy)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name='compras',
                                  verbose_name='Vendedor')
    tipo_comprobante = models.CharField(max_length=10, choices=TIPOS_COMPROBANTE, default='FACTURA')
    numero_comprobante = models.CharField('N.º de comprobante', max_length=50, blank=True)
    comprobante = models.FileField(
        'Ticket o factura', upload_to=ruta_comprobante, storage=almacen_privado,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
    )
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+')

    class Meta:
        db_table = 'compras_materia_prima'
        verbose_name = 'Compra de materia prima'
        verbose_name_plural = 'Compras de materia prima'
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'Compra #{self.pk} · {self.proveedor} · {self.fecha:%d/%m/%Y}'

    @property
    def total(self):
        return sum((l.subtotal for l in self.lotes.all()), DEC0)

    @property
    def es_pdf(self):
        return self.comprobante.name.lower().endswith('.pdf')


class LoteMateriaPrima(models.Model):
    """Renglón de una compra. Se va consumiendo en orden FIFO por las tandas."""

    compra = models.ForeignKey(CompraMateriaPrima, on_delete=models.CASCADE, related_name='lotes')
    tipo = models.ForeignKey(TipoMateriaPrima, on_delete=models.PROTECT, related_name='lotes',
                             verbose_name='Materia prima')
    lote = models.CharField('Lote del proveedor', max_length=50, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=3,
                                   validators=[MinValueValidator(Decimal('0.001'))])
    cantidad_disponible = models.DecimalField(max_digits=10, decimal_places=3, default=DEC0)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(DEC0)])
    vencimiento = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'lotes_materia_prima'
        verbose_name = 'Lote de materia prima'
        verbose_name_plural = 'Lotes de materia prima'
        ordering = ['compra__fecha', 'id']           # FIFO
        constraints = [
            models.CheckConstraint(
                condition=Q(cantidad_disponible__gte=0) & Q(cantidad_disponible__lte=models.F('cantidad')),
                name='chk_lote_disponible',
            ),
        ]

    def __str__(self):
        return f'{self.tipo} · {self.cantidad_disponible}/{self.cantidad} {self.tipo.unidad}'

    @property
    def subtotal(self):
        return (self.cantidad * self.precio_unitario).quantize(Decimal('0.01'))


# ---------------------------------------------------------------------------
# Recetas (= productos que se venden en la tienda)
# ---------------------------------------------------------------------------

class Receta(models.Model):
    CATEGORIAS = [
        ('CURADO', 'Curado / estacionado'),
        ('AHUMADO', 'Ahumado'),
        ('COCIDO', 'Cocido'),
        ('FRESCO', 'Embutido fresco'),
        ('OTRO', 'Otro'),
    ]

    nombre = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    categoria = models.CharField(max_length=10, choices=CATEGORIAS, default='CURADO')
    etiqueta = models.CharField('Etiqueta corta', max_length=40, blank=True,
                                help_text='Se ve sobre la foto. Ej.: "18 meses cava", "Picado grueso".')
    subtitulo = models.CharField(max_length=60, blank=True, help_text='Ej.: "Receta colonial 1920".')
    descripcion = models.TextField(blank=True)
    foto = models.ImageField(upload_to='recetas/', blank=True)

    precio_kg = models.DecimalField('Precio de venta por kg', max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(DEC0)])
    peso_nominal_g = models.PositiveIntegerField(
        'Peso aproximado por pieza (g)', validators=[MinValueValidator(1)],
        help_text='Se muestra en la tienda como presentación aproximada.',
    )
    presentacion = models.CharField(max_length=60, blank=True, help_text='Ej.: "Pieza entera al vacío".')
    rendimiento_base_kg = models.DecimalField(
        'Los ingredientes son para (kg)', max_digits=8, decimal_places=3, default=Decimal('1'),
        validators=[MinValueValidator(Decimal('0.001'))],
        help_text='Ej.: si escribís los ingredientes para 10 kg de producto, poné 10.',
    )
    dias_estacionamiento = models.PositiveIntegerField(
        'Días de estacionamiento', default=0,
        help_text='Desde que se carga la tanda hasta que se puede vender.',
    )
    dias_vencimiento = models.PositiveIntegerField(
        'Se vence a los (días)', null=True, blank=True,
        help_text='Días desde que se produce hasta "consumir antes de". Vacío = no se controla el vencimiento.',
    )
    dias_aviso_vencimiento = models.PositiveIntegerField(
        'Avisar antes del vencimiento (días)', default=7,
        help_text='Con cuántos días de anticipación aparece el aviso en el panel.',
    )
    publicada = models.BooleanField('Visible en la tienda', default=True)
    stock_minimo_piezas = models.PositiveIntegerField(
        'Avisar con menos de (piezas)', default=5,
        help_text='Si quedan menos piezas a la venta, aparece la alerta en Inventario.',
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'recetas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nombre)[:150] or 'receta'
            slug, n = base, 2
            while Receta.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f'{base}-{n}', n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('tienda:producto', args=[self.slug])

    @property
    def foto_principal(self):
        """La foto que se ve en la tarjeta del catálogo (o None)."""
        if self.foto:
            return self.foto
        extra = self.fotos.first()
        if extra:
            return extra.imagen
        tf = FotoTanda.objects.filter(tanda__receta=self).order_by('-tanda__fecha_produccion', 'id').first()
        return tf.imagen if tf else None

    def galeria(self):
        """Fotos para la tienda: principal + extras de la receta + las de las tandas que están a la venta."""
        fotos = [self.foto] if self.foto else []
        fotos += [f.imagen for f in self.fotos.all()]
        fotos += [f.imagen for f in FotoTanda.objects.filter(
            tanda__receta=self, tanda__estado=Tanda.ACTIVA, tanda__fecha_disponible__lte=hoy()
        ).exclude(tanda__fecha_vencimiento__lte=hoy()).order_by('tanda__fecha_produccion', 'tanda__numero_dia', 'id')]
        return fotos

    @property
    def precio_pieza_estimado(self):
        return (self.precio_kg * self.peso_nominal_g / 1000).quantize(Decimal('1'))

    def ingredientes_para(self, kg):
        """[(ingrediente, cantidad necesaria)] para producir `kg` kilos."""
        factor = Decimal(kg) / self.rendimiento_base_kg
        return [(i, (i.cantidad * factor).quantize(Decimal('0.001'))) for i in self.ingredientes.select_related('tipo__unidad')]

    # --- stock para la tienda ---
    def piezas_a_la_venta(self):
        return Pieza.objects.a_la_venta().filter(tanda__receta=self)

    def proxima_tanda(self):
        """La tanda que todavía está estacionando y sale primero (para avisar 'disponible desde')."""
        return (self.tandas.filter(estado=Tanda.ACTIVA, fecha_disponible__gt=hoy())
                .order_by('fecha_disponible').first())


class FotoReceta(models.Model):
    """Fotos extra de la receta (la principal es Receta.foto). Las usan todas sus tandas."""

    receta = models.ForeignKey(Receta, on_delete=models.CASCADE, related_name='fotos')
    imagen = models.ImageField(upload_to='recetas/')
    orden = models.PositiveSmallIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fotos_receta'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'Foto de {self.receta}'


class IngredienteReceta(models.Model):
    receta = models.ForeignKey(Receta, on_delete=models.CASCADE, related_name='ingredientes')
    tipo = models.ForeignKey(TipoMateriaPrima, on_delete=models.PROTECT, verbose_name='Materia prima')
    cantidad = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])

    class Meta:
        db_table = 'recetas_detalles'
        verbose_name = 'Ingrediente'
        constraints = [models.UniqueConstraint(fields=['receta', 'tipo'], name='uq_receta_ingrediente')]

    def __str__(self):
        return f'{self.tipo} {self.cantidad} {self.tipo.unidad}'


# ---------------------------------------------------------------------------
# Tandas de producción y piezas
# ---------------------------------------------------------------------------

class TandaQuerySet(models.QuerySet):
    def fifo(self):
        return self.order_by('fecha_produccion', 'numero_dia')

    def con_stock(self):
        return self.annotate(
            piezas_disp=Count('piezas', filter=Q(piezas__estado=Pieza.DISPONIBLE)),
            piezas_vend=Count('piezas', filter=Q(piezas__estado=Pieza.VENDIDA)),
            piezas_res=Count('piezas', filter=Q(piezas__estado=Pieza.RESERVADA)),
            peso_disp_g=Sum('piezas__peso_g', filter=Q(piezas__estado=Pieza.DISPONIBLE)),
        )


class Tanda(models.Model):
    ACTIVA, PAUSADA, AGOTADA, ANULADA = 'ACTIVA', 'PAUSADA', 'AGOTADA', 'ANULADA'
    ESTADOS = [
        (ACTIVA, 'Activa'),          # se vende (o está estacionando hasta fecha_disponible)
        (PAUSADA, 'Pausada'),        # problema de calidad: no se vende ninguna pieza
        (AGOTADA, 'Agotada'),        # se vendieron todas las piezas
        (ANULADA, 'Anulada'),        # se descartó la tanda entera
    ]

    receta = models.ForeignKey(Receta, on_delete=models.PROTECT, related_name='tandas')
    fecha_produccion = models.DateField(default=hoy)
    numero_dia = models.PositiveSmallIntegerField('N.º de tanda del día', validators=[MinValueValidator(1)])
    codigo = models.CharField(max_length=10, unique=True, editable=False)
    kg_producidos = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    cantidad_piezas = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fecha_disponible = models.DateField('A la venta desde')
    fecha_vencimiento = models.DateField('Consumir antes de', null=True, blank=True,
                                         help_text='El día que vence ya no se vende.')
    estado = models.CharField(max_length=10, choices=ESTADOS, default=ACTIVA)
    motivo_pausa = models.TextField(blank=True)
    pausada_en = models.DateTimeField(null=True, blank=True)
    costo_insumos = models.DecimalField(max_digits=14, decimal_places=2, default=DEC0)
    desconto_insumos = models.BooleanField('Se descontaron insumos', default=True)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+')

    objects = TandaQuerySet.as_manager()

    class Meta:
        db_table = 'tandas_produccion'
        verbose_name = 'Tanda de producción'
        verbose_name_plural = 'Tandas de producción'
        ordering = ['fecha_produccion', 'numero_dia']    # FIFO
        constraints = [
            models.UniqueConstraint(fields=['fecha_produccion', 'numero_dia'], name='uq_tanda_dia'),
            models.CheckConstraint(condition=Q(numero_dia__lte=99), name='chk_tanda_numero_dia'),
        ]

    def __str__(self):
        return f'{self.codigo_fmt} · {self.receta}'

    @staticmethod
    def armar_codigo(fecha, numero_dia):
        return f'{fecha:%d%m%Y}{numero_dia:02d}'

    def save(self, *args, **kwargs):
        self.codigo = self.armar_codigo(self.fecha_produccion, self.numero_dia)
        super().save(*args, **kwargs)

    @property
    def codigo_fmt(self):
        return self.codigo

    @property
    def costo_kg(self):
        return (self.costo_insumos / self.kg_producidos).quantize(Decimal('1')) if self.kg_producidos else DEC0

    @property
    def costo_pieza(self):
        return (self.costo_insumos / self.cantidad_piezas).quantize(Decimal('1')) if self.cantidad_piezas else DEC0

    @property
    def estacionando(self):
        return self.estado == self.ACTIVA and self.fecha_disponible > hoy()

    @property
    def vencida(self):
        return bool(self.fecha_vencimiento) and self.fecha_vencimiento <= hoy()

    @property
    def dias_para_vencer(self):
        return (self.fecha_vencimiento - hoy()).days if self.fecha_vencimiento else None

    @property
    def por_vencer(self):
        """Todavía se vende pero entra en el período de aviso de la receta."""
        d = self.dias_para_vencer
        return d is not None and 0 < d <= self.receta.dias_aviso_vencimiento

    @property
    def a_la_venta(self):
        return self.estado == self.ACTIVA and self.fecha_disponible <= hoy() and not self.vencida

    def etiqueta_estado(self):
        """(texto, clase css) para mostrar el estado."""
        if self.estado == self.ACTIVA and self.vencida:
            return f'Vencida el {self.fecha_vencimiento:%d/%m} · no se vende', 'ik-est-err'
        if self.estacionando:
            return f'Estacionando hasta {self.fecha_disponible:%d/%m}', 'ik-est-wait'
        return {
            self.ACTIVA: ('Disponible', 'ik-est-ok'),
            self.PAUSADA: ('Pausada', 'ik-est-pause'),
            self.AGOTADA: ('Agotada', 'ik-est-info'),
            self.ANULADA: ('Anulada', 'ik-est-err'),
        }[self.estado]


class PiezaQuerySet(models.QuerySet):
    def fifo(self):
        return self.order_by('tanda__fecha_produccion', 'tanda__numero_dia', 'numero')

    def disponibles(self):
        """Piezas libres de tandas activas (incluye las que están estacionando)."""
        return self.filter(estado=Pieza.DISPONIBLE, tanda__estado=Tanda.ACTIVA).fifo()

    def no_vencidas(self):
        """El día del vencimiento ya no se vende."""
        return self.filter(Q(tanda__fecha_vencimiento__isnull=True) | Q(tanda__fecha_vencimiento__gt=hoy()))

    def a_la_venta(self):
        """Lo que se puede vender hoy en la tienda (ya estacionado y sin vencer)."""
        return self.disponibles().filter(tanda__fecha_disponible__lte=hoy()).no_vencidas()


class Pieza(models.Model):
    DISPONIBLE, RESERVADA, VENDIDA, BAJA = 'DISPONIBLE', 'RESERVADA', 'VENDIDA', 'BAJA'
    ESTADOS = [(DISPONIBLE, 'Disponible'), (RESERVADA, 'Reservada (pago en curso)'),
               (VENDIDA, 'Vendida'), (BAJA, 'Dada de baja')]

    tanda = models.ForeignKey(Tanda, on_delete=models.CASCADE, related_name='piezas')
    numero = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    codigo = models.CharField(max_length=14, unique=True, editable=False)
    peso_g = models.PositiveIntegerField('Peso (g)', validators=[MinValueValidator(1)])
    estado = models.CharField(max_length=10, choices=ESTADOS, default=DISPONIBLE)
    motivo_baja = models.CharField('Motivo / detalle', max_length=200, blank=True)
    # Venta registrada a mano (WhatsApp, en el local, feria...): no tiene pedido de la tienda
    venta_externa_fecha = models.DateField('Vendida por fuera de la web el', null=True, blank=True)
    venta_externa_monto = models.DecimalField('Importe de la venta externa', max_digits=12, decimal_places=2,
                                              null=True, blank=True)

    objects = PiezaQuerySet.as_manager()

    class Meta:
        db_table = 'piezas'
        ordering = ['tanda__fecha_produccion', 'tanda__numero_dia', 'numero']   # FIFO
        constraints = [
            models.UniqueConstraint(fields=['tanda', 'numero'], name='uq_pieza_tanda_numero'),
            models.CheckConstraint(condition=Q(numero__lte=9999), name='chk_pieza_numero'),
        ]
        indexes = [models.Index(fields=['estado', 'tanda'])]

    def __str__(self):
        return self.codigo_fmt

    def save(self, *args, **kwargs):
        self.codigo = f'{self.tanda.codigo}{self.numero:04d}'
        super().save(*args, **kwargs)

    @property
    def codigo_fmt(self):
        return self.codigo

    @property
    def peso_kg(self):
        return Decimal(self.peso_g) / 1000

    @property
    def vendida_fuera(self):
        return self.estado == self.VENDIDA and self.venta_externa_fecha is not None


class FotoTanda(models.Model):
    """Fotos propias de una tanda (además de las de su receta, que hereda)."""

    tanda = models.ForeignKey(Tanda, on_delete=models.CASCADE, related_name='fotos')
    imagen = models.ImageField(upload_to='tandas/%Y/%m/')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fotos_tanda'
        ordering = ['id']

    def __str__(self):
        return f'Foto de la tanda {self.tanda.codigo}'


class ConsumoMateriaPrima(models.Model):
    """Qué lote de materia prima se usó en cada tanda (trazabilidad + costo)."""

    tanda = models.ForeignKey(Tanda, on_delete=models.CASCADE, related_name='consumos')
    lote = models.ForeignKey(LoteMateriaPrima, on_delete=models.PROTECT, related_name='consumos')
    cantidad = models.DecimalField(max_digits=10, decimal_places=3)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'consumo_materia_prima'
        verbose_name = 'Consumo de materia prima'

    @property
    def subtotal(self):
        return (self.cantidad * self.precio_unitario).quantize(Decimal('0.01'))


def validar_receta_vendible(receta):
    """El precio de venta no puede quedar por debajo del costo (regla de negocio)."""
    if receta.pk and receta.precio_kg and receta.ingredientes.exists():
        costo = costo_estimado_kg(receta)
        if costo and receta.precio_kg < costo:
            raise ValidationError(
                f'El precio por kg (${receta.precio_kg}) es menor al costo estimado de los ingredientes (${costo}).'
            )


def costo_estimado_kg(receta):
    """Costo de insumos por kg según el último precio de compra de cada materia prima."""
    total = DEC0
    for ing in receta.ingredientes.select_related('tipo'):
        ultimo = ing.tipo.lotes.order_by('-compra__fecha', '-id').first()
        if not ultimo:
            return None
        total += ing.cantidad * ultimo.precio_unitario
    return (total / receta.rendimiento_base_kg).quantize(Decimal('0.01'))
