"""Lógica de negocio de producción: cargar tandas, FIFO de insumos, pausar/reanudar."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.utils import timezone

from .models import ConsumoMateriaPrima, LoteMateriaPrima, Pieza, Tanda


class FaltanInsumos(ValidationError):
    def __init__(self, faltantes):
        self.faltantes = faltantes   # [(tipo, necesario, disponible)]
        texto = '; '.join(f'{t.nombre}: faltan {n - d} {t.unidad}' for t, n, d in faltantes)
        super().__init__(f'No alcanza la materia prima. {texto}')


def proximo_numero_dia(fecha):
    ultimo = Tanda.objects.filter(fecha_produccion=fecha).aggregate(m=Max('numero_dia'))['m'] or 0
    if ultimo >= 99:
        raise ValidationError('Ya hay 99 tandas cargadas ese día.')
    return ultimo + 1


def necesidad_insumos(receta, kg):
    """Para cada ingrediente: (ingrediente, necesario, disponible, alcanza)."""
    filas = []
    for ing, necesario in receta.ingredientes_para(kg):
        disponible = (LoteMateriaPrima.objects.filter(tipo=ing.tipo)
                      .aggregate(s=Sum('cantidad_disponible'))['s'] or Decimal('0'))
        filas.append((ing, necesario, disponible, disponible >= necesario))
    return filas


def repartir_pesos(kg_total, cantidad):
    """Reparte el peso total en piezas iguales (en gramos) sin perder gramos por redondeo."""
    total_g = int((Decimal(kg_total) * 1000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    base, resto = divmod(total_g, cantidad)
    if base < 1:
        raise ValidationError('El peso por pieza da menos de 1 g: revisá los kg y la cantidad de piezas.')
    return [base + (1 if i < resto else 0) for i in range(cantidad)]


def _consumir_fifo(tanda, tipo, cantidad):
    """Descuenta `cantidad` de los lotes más viejos de `tipo`. Devuelve el costo."""
    pendiente, costo = cantidad, Decimal('0')
    lotes = (LoteMateriaPrima.objects.select_for_update()
             .filter(tipo=tipo, cantidad_disponible__gt=0)
             .order_by('compra__fecha', 'id'))
    for lote in lotes:
        if pendiente <= 0:
            break
        usar = min(lote.cantidad_disponible, pendiente)
        lote.cantidad_disponible -= usar
        lote.save(update_fields=['cantidad_disponible'])
        ConsumoMateriaPrima.objects.create(tanda=tanda, lote=lote, cantidad=usar,
                                           precio_unitario=lote.precio_unitario)
        costo += usar * lote.precio_unitario
        pendiente -= usar
    if pendiente > 0:   # no debería pasar: se validó antes dentro de la misma transacción
        raise FaltanInsumos([(tipo, cantidad, cantidad - pendiente)])
    return costo


@transaction.atomic
def crear_tanda(*, receta, kg_producidos, cantidad_piezas, fecha_produccion=None,
                descontar_insumos=True, usuario=None, observaciones='', pesos_g=None,
                dias_estacionamiento=None, fecha_vencimiento=None):
    """Carga una tanda: descuenta insumos (FIFO), genera las piezas y avisa a quien esperaba stock."""
    fecha = fecha_produccion or timezone.localdate()
    kg = Decimal(kg_producidos)
    if cantidad_piezas < 1 or cantidad_piezas > 9999:
        raise ValidationError('La cantidad de piezas tiene que estar entre 1 y 9999.')

    if descontar_insumos:
        faltantes = [(ing.tipo, nec, disp) for ing, nec, disp, ok in necesidad_insumos(receta, kg) if not ok]
        if faltantes:
            raise FaltanInsumos(faltantes)

    tanda = Tanda.objects.create(
        receta=receta,
        fecha_produccion=fecha,
        numero_dia=proximo_numero_dia(fecha),
        kg_producidos=kg,
        cantidad_piezas=cantidad_piezas,
        fecha_disponible=fecha + timedelta(
            days=receta.dias_estacionamiento if dias_estacionamiento is None else dias_estacionamiento),
        fecha_vencimiento=fecha_vencimiento or (
            fecha + timedelta(days=receta.dias_vencimiento) if receta.dias_vencimiento else None),
        desconto_insumos=descontar_insumos,
        observaciones=observaciones,
        creado_por=usuario,
    )

    if descontar_insumos:
        costo = sum((_consumir_fifo(tanda, ing.tipo, nec) for ing, nec in receta.ingredientes_para(kg)),
                    Decimal('0'))
        tanda.costo_insumos = costo.quantize(Decimal('0.01'))
        tanda.save(update_fields=['costo_insumos'])

    pesos = pesos_g or repartir_pesos(kg, cantidad_piezas)
    Pieza.objects.bulk_create([
        Pieza(tanda=tanda, numero=i, peso_g=p, codigo=f'{tanda.codigo}{i:04d}')
        for i, p in enumerate(pesos, start=1)
    ])

    # Si ya se puede vender, avisa a los clientes que pidieron "avisame cuando haya"
    from tienda.services import enviar_avisos_stock
    transaction.on_commit(enviar_avisos_stock)
    return tanda


@transaction.atomic
def pausar_tanda(tanda, motivo):
    if tanda.estado != Tanda.ACTIVA:
        raise ValidationError('Solo se puede pausar una tanda activa.')
    if not motivo.strip():
        raise ValidationError('Escribí el motivo de la pausa.')
    tanda.estado = Tanda.PAUSADA
    tanda.motivo_pausa = motivo.strip()
    tanda.pausada_en = timezone.now()
    tanda.save(update_fields=['estado', 'motivo_pausa', 'pausada_en'])
    # Los QR sin pagar que tenían piezas de esta tanda se cancelan y las piezas se liberan
    from tienda.services import cancelar_reservas_de_tanda
    return cancelar_reservas_de_tanda(tanda)


@transaction.atomic
def reanudar_tanda(tanda):
    if tanda.estado != Tanda.PAUSADA:
        raise ValidationError('La tanda no está pausada.')
    tanda.estado = Tanda.ACTIVA
    tanda.save(update_fields=['estado'])
    actualizar_estado_agotada(tanda)
    from tienda.services import enviar_avisos_stock
    transaction.on_commit(enviar_avisos_stock)


@transaction.atomic
def anular_tanda(tanda, motivo):
    """Descarta la tanda: las piezas sin vender quedan dadas de baja."""
    if tanda.estado == Tanda.ANULADA:
        return
    if tanda.piezas.filter(estado=Pieza.RESERVADA).exists():
        raise ValidationError('Hay piezas de esta tanda con un pago en curso. Pausala y esperá unos minutos.')
    tanda.piezas.filter(estado=Pieza.DISPONIBLE).update(estado=Pieza.BAJA, motivo_baja=motivo[:200])
    tanda.estado = Tanda.ANULADA
    tanda.motivo_pausa = motivo
    tanda.save(update_fields=['estado', 'motivo_pausa'])


@transaction.atomic
def eliminar_tanda(tanda):
    """Borra una tanda cargada por error: devuelve las materias primas a sus lotes y borra sus piezas.
    No se puede si alguna pieza se vendió o tiene un pago en curso (eso se resuelve dando de baja/anulando)."""
    if tanda.piezas.filter(Q(estado__in=[Pieza.VENDIDA, Pieza.RESERVADA]) | Q(venta__isnull=False)).exists():
        raise ValidationError('No se puede eliminar: la tanda tiene piezas vendidas o reservadas. '
                              'Dala de baja (anular) para que quede el registro de lo vendido.')
    devueltos = 0
    for c in tanda.consumos.select_related('lote'):
        LoteMateriaPrima.objects.filter(pk=c.lote_id).update(cantidad_disponible=F('cantidad_disponible') + c.cantidad)
        devueltos += 1
    codigo = tanda.codigo
    tanda.delete()          # se borran en cascada las piezas, los consumos y las fotos
    return codigo, devueltos


def _piezas_de(tanda, ids):
    return tanda.piezas.filter(pk__in=[int(i) for i in ids if str(i).isdigit()])


@transaction.atomic
def dar_baja_piezas(tanda, ids, motivo):
    """Da de baja piezas libres (rotas, consumo interno, muestras...). Devuelve cuántas."""
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError('Elegí o escribí el motivo de la baja.')
    n = _piezas_de(tanda, ids).filter(estado=Pieza.DISPONIBLE).update(estado=Pieza.BAJA, motivo_baja=motivo[:200])
    actualizar_estado_agotada(tanda)
    return n


CANALES_VENTA_EXTERNA = ['WhatsApp', 'En el local / en persona', 'Feria o evento', 'Por encargo']


@transaction.atomic
def vender_fuera_de_web(tanda, ids, canal, cliente='', fecha=None, importe=None):
    """Registra piezas vendidas por fuera de la tienda web (WhatsApp, en persona, feria...).
    Quedan VENDIDAS, sin pedido, con la fecha y el importe para los reportes.
    Si no se indica el importe, se calcula con el precio por kg de la receta y el peso de cada pieza;
    si se indica el total cobrado, se reparte entre las piezas según su peso. Devuelve cuántas."""
    canal = (canal or '').strip()
    if not canal:
        raise ValidationError('Elegí por dónde se vendió (WhatsApp, en el local, feria…).')
    hoy_ = timezone.localdate()
    fecha = fecha or hoy_
    if fecha > hoy_:
        raise ValidationError('La fecha de la venta no puede ser futura.')
    if fecha < tanda.fecha_produccion:
        raise ValidationError('La fecha de la venta no puede ser anterior a la producción de la tanda.')
    if tanda.fecha_vencimiento and fecha >= tanda.fecha_vencimiento:
        raise ValidationError(f'La tanda venció el {tanda.fecha_vencimiento:%d/%m/%Y}: desde ese día no se vende. '
                              'Si se vendió antes, poné la fecha real de la venta.')
    if importe is not None and importe < 0:
        raise ValidationError('El importe no puede ser negativo.')
    piezas = list(_piezas_de(tanda, ids).filter(estado=Pieza.DISPONIBLE).select_for_update().order_by('numero'))
    if not piezas:
        return 0
    detalle = f'Venta por fuera de la web · {canal}' + (f' · {cliente.strip()}' if cliente and cliente.strip() else '')
    precio_kg = Decimal(str(tanda.receta.precio_kg or 0))
    if importe is None:
        montos = [(precio_kg * p.peso_g / 1000).quantize(Decimal('1'), ROUND_HALF_UP) for p in piezas]
    else:
        total_g = sum(p.peso_g for p in piezas)
        montos = [(importe * p.peso_g / total_g).quantize(Decimal('0.01'), ROUND_HALF_UP) for p in piezas]
        montos[-1] += importe - sum(montos)        # el redondeo no cambia el total cobrado
    for p, m in zip(piezas, montos):
        p.estado, p.motivo_baja = Pieza.VENDIDA, detalle[:200]
        p.venta_externa_fecha, p.venta_externa_monto = fecha, m
        p.save(update_fields=['estado', 'motivo_baja', 'venta_externa_fecha', 'venta_externa_monto'])
    actualizar_estado_agotada(tanda)
    return len(piezas)


@transaction.atomic
def reactivar_piezas(tanda, ids):
    """Vuelve a poner a la venta piezas dadas de baja (o vendidas por fuera de la web) por error."""
    if tanda.estado == Tanda.ANULADA:
        raise ValidationError('La tanda está anulada: sus piezas no se pueden volver a vender.')
    n = _piezas_de(tanda, ids).filter(
        Q(estado=Pieza.BAJA) | Q(estado=Pieza.VENDIDA, venta__isnull=True, venta_externa_fecha__isnull=False)
    ).update(estado=Pieza.DISPONIBLE, motivo_baja='', venta_externa_fecha=None, venta_externa_monto=None)
    tanda.refresh_from_db()
    actualizar_estado_agotada(tanda)
    if n:
        from tienda.services import enviar_avisos_stock
        transaction.on_commit(enviar_avisos_stock)
    return n


@transaction.atomic
def eliminar_piezas(tanda, ids):
    """Borra piezas que no existen (se cargaron de más). Las vendidas o reservadas no se pueden borrar."""
    qs = _piezas_de(tanda, ids)
    bloqueadas = qs.filter(Q(estado__in=[Pieza.VENDIDA, Pieza.RESERVADA]) | Q(venta__isnull=False)).count()
    if bloqueadas:
        raise ValidationError(f'{bloqueadas} de las piezas elegidas están vendidas o reservadas y no se pueden eliminar.')
    n = qs.count()
    qs.delete()
    tanda.cantidad_piezas = tanda.piezas.count()
    tanda.save(update_fields=['cantidad_piezas'])
    actualizar_estado_agotada(tanda)
    return n


def actualizar_estado_agotada(tanda):
    """Marca AGOTADA cuando ya no le quedan piezas disponibles (y vuelve a ACTIVA si aparecen)."""
    quedan = tanda.piezas.filter(estado__in=[Pieza.DISPONIBLE, Pieza.RESERVADA]).exists()
    if tanda.estado == Tanda.ACTIVA and not quedan:
        Tanda.objects.filter(pk=tanda.pk).update(estado=Tanda.AGOTADA)
    elif tanda.estado == Tanda.AGOTADA and quedan:
        Tanda.objects.filter(pk=tanda.pk).update(estado=Tanda.ACTIVA)


@transaction.atomic
def cambiar_fecha_venta(tanda, fecha):
    """Cambia desde cuándo se vende la tanda (estacionamiento). Hoy o antes = ya a la venta."""
    if fecha < tanda.fecha_produccion:
        raise ValidationError('La fecha de venta no puede ser anterior a la de producción.')
    tanda.fecha_disponible = fecha
    tanda.save(update_fields=['fecha_disponible'])
    if fecha <= timezone.localdate():
        from tienda.services import enviar_avisos_stock
        transaction.on_commit(enviar_avisos_stock)


@transaction.atomic
def cambiar_vencimiento(tanda, fecha):
    """Cambia el 'consumir antes de' de la tanda (None = sin vencimiento)."""
    if fecha and fecha <= tanda.fecha_produccion:
        raise ValidationError('El vencimiento tiene que ser posterior a la fecha de producción.')
    tanda.fecha_vencimiento = fecha
    tanda.save(update_fields=['fecha_vencimiento'])


@transaction.atomic
def dar_baja_vencidas(tanda):
    """Da de baja las piezas libres de una tanda vencida (merma). Devuelve cuántas."""
    if not tanda.vencida:
        raise ValidationError('La tanda todavía no venció.')
    n = tanda.piezas.filter(estado=Pieza.DISPONIBLE).update(
        estado=Pieza.BAJA, motivo_baja=f'Vencida el {tanda.fecha_vencimiento:%d/%m/%Y}')
    actualizar_estado_agotada(tanda)
    return n


def tandas_por_vencer():
    """Tandas activas con piezas libres que ya vencieron o entran en el aviso de su receta.
    Se calcula al abrir el panel: el aviso es solo dentro de la plataforma (no manda correos)."""
    from django.db.models import Count, Q, Sum
    hoy = timezone.localdate()
    qs = (Tanda.objects.filter(estado=Tanda.ACTIVA,
                               fecha_vencimiento__isnull=False)
          .select_related('receta')
          .annotate(libres=Count('piezas', filter=Q(piezas__estado=Pieza.DISPONIBLE)),
                    libres_g=Sum('piezas__peso_g', filter=Q(piezas__estado=Pieza.DISPONIBLE)))
          .filter(libres__gt=0).order_by('fecha_vencimiento', 'fecha_produccion', 'numero_dia'))
    return [t for t in qs if t.vencida or t.por_vencer]


# Conversiones permitidas al cambiar la unidad de una materia prima que ya tiene datos
CONVERSIONES = {('kg', 'g'): Decimal('1000'), ('g', 'kg'): Decimal('0.001'),
                ('l', 'ml'): Decimal('1000'), ('ml', 'l'): Decimal('0.001')}


@transaction.atomic
def cambiar_unidad(tipo, unidad):
    """Cambia la unidad de una materia prima convirtiendo compras, recetas y consumos.
    Ej.: pimienta de kg a g -> 0,03 kg pasa a 30 g y $21.000/kg pasa a $21/g."""
    from .models import ConsumoMateriaPrima, IngredienteReceta
    actual = tipo.unidad
    if actual.pk == unidad.pk:
        return
    tiene_datos = tipo.lotes.exists() or IngredienteReceta.objects.filter(tipo=tipo).exists()
    factor = CONVERSIONES.get((actual.abreviatura, unidad.abreviatura))
    if tiene_datos and factor is None:
        raise ValidationError(f'No se puede pasar de {actual} a {unidad} porque {tipo.nombre} ya tiene compras o '
                              f'recetas cargadas. Solo se convierten kg ↔ g y l ↔ ml.')
    if factor is not None:
        q3, q2 = Decimal('0.001'), Decimal('0.01')
        for lote in tipo.lotes.select_for_update():
            lote.cantidad = (lote.cantidad * factor).quantize(q3)
            lote.cantidad_disponible = (lote.cantidad_disponible * factor).quantize(q3)
            lote.precio_unitario = (lote.precio_unitario / factor).quantize(q2)
            lote.save(update_fields=['cantidad', 'cantidad_disponible', 'precio_unitario'])
        for ing in IngredienteReceta.objects.filter(tipo=tipo):
            ing.cantidad = (ing.cantidad * factor).quantize(q3)
            ing.save(update_fields=['cantidad'])
        for c in ConsumoMateriaPrima.objects.filter(lote__tipo=tipo):
            c.cantidad = (c.cantidad * factor).quantize(q3)
            c.precio_unitario = (c.precio_unitario / factor).quantize(q2)
            c.save(update_fields=['cantidad', 'precio_unitario'])
        tipo.stock_minimo = (tipo.stock_minimo * factor).quantize(q3)
    tipo.unidad = unidad
    tipo.save(update_fields=['unidad', 'stock_minimo'])


def pedidos_afectados(tanda):
    """Pedidos no entregados que tienen piezas de esta tanda (para avisar al pausar)."""
    from tienda.models import Pedido
    return (Pedido.objects.filter(items__asignaciones__pieza__tanda=tanda)
            .exclude(estado__in=[Pedido.ENTREGADO, Pedido.CANCELADO]).distinct())


# ---------------------------------------------------------------------------
# Trazabilidad: frenar de una vez todo lo hecho con un lote con problemas
# ---------------------------------------------------------------------------
def tandas_del_lote(lote):
    return Tanda.objects.filter(consumos__lote=lote).distinct()


@transaction.atomic
def pausar_tandas_de_lote(lote, motivo):
    """Pausa todas las tandas activas que usaron el lote. Devuelve (tandas pausadas, pedidos sin pagar cancelados)."""
    if not (motivo or '').strip():
        raise ValidationError('Escribí el motivo de la pausa.')
    pausadas, cancelados = [], 0
    for t in tandas_del_lote(lote).filter(estado=Tanda.ACTIVA):
        r = pausar_tanda(t, motivo)
        cancelados += r or 0
        pausadas.append(t)
    return pausadas, cancelados


@transaction.atomic
def reanudar_tandas_de_lote(lote):
    """Si fue una falsa alarma: vuelve a poner a la venta las tandas pausadas que usaron el lote."""
    reanudadas = []
    for t in tandas_del_lote(lote).filter(estado=Tanda.PAUSADA):
        reanudar_tanda(t)
        reanudadas.append(t)
    return reanudadas
