"""
Tienda online: clientes, carrito, pedidos y avisos de stock.

- El cliente compra POR PIEZAS. El precio es peso real x precio por kg.
- Al agregar al carrito no se aparta nada.
- Al generar el QR de Mercado Pago se RESERVAN las piezas (FIFO) por 15 minutos:
  el monto del QR es el peso exacto de esas piezas. Si paga, quedan vendidas;
  si el QR vence, vuelven al stock.
- Si un producto no tiene stock, el cliente puede pedir "Avisame cuando haya"
  (no paga nada por adelantado, así el precio de la tanda nueva puede cambiar).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from produccion.models import Pieza, Receta


class TipoCliente(models.Model):
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        db_table = 'tipo_cliente'
        verbose_name = 'Tipo de cliente'
        verbose_name_plural = 'Tipos de cliente'

    def __str__(self):
        return self.nombre


class Cliente(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cliente')
    tipo = models.ForeignKey(TipoCliente, on_delete=models.SET_NULL, null=True, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    localidad = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = 'clientes'

    def __str__(self):
        return self.user.nombre_visible()


class ItemCarrito(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carrito')
    receta = models.ForeignKey(Receta, on_delete=models.CASCADE)
    cantidad = models.PositiveSmallIntegerField(default=1)
    agregado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'carrito'
        ordering = ['agregado']
        constraints = [models.UniqueConstraint(fields=['user', 'receta'], name='uq_carrito_receta')]


class Pedido(models.Model):
    PENDIENTE_PAGO = 'PENDIENTE_PAGO'
    PAGADO = 'PAGADO'
    PREPARANDO = 'PREPARANDO'
    LISTO = 'LISTO'
    ENTREGADO = 'ENTREGADO'
    CANCELADO = 'CANCELADO'
    VENCIDO = 'VENCIDO'
    ESTADOS = [
        (PENDIENTE_PAGO, 'Esperando el pago'),
        (PAGADO, 'Pagado'),
        (PREPARANDO, 'Preparando'),
        (LISTO, 'Listo para retirar'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
        (VENCIDO, 'QR vencido'),
    ]
    CLASES = {
        PENDIENTE_PAGO: 'ik-est-mp', PAGADO: 'ik-est-ok', PREPARANDO: 'ik-est-info', LISTO: 'ik-est-ok',
        ENTREGADO: 'ik-est-info', CANCELADO: 'ik-est-err', VENCIDO: 'ik-est-err',
    }
    PASOS = [PAGADO, PREPARANDO, LISTO, ENTREGADO]
    SIGUIENTE = {PAGADO: PREPARANDO, PREPARANDO: LISTO, LISTO: ENTREGADO}

    cliente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pedidos')
    creado = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default=PENDIENTE_PAGO)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    notas = models.TextField('Notas del cliente', blank=True)
    motivo_cancelacion = models.CharField(max_length=200, blank=True)
    reintegro_pendiente = models.BooleanField(
        default=False, help_text='Se cobró pero hay que devolver la plata (se marca a mano al reintegrar).')

    # Mercado Pago (API de Orders, QR dinámico)
    mp_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    qr_data = models.TextField(blank=True)
    reserva_hasta = models.DateTimeField(null=True, blank=True)   # vencen el QR y la reserva de piezas
    mp_payment_id = models.CharField('N.º de operación de Mercado Pago', max_length=64, blank=True)
    mp_metodo = models.CharField('Medio de pago', max_length=60, blank=True)
    mp_datos = models.JSONField(null=True, blank=True, help_text='Respuesta de Mercado Pago al confirmar el pago.')
    pagado_en = models.DateTimeField(null=True, blank=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    novedad = models.BooleanField(default=False, help_text='Hay algo nuevo que el productor todavía no vio.')

    class Meta:
        db_table = 'pedidos'
        ordering = ['-creado']

    def __str__(self):
        return f'Pedido #{self.numero}'

    @property
    def numero(self):
        return f'{self.pk:04d}' if self.pk else '----'

    @property
    def external_reference(self):
        return f'IURKO-PEDIDO-{self.pk}'

    @property
    def clase_estado(self):
        return self.CLASES.get(self.estado, 'ik-est-info')

    @property
    def esperando_pago(self):
        return self.estado == self.PENDIENTE_PAGO and self.reserva_hasta and timezone.now() < self.reserva_hasta

    @property
    def segundos_restantes(self):
        if not self.esperando_pago:
            return 0
        return max(0, int((self.reserva_hasta - timezone.now()).total_seconds()))

    @property
    def segundos_qr(self):
        """El QR vence 2 minutos antes que la reserva (margen para que llegue el aviso de pago)."""
        return max(0, self.segundos_restantes - 120)

    @property
    def siguiente_estado(self):
        s = self.SIGUIENTE.get(self.estado)
        return (s, dict(self.ESTADOS)[s]) if s else None

    @property
    def metodo_legible(self):
        return {
            'account_money': 'Dinero en cuenta de Mercado Pago',
            'debit_card': 'Tarjeta de débito', 'credit_card': 'Tarjeta de crédito',
            'prepaid_card': 'Tarjeta prepaga', 'bank_transfer': 'Transferencia bancaria',
            'demo': 'Pago simulado (modo demo)',
        }.get(self.mp_metodo, self.mp_metodo or 'Mercado Pago (QR)')

    @property
    def peso_total_g(self):
        return sum(i.peso_g for i in self.items.all())

    def pasos_timeline(self):
        """[(nombre, hecho, actual)] para la línea de tiempo."""
        if self.estado not in self.PASOS:
            return []
        idx = self.PASOS.index(self.estado)
        return [(dict(self.ESTADOS)[p], i <= idx, i == idx) for i, p in enumerate(self.PASOS)]


class PedidoItem(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
    receta = models.ForeignKey(Receta, on_delete=models.PROTECT, related_name='+')
    nombre = models.CharField(max_length=150)              # copia por si cambia la receta
    cantidad = models.PositiveSmallIntegerField()          # piezas
    precio_kg = models.DecimalField(max_digits=12, decimal_places=2)
    peso_g = models.PositiveIntegerField()                 # peso real de las piezas reservadas
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        db_table = 'pedidos_x_producto'

    def __str__(self):
        return f'{self.cantidad} x {self.nombre}'

    @property
    def peso_kg(self):
        return Decimal(self.peso_g) / 1000

    def vencimientos(self):
        """Fechas de 'consumir antes de' de las piezas que le tocaron (sin repetir, la primera arriba)."""
        return sorted({a.pieza.tanda.fecha_vencimiento for a in self.asignaciones.all()
                       if a.pieza.tanda.fecha_vencimiento})


class PiezaPedido(models.Model):
    """Qué pieza física (con su código) le tocó a cada renglón del pedido."""

    item = models.ForeignKey(PedidoItem, on_delete=models.CASCADE, related_name='asignaciones')
    pieza = models.OneToOneField(Pieza, on_delete=models.PROTECT, related_name='venta')
    asignada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'detalle_pedido_pieza'
        ordering = ['pieza__tanda__fecha_produccion', 'pieza__tanda__numero_dia', 'pieza__numero']


class AvisoStock(models.Model):
    """'Avisame cuando haya': se avisa por correo y en la web cuando vuelve a haber stock."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='avisos_stock')
    receta = models.ForeignKey(Receta, on_delete=models.CASCADE, related_name='avisos_stock')
    creado = models.DateTimeField(auto_now_add=True)
    avisado_en = models.DateTimeField(null=True, blank=True)
    visto = models.BooleanField(default=False)

    class Meta:
        db_table = 'avisos_stock'
        verbose_name = 'Aviso de stock'
        verbose_name_plural = 'Avisos de stock'
        ordering = ['creado']
        constraints = [
            # un solo aviso pendiente por cliente y producto
            models.UniqueConstraint(fields=['user', 'receta'], condition=models.Q(avisado_en__isnull=True),
                                    name='uq_aviso_pendiente'),
        ]

    def __str__(self):
        return f'{self.user} espera {self.receta}'
