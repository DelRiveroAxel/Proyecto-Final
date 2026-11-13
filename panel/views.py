"""Panel del productor: producción, inventario por tanda, materias primas, recetas y pedidos."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from produccion.models import (CompraMateriaPrima, Pieza, Receta, Tanda, TipoMateriaPrima, costo_estimado_kg)
from produccion.models import UnidadMedida
from produccion.services import (CANALES_VENTA_EXTERNA, FaltanInsumos, anular_tanda, cambiar_fecha_venta,
                                 cambiar_unidad, cambiar_vencimiento, vender_fuera_de_web,
                                 crear_tanda, dar_baja_piezas, dar_baja_vencidas, eliminar_piezas, eliminar_tanda,
                                 necesidad_insumos, pausar_tanda, pausar_tandas_de_lote, pedidos_afectados,
                                 reactivar_piezas, reanudar_tanda, reanudar_tandas_de_lote)
from tienda.models import AvisoStock, Pedido
from tienda.services import cancelar_pedido_pagado, liberar_reservas_vencidas

from produccion.models import FotoReceta, FotoTanda

from .forms import (CompraForm, FotosForm, IngredienteFormSet, LoteFormSet, NosotrosForm, PausaForm, RecetaForm,
                    SelloForm, SlideForm, TandaForm, TipoMateriaPrimaForm, UnidadForm, preguntas_formset)

productor = staff_member_required(login_url='login')


def _unidades_tipos():
    """{id materia prima: abreviatura de su unidad} para mostrar 'kg', 'g', 'u' al lado de la cantidad."""
    return {str(t.pk): t.unidad.abreviatura for t in TipoMateriaPrima.objects.select_related('unidad')}


def _volver(request, defecto, *args):
    """Vuelve a la página indicada en el formulario (solo rutas internas) o a la de defecto."""
    v = request.POST.get('volver', '')
    if v.startswith('/') and not v.startswith('//'):
        return redirect(v)
    return redirect(defecto, *args)


# ------------------------------------------------------------------ inicio

@productor
def inicio(request):
    liberar_reservas_vencidas()
    hoy = timezone.localdate()
    a_la_venta = Pieza.objects.a_la_venta()
    kpis = {
        'piezas': a_la_venta.count(),
        'kg': (a_la_venta.aggregate(s=Sum('peso_g'))['s'] or 0) / 1000,
        'tandas_venta': a_la_venta.values('tanda').distinct().count(),
        'reservadas': Pieza.objects.filter(estado=Pieza.RESERVADA).count(),
        'estacionando': Pieza.objects.disponibles().filter(tanda__fecha_disponible__gt=hoy).count(),
        'pausadas': Tanda.objects.filter(estado=Tanda.PAUSADA).count(),
        'a_preparar': Pedido.objects.filter(estado__in=[Pedido.PAGADO, Pedido.PREPARANDO]).count(),
        'ventas_mes': Pedido.objects.filter(pagado_en__date__gte=hoy.replace(day=1))
                      .exclude(estado=Pedido.CANCELADO).aggregate(s=Sum('total'))['s'] or 0,
    }
    insumos = [t for t in TipoMateriaPrima.objects.filter(activo=True).annotate(stock_anotado=Sum('lotes__cantidad_disponible'))
               .select_related('unidad') if t.bajo_minimo]
    pedidos = (Pedido.objects.filter(estado__in=[Pedido.PAGADO, Pedido.PREPARANDO, Pedido.LISTO])
               .select_related('cliente').order_by('pagado_en')[:8])
    esperando = (AvisoStock.objects.filter(avisado_en__isnull=True).values('receta__nombre')
                 .annotate(n=Count('id')).order_by('-n')[:6])
    reintegros = Pedido.objects.filter(reintegro_pendiente=True)
    return render(request, 'panel/inicio.html', {'kpis': kpis, 'insumos': insumos, 'pedidos': pedidos,
                                                 'esperando': esperando, 'reintegros': reintegros})


# ------------------------------------------------------------------ tandas / inventario

@productor
def tandas(request):
    liberar_reservas_vencidas()
    qs = Tanda.objects.select_related('receta').con_stock().fifo()
    estado = request.GET.get('estado', 'vigentes')
    if estado == 'vigentes':
        qs = qs.filter(estado__in=[Tanda.ACTIVA, Tanda.PAUSADA])
    elif estado in dict(Tanda.ESTADOS):
        qs = qs.filter(estado=estado)
    receta = request.GET.get('receta')
    if receta:
        qs = qs.filter(receta_id=receta)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(codigo__startswith=q[:10]) | Q(receta__nombre__icontains=q))
    tandas = list(qs)
    # Orden de salida FIFO de cada receta (se calcula con todas las tandas, no solo las filtradas):
    # 1.ª = la más vieja con piezas libres a la venta -> de esa se vende ahora.
    cola = {}
    for tid, rid in (Tanda.objects.filter(estado=Tanda.ACTIVA, fecha_disponible__lte=timezone.localdate(),
                                          piezas__estado=Pieza.DISPONIBLE)
                     .exclude(fecha_vencimiento__lte=timezone.localdate())
                     .order_by('fecha_produccion', 'numero_dia').values_list('id', 'receta_id').distinct()):
        cola.setdefault(rid, []).append(tid)
    for t in tandas:
        orden = cola.get(t.receta_id, [])
        t.turno = orden.index(t.pk) + 1 if t.pk in orden else None
        t.fifo = t.turno == 1
        total = t.cantidad_piezas or 1
        t.pct_vend = round(100 * t.piezas_vend / total)
        t.pct_res = round(100 * t.piezas_res / total)
        t.pct_disp = max(0, 100 - t.pct_vend - t.pct_res)
    return render(request, 'panel/tandas.html', {
        'tandas': tandas, 'estado': estado, 'estados': Tanda.ESTADOS, 'recetas': Receta.objects.all(),
        'receta_sel': receta, 'q': q, 'form_pausa': PausaForm(),
    })


@productor
def tanda_nueva(request):
    form = TandaForm(request.POST or None, initial={'receta': request.GET.get('receta')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            tanda = crear_tanda(receta=d['receta'], kg_producidos=d['kg_producidos'],
                                cantidad_piezas=d['cantidad_piezas'], fecha_produccion=d['fecha_produccion'],
                                descontar_insumos=d['descontar_insumos'], usuario=request.user,
                                observaciones=d['observaciones'], dias_estacionamiento=d['dias_estacionamiento'],
                                fecha_vencimiento=(d['fecha_produccion'] + timedelta(days=d['dias_vencimiento'])
                                                   if d['dias_vencimiento'] else None))
        except FaltanInsumos as e:
            for tipo, nec, disp in e.faltantes:
                form.add_error(None, f'{tipo.nombre}: hacen falta {nec} {tipo.unidad} y hay {disp} {tipo.unidad}.')
        except ValidationError as e:
            form.add_error(None, ' '.join(e.messages))
        else:
            messages.success(request, f'Tanda {tanda.codigo} cargada: {tanda.cantidad_piezas} piezas '
                                      f'({tanda.piezas.first().codigo} a {tanda.piezas.last().codigo}).')
            return redirect('panel:tanda', tanda.pk)
    recetas = list(Receta.objects.all())
    dias = {str(r.pk): r.dias_estacionamiento for r in recetas}
    venc = {str(r.pk): r.dias_vencimiento for r in recetas}
    return render(request, 'panel/tanda_nueva.html', {'form': form, 'dias_recetas': dias, 'venc_recetas': venc})


@productor
def necesidad(request):
    """Fragmento HTML con los ingredientes necesarios (se carga mientras se completa el formulario)."""
    receta = Receta.objects.filter(pk=request.GET.get('receta')).first()
    try:
        kg = Decimal(request.GET.get('kg', '0').replace(',', '.'))
    except InvalidOperation:
        kg = Decimal('0')
    filas = [(ing, nec, disp, ok, nec - disp) for ing, nec, disp, ok in necesidad_insumos(receta, kg)] \
        if receta and kg > 0 else []
    return render(request, 'panel/_necesidad.html', {'filas': filas, 'receta': receta, 'kg': kg})


@productor
def tanda(request, pk):
    tanda = get_object_or_404(Tanda.objects.select_related('receta', 'creado_por'), pk=pk)
    piezas = tanda.piezas.select_related('venta__item__pedido').order_by('numero')
    resumen = piezas.aggregate(
        disp=Count('id', filter=Q(estado=Pieza.DISPONIBLE)), res=Count('id', filter=Q(estado=Pieza.RESERVADA)),
        vend=Count('id', filter=Q(estado=Pieza.VENDIDA)), baja=Count('id', filter=Q(estado=Pieza.BAJA)),
        fuera=Count('id', filter=Q(estado=Pieza.VENDIDA, venta_externa_fecha__isnull=False)),
        peso_disp=Sum('peso_g', filter=Q(estado=Pieza.DISPONIBLE)),
    )
    proxima = Pieza.objects.a_la_venta().filter(tanda__receta=tanda.receta).first()
    return render(request, 'panel/tanda.html', {
        'tanda': tanda, 'piezas': piezas, 'resumen': resumen, 'proxima': proxima,
        'consumos': tanda.consumos.select_related('lote__tipo__unidad', 'lote__compra__proveedor'),
        'afectados': pedidos_afectados(tanda), 'form_pausa': PausaForm(),
        'resaltar': request.GET.get('pieza', ''), 'form_fotos': FotosForm(), 'motivos_baja': MOTIVOS_BAJA,
        'canales_venta': CANALES_VENTA_EXTERNA, 'hoy': timezone.localdate(),
        'se_puede_eliminar': not (resumen['res'] or resumen['vend'] or tanda.piezas.filter(venta__isnull=False).exists()),
        'fotos_receta': ([tanda.receta.foto] if tanda.receta.foto else []) + [f.imagen for f in tanda.receta.fotos.all()],
    })


@productor
@require_POST
def tanda_fotos(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    form = FotosForm(request.POST, request.FILES)
    if form.is_valid() and form.cleaned_data['fotos']:
        for img in form.cleaned_data['fotos']:
            FotoTanda.objects.create(tanda=tanda, imagen=img)
        messages.success(request, f'Se agregaron {len(form.cleaned_data["fotos"])} foto(s) a la tanda.')
    else:
        messages.error(request, ' '.join(form.errors.get('fotos', ['Elegí al menos una foto.'])))
    return redirect(f'/panel/tandas/{pk}/#fotos')


@productor
@require_POST
def foto_borrar(request, tipo, pk):
    modelo = FotoTanda if tipo == 'tanda' else FotoReceta
    foto = get_object_or_404(modelo, pk=pk)
    volver = f'/panel/tandas/{foto.tanda_id}/#fotos' if tipo == 'tanda' else f'/panel/recetas/{foto.receta_id}/#fotos'
    foto.imagen.delete(save=False)
    foto.delete()
    messages.info(request, 'Foto eliminada.')
    return redirect(volver)


@productor
@require_POST
def receta_foto_principal(request, pk):
    """Pasa una foto extra a ser la principal (y la principal anterior queda como extra)."""
    foto = get_object_or_404(FotoReceta.objects.select_related('receta'), pk=pk)
    receta = foto.receta
    anterior = receta.foto.name if receta.foto else ''
    receta.foto = foto.imagen.name
    receta.save(update_fields=['foto'])
    if anterior:
        foto.imagen = anterior
        foto.save(update_fields=['imagen'])
    else:
        foto.delete()
    messages.success(request, 'Foto principal cambiada.')
    return redirect(f'/panel/recetas/{receta.pk}/#fotos')


@productor
@require_POST
def receta_foto_quitar_principal(request, pk):
    receta = get_object_or_404(Receta, pk=pk)
    if receta.foto:
        receta.foto.delete(save=False)
        receta.foto = ''
        receta.save(update_fields=['foto'])
        messages.info(request, 'Foto principal eliminada.')
    return redirect(f'/panel/recetas/{pk}/#fotos')


@productor
@require_POST
def tanda_pausar(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    form = PausaForm(request.POST)
    if form.is_valid():
        try:
            cancelados = pausar_tanda(tanda, form.cleaned_data['motivo'])
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            extra = f' Se cancelaron {cancelados} pago(s) en curso.' if cancelados else ''
            messages.warning(request, f'Tanda {tanda.codigo} pausada: sus piezas no se venden.{extra}')
    else:
        messages.error(request, 'Escribí el motivo de la pausa.')
    return _volver(request, 'panel:tanda', pk)


@productor
@require_POST
def tanda_reanudar(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    try:
        reanudar_tanda(tanda)
        messages.success(request, f'Tanda {tanda.codigo} reanudada: vuelve a la venta.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return _volver(request, 'panel:tanda', pk)


@productor
@require_POST
def tanda_anular(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    motivo = request.POST.get('motivo', '').strip()
    if not motivo:
        messages.error(request, 'Escribí el motivo para anular la tanda.')
    else:
        try:
            anular_tanda(tanda, motivo)
            messages.warning(request, f'Tanda {tanda.codigo} anulada: las piezas sin vender se dieron de baja.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
    return redirect('panel:tanda', pk)


@productor
@require_POST
def tanda_fecha_venta(request, pk):
    """Cambia el estacionamiento: desde qué fecha se vende la tanda."""
    from datetime import date
    tanda = get_object_or_404(Tanda, pk=pk)
    try:
        fecha = timezone.localdate() if request.POST.get('ahora') else date.fromisoformat(request.POST.get('fecha', ''))
        cambiar_fecha_venta(tanda, fecha)
    except ValueError:
        messages.error(request, 'Fecha inválida.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        if fecha <= timezone.localdate():
            messages.success(request, f'Tanda {tanda.codigo} a la venta desde hoy.')
        else:
            messages.success(request, f'Tanda {tanda.codigo}: estaciona hasta el {fecha:%d/%m/%Y}.')
    return redirect('panel:tanda', pk)


@productor
@require_POST
def tanda_vencimiento(request, pk):
    """Cambia el 'consumir antes de' de la tanda (vacío = sin vencimiento)."""
    from datetime import date
    tanda = get_object_or_404(Tanda, pk=pk)
    texto = request.POST.get('fecha', '').strip()
    try:
        fecha = date.fromisoformat(texto) if texto else None
        cambiar_vencimiento(tanda, fecha)
    except ValueError:
        messages.error(request, 'Fecha inválida.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, f'Tanda {tanda.codigo}: ' + (f'consumir antes del {fecha:%d/%m/%Y}.' if fecha
                                                                else 'sin fecha de vencimiento.'))
    return _volver(request, 'panel:tanda', pk)


@productor
@require_POST
def tanda_baja_vencidas(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    try:
        n = dar_baja_vencidas(tanda)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, f'Tanda {tanda.codigo}: {n} pieza{"s" if n != 1 else ""} dada{"s" if n != 1 else ""} '
                                  f'de baja por vencimiento.')
    return _volver(request, 'panel:tanda', pk)


@productor
@require_POST
def tanda_eliminar(request, pk):
    tanda = get_object_or_404(Tanda, pk=pk)
    if request.POST.get('confirmar', '').strip() != tanda.codigo:
        messages.error(request, f'Para eliminar escribí el código de la tanda ({tanda.codigo}).')
        return redirect('panel:tanda', pk)
    try:
        codigo, devueltos = eliminar_tanda(tanda)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('panel:tanda', pk)
    messages.success(request, f'Tanda {codigo} eliminada.' + (
        f' Se devolvieron al depósito las materias primas de {devueltos} lote{"s" if devueltos != 1 else ""}.'
        if devueltos else ''))
    return redirect('panel:tandas')


MOTIVOS_BAJA = ['Rota o dañada', 'Vencida', 'Consumo propio / degustación', 'Muestra o regalo', 'Error de carga']


@productor
@require_POST
def tanda_piezas(request, pk):
    """Acciones sobre las piezas elegidas con los casilleros: dar de baja, volver a la venta o eliminar."""
    tanda = get_object_or_404(Tanda, pk=pk)
    ids = request.POST.getlist('pieza')
    accion = request.POST.get('accion')
    if not ids:
        messages.error(request, 'Marcá primero las piezas con el casillero.')
        return redirect('panel:tanda', pk)
    try:
        if accion == 'baja':
            motivo = request.POST.get('motivo_baja', '')
            if motivo == 'otro' or not motivo:
                motivo = request.POST.get('motivo_otro', '')
            n = dar_baja_piezas(tanda, ids, motivo)
            messages.success(request, f'{n} pieza{"s" if n != 1 else ""} dada{"s" if n != 1 else ""} de baja ({motivo}).'
                             if n else 'Las piezas elegidas no estaban libres: no se dio de baja ninguna.')
        elif accion == 'vender':
            fecha = None
            if request.POST.get('venta_fecha'):
                try:
                    fecha = date.fromisoformat(request.POST['venta_fecha'])
                except ValueError:
                    raise ValidationError('La fecha de la venta no es válida.')
            importe = None
            texto = (request.POST.get('venta_importe') or '').strip().replace('$', '').replace(' ', '')
            if texto:
                if ',' in texto:                                   # 15.000,50
                    texto = texto.replace('.', '').replace(',', '.')
                elif re.fullmatch(r'\d{1,3}(\.\d{3})+', texto):     # 15.000
                    texto = texto.replace('.', '')
                try:
                    importe = Decimal(texto)
                except InvalidOperation:
                    raise ValidationError('El importe no es un número válido.')
            n = vender_fuera_de_web(tanda, ids, request.POST.get('venta_canal', ''),
                                    request.POST.get('venta_cliente', ''), fecha, importe)
            messages.success(request, f'{n} pieza{"s" if n != 1 else ""} registrada{"s" if n != 1 else ""} como '
                                      f'vendida{"s" if n != 1 else ""} por fuera de la web.'
                             if n else 'Las piezas elegidas no estaban libres: no se registró ninguna venta.')
        elif accion == 'reactivar':
            n = reactivar_piezas(tanda, ids)
            messages.success(request, f'{n} pieza{"s" if n != 1 else ""} volvi{"eron" if n != 1 else "ó"} a la venta.'
                             if n else 'Las piezas elegidas no estaban dadas de baja.')
        elif accion == 'eliminar':
            n = eliminar_piezas(tanda, ids)
            messages.success(request, f'{n} pieza{"s" if n != 1 else ""} eliminada{"s" if n != 1 else ""}. '
                                      f'La tanda ahora tiene {tanda.cantidad_piezas} piezas.')
        else:
            messages.error(request, 'Acción desconocida.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect(f"{reverse('panel:tanda', args=[pk])}#piezas")


@productor
@require_POST
def tanda_pesos(request, pk):
    """Guarda los pesos reales de las piezas (cuando se pesan al etiquetar)."""
    tanda = get_object_or_404(Tanda, pk=pk)
    cambios = 0
    with transaction.atomic():
        for p in tanda.piezas.filter(estado=Pieza.DISPONIBLE):
            valor = request.POST.get(f'peso_{p.pk}')
            if valor and valor.isdigit() and int(valor) > 0 and int(valor) != p.peso_g:
                p.peso_g = int(valor)
                p.save(update_fields=['peso_g'])
                cambios += 1
    messages.success(request, f'Se actualizaron {cambios} peso(s).' if cambios else 'No hubo cambios de peso.')
    return redirect('panel:tanda', pk)


@productor
def buscar_pieza(request):
    codigo = ''.join(ch for ch in request.GET.get('codigo', '') if ch.isdigit())
    if len(codigo) == 14:
        pieza = Pieza.objects.filter(codigo=codigo).first()
        if pieza:
            return redirect(f'/panel/tandas/{pieza.tanda_id}/?pieza={codigo}#p{codigo}')
    elif len(codigo) == 10:
        tanda = Tanda.objects.filter(codigo=codigo).first()
        if tanda:
            return redirect('panel:tanda', tanda.pk)
    messages.error(request, f'No encontramos ninguna pieza ni tanda con el código {codigo or "ingresado"}.')
    return redirect(request.META.get('HTTP_REFERER') or 'panel:tandas')


# ------------------------------------------------------------------ materias primas

@productor
def insumos(request):
    es_nuevo = request.method == 'POST' and 'nuevo-nombre' in request.POST
    es_unidad = request.method == 'POST' and 'unidad-nombre' in request.POST
    form = TipoMateriaPrimaForm(request.POST if es_nuevo else None, prefix='nuevo')
    form_unidad = UnidadForm(request.POST if es_unidad else None, prefix='unidad')
    if es_nuevo and form.is_valid():
        form.save()
        messages.success(request, f'Agregaste {form.instance.nombre} a las materias primas.')
        return redirect('panel:insumos')
    if es_unidad and form_unidad.is_valid():
        form_unidad.save()
        messages.success(request, f'Agregaste la unidad {form_unidad.instance.nombre}.')
        return redirect('panel:insumos')
    tipos = (TipoMateriaPrima.objects.select_related('unidad')
             .annotate(stock_anotado=Sum('lotes__cantidad_disponible'), compras=Count('lotes', distinct=True)))
    return render(request, 'panel/insumos.html', {'tipos': tipos, 'form': form, 'form_unidad': form_unidad,
                                                  'unidades': UnidadMedida.objects.all()})


@productor
@require_POST
def insumo_unidad(request, pk):
    tipo = get_object_or_404(TipoMateriaPrima.objects.select_related('unidad'), pk=pk)
    unidad = get_object_or_404(UnidadMedida, pk=request.POST.get('unidad'))
    antes = tipo.unidad
    try:
        cambiar_unidad(tipo, unidad)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        if antes.pk != unidad.pk:
            messages.success(request, f'{tipo.nombre}: ahora se mide en {unidad.nombre.lower()} ({unidad}). '
                                      f'Se convirtieron sus compras y recetas.')
    return redirect('panel:insumos')


@productor
@require_POST
def insumo_minimo(request, pk):
    tipo = get_object_or_404(TipoMateriaPrima, pk=pk)
    try:
        tipo.stock_minimo = max(Decimal('0'), Decimal(request.POST.get('stock_minimo', '0').replace(',', '.')))
        tipo.save(update_fields=['stock_minimo'])
    except InvalidOperation:
        messages.error(request, 'Stock mínimo inválido.')
    return redirect('panel:insumos')


@productor
def compras(request):
    qs = CompraMateriaPrima.objects.select_related('proveedor').prefetch_related('lotes__tipo__unidad')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(proveedor__nombre__icontains=q) | Q(numero_comprobante__icontains=q)).distinct()
    return render(request, 'panel/compras.html', {'compras': qs[:200], 'q': q})


@productor
def compra_nueva(request):
    from produccion.models import Proveedor
    compra = CompraMateriaPrima()
    form = CompraForm(request.POST or None, request.FILES or None, instance=compra)
    formset = LoteFormSet(request.POST or None, instance=compra, prefix='lotes')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            compra = form.save(commit=False)
            compra.creado_por = request.user
            compra.save()
            formset.instance = compra
            formset.save()
        messages.success(request, f'Compra cargada: {compra.lotes.count()} renglón(es) por ${compra.total:,.2f}.')
        return redirect('panel:compra', compra.pk)
    if not TipoMateriaPrima.objects.exists():
        messages.info(request, 'Primero cargá las materias primas que comprás (carne, sal, tripa…).')
    return render(request, 'panel/compra_nueva.html', {'form': form, 'formset': formset,
                                                       'vendedores': Proveedor.objects.values_list('nombre', flat=True),
                                                       'unidades_tipos': _unidades_tipos()})


@productor
def compra(request, pk):
    c = get_object_or_404(CompraMateriaPrima.objects.select_related('proveedor', 'creado_por'), pk=pk)
    return render(request, 'panel/compra.html', {'compra': c, 'lotes': c.lotes.select_related('tipo__unidad')})


@productor
def comprobante(request, pk):
    """Los tickets/facturas son privados: solo los ve el productor."""
    c = get_object_or_404(CompraMateriaPrima, pk=pk)
    if not c.comprobante:
        raise Http404
    try:
        return FileResponse(c.comprobante.open('rb'), filename=c.comprobante.name.rsplit('/', 1)[-1])
    except FileNotFoundError:
        raise Http404('El archivo no está en el servidor.')


# ------------------------------------------------------------------ recetas

@productor
def recetas(request):
    lista = list(Receta.objects.annotate(
        tandas_n=Count('tandas', distinct=True),
        disp=Count('tandas__piezas', filter=Q(tandas__piezas__estado=Pieza.DISPONIBLE, tandas__estado=Tanda.ACTIVA)),
        esperando=Count('avisos_stock', filter=Q(avisos_stock__avisado_en__isnull=True), distinct=True),
    ))
    for r in lista:
        r.costo = costo_estimado_kg(r)
    return render(request, 'panel/recetas.html', {'recetas': lista})


@productor
def receta_editar(request, pk=None):
    receta = get_object_or_404(Receta, pk=pk) if pk else Receta()
    form = RecetaForm(request.POST or None, request.FILES or None, instance=receta)
    formset = IngredienteFormSet(request.POST or None, instance=receta, prefix='ing')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            receta = form.save()
            formset.instance = receta
            formset.save()
            base = receta.fotos.count()
            for i, img in enumerate(form.cleaned_data.get('fotos_extra') or []):
                FotoReceta.objects.create(receta=receta, imagen=img, orden=base + i)
            costo = costo_estimado_kg(receta)
        if costo and receta.precio_kg < costo:
            messages.warning(request, f'Ojo: el precio (${receta.precio_kg:,.0f}/kg) quedó por debajo del costo '
                                      f'estimado de insumos (${costo:,.0f}/kg).')
        messages.success(request, f'Receta {receta.nombre} guardada.')
        return redirect('panel:recetas')
    return render(request, 'panel/receta_form.html', {'form': form, 'formset': formset, 'receta': receta,
                                                      'costo': costo_estimado_kg(receta) if receta.pk else None,
                                                      'unidades_tipos': _unidades_tipos()})


# ------------------------------------------------------------------ pedidos

@productor
def pedidos(request):
    liberar_reservas_vencidas()
    estado = request.GET.get('estado', 'activos')
    qs = Pedido.objects.select_related('cliente__cliente').prefetch_related('items')
    if estado == 'activos':
        qs = qs.filter(estado__in=[Pedido.PAGADO, Pedido.PREPARANDO, Pedido.LISTO]).order_by('pagado_en')
    elif estado == 'reintegros':
        qs = qs.filter(reintegro_pendiente=True)
    elif estado in dict(Pedido.ESTADOS):
        qs = qs.filter(estado=estado)
    else:
        qs = qs.exclude(estado=Pedido.VENCIDO)
    return render(request, 'panel/pedidos.html', {'pedidos': qs[:200], 'estado': estado, 'estados': Pedido.ESTADOS})


@productor
def pedido(request, pk):
    p = get_object_or_404(Pedido.objects.select_related('cliente__cliente')
                          .prefetch_related('items__asignaciones__pieza__tanda'), pk=pk)
    era_nuevo = p.novedad
    if p.novedad:
        Pedido.objects.filter(pk=pk).update(novedad=False)
    return render(request, 'panel/pedido.html', {'pedido': p, 'era_nuevo': era_nuevo})


@productor
def pedido_comprobante(request, pk):
    p = get_object_or_404(Pedido.objects.select_related('cliente__cliente')
                          .prefetch_related('items__asignaciones__pieza'), pk=pk)
    if not p.pagado_en:
        raise Http404('Este pedido no tiene un pago registrado.')
    return render(request, 'comprobante.html', {'pedido': p, 'volver': f'/panel/pedidos/{pk}/'})


@productor
@require_POST
def pedidos_vistos(request):
    Pedido.objects.filter(novedad=True).update(novedad=False)
    return redirect('panel:pedidos')


@productor
def preguntas(request):
    from core.models import PreguntaFrecuente
    FormSet = preguntas_formset()
    formset = FormSet(request.POST or None, queryset=PreguntaFrecuente.objects.all(), prefix='faq')
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, 'Preguntas frecuentes guardadas.')
        return redirect('panel:preguntas')
    from core.models import PaginaNosotros
    return render(request, 'panel/preguntas.html', {'formset': formset, 'pagina': PaginaNosotros.obtener()})


@productor
def nosotros(request):
    from core.models import PaginaNosotros
    pagina = PaginaNosotros.obtener()
    form = NosotrosForm(request.POST or None, request.FILES or None, instance=pagina)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Página "Nosotros" actualizada.')
        return redirect('panel:nosotros')
    return render(request, 'panel/nosotros.html', {'form': form, 'pagina': pagina})


@productor
@require_POST
def pedido_avanzar(request, pk):
    p = get_object_or_404(Pedido, pk=pk)
    sig = p.siguiente_estado
    if sig:
        p.estado = sig[0]
        if p.estado == Pedido.ENTREGADO:
            p.entregado_en = timezone.now()
        p.save(update_fields=['estado', 'entregado_en'])
        messages.success(request, f'Pedido #{p.numero}: {sig[1]}.')
    return _volver(request, 'panel:pedido', pk)


@productor
@require_POST
def pedido_cancelar(request, pk):
    p = get_object_or_404(Pedido, pk=pk)
    try:
        cancelar_pedido_pagado(p, request.POST.get('motivo', '').strip() or 'Cancelado por el productor',
                               devolver_al_stock=request.POST.get('devolver') == '1')
        messages.warning(request, f'Pedido #{p.numero} cancelado. Acordate de reintegrar ${p.total:,.0f} desde Mercado Pago.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('panel:pedido', pk)


@productor
@require_POST
def pedido_reintegrado(request, pk):
    Pedido.objects.filter(pk=pk).update(reintegro_pendiente=False)
    messages.success(request, 'Marcado como reintegrado.')
    return redirect('panel:pedido', pk)



# ------------------------------------------------------------------ carrusel del inicio

@productor
def carrusel(request):
    from core.models import PaginaNosotros, SlideInicio
    pagina = PaginaNosotros.obtener()
    sello = SelloForm(request.POST or None, instance=pagina)
    if request.method == 'POST' and sello.is_valid():
        sello.save()
        messages.success(request, 'Sello actualizado.')
        return redirect('panel:carrusel')
    slides = list(SlideInicio.objects.select_related('receta'))
    automaticos = []
    if not any(x.activo for x in slides):
        from tienda.services import stock_por_receta
        recetas = list(Receta.objects.filter(publicada=True).order_by('precio_kg'))
        stock = stock_por_receta(recetas)
        automaticos = [r for r in recetas if stock.get(r.pk)][:3]
    return render(request, 'panel/carrusel.html', {'slides': slides, 'sello': sello, 'automaticos': automaticos})


@productor
def slide_editar(request, pk=None):
    from core.models import SlideInicio
    slide = get_object_or_404(SlideInicio, pk=pk) if pk else SlideInicio()
    if not pk and request.method == 'GET':
        slide.orden = (SlideInicio.objects.order_by('-orden').values_list('orden', flat=True).first() or 0) + 10
    form = SlideForm(request.POST or None, request.FILES or None, instance=slide)
    if request.method == 'POST' and form.is_valid():
        s = form.save(commit=False)
        if request.POST.get('quitar_imagen') and s.imagen:
            s.imagen.delete(save=False)
            s.imagen = ''
        if not s.pk:
            s.orden = (SlideInicio.objects.order_by('-orden').values_list('orden', flat=True).first() or 0) + 10
        s.save()
        messages.success(request, f'Diapositiva “{s.titulo}” guardada.')
        return redirect('panel:carrusel')
    recetas = {str(r.pk): {'titulo': r.nombre, 'texto': r.descripcion[:200], 'etiqueta': (r.subtitulo or r.get_categoria_display())[:40],
                           'foto': r.foto_principal.url if r.foto_principal else ''}
               for r in Receta.objects.all()}
    from core.models import PaginaNosotros
    return render(request, 'panel/slide_form.html', {'form': form, 'slide': slide, 'recetas_json': recetas,
                                                     'pagina': PaginaNosotros.obtener()})


@productor
@require_POST
def slide_accion(request, pk, accion):
    from core.models import SlideInicio
    slide = get_object_or_404(SlideInicio, pk=pk)
    if accion == 'borrar':
        if slide.imagen:
            slide.imagen.delete(save=False)
        slide.delete()
        messages.info(request, 'Diapositiva eliminada.')
    elif accion == 'visible':
        slide.activo = not slide.activo
        slide.save(update_fields=['activo'])
    elif accion in ('subir', 'bajar'):
        lista = list(SlideInicio.objects.all())
        i = lista.index(slide)
        j = i - 1 if accion == 'subir' else i + 1
        if 0 <= j < len(lista):
            lista[i], lista[j] = lista[j], lista[i]
            for n, x in enumerate(lista):
                if x.orden != (n + 1) * 10:
                    x.orden = (n + 1) * 10
                    x.save(update_fields=['orden'])
    return redirect('panel:carrusel')


# ---------------------------------------------------------------------------
# Consultas que llegan por el formulario de contacto
# ---------------------------------------------------------------------------
@productor
def consultas(request):
    from django.conf import settings

    from core.correo import destino_consultas
    from core.models import ConsultaContacto
    ver = request.GET.get('ver', 'pendientes')
    qs = ConsultaContacto.objects.select_related('usuario')
    if ver != 'todas':
        qs = qs.filter(respondida=False)
    return render(request, 'panel/consultas.html', {
        'consultas': qs[:200], 'ver': ver,
        'pendientes': ConsultaContacto.objects.filter(respondida=False).count(),
        'correo_real': getattr(settings, 'EMAIL_REAL', False),
        'correo_envia': getattr(settings, 'EMAIL_USER', ''),
        'correo_destino': destino_consultas(),
    })


@productor
@require_POST
def consulta_respondida(request, pk):
    from core.models import ConsultaContacto
    c = get_object_or_404(ConsultaContacto, pk=pk)
    c.respondida = not c.respondida
    c.save(update_fields=['respondida'])
    messages.success(request, f'Consulta de {c.nombre}: ' + ('marcada como respondida.' if c.respondida else 'volvió a pendientes.'))
    return _volver(request, 'panel:consultas')


@productor
@require_POST
def correo_prueba(request):
    from django.conf import settings

    from core.correo import destino_consultas, enviar_prueba
    destino = request.POST.get('destino', '').strip() or destino_consultas() or request.user.email
    if not destino:
        messages.error(request, 'No hay a qué correo mandar la prueba: cargá uno en Página "Nosotros".')
        return _volver(request, 'panel:consultas')
    ok, error = enviar_prueba(destino)
    if not ok:
        messages.error(request, f'No se pudo enviar: {error}. Revisá EMAIL_USER y EMAIL_PASSWORD en el archivo .env.')
    elif getattr(settings, 'EMAIL_REAL', False):
        messages.success(request, f'Correo de prueba enviado a {destino}. Fijate en la bandeja (o en Spam).')
    else:
        messages.warning(request, 'El correo de prueba se mostró en la consola donde corre el servidor: todavía no '
                                  'está configurado el envío real (ver abajo cómo activarlo).')
    return _volver(request, 'panel:consultas')


# ---------------------------------------------------------------------------
# Etiquetas para imprimir en hoja A4
# ---------------------------------------------------------------------------
FORMATOS_ETIQUETA = {
    # clave: (nombre, columnas, filas)
    '24': ('24 por hoja · 70×37 mm', 3, 8),
    '14': ('14 por hoja · 99×38 mm', 2, 7),
    '40': ('40 por hoja · 52×30 mm', 4, 10),
}


@productor
def etiquetas(request):
    """Hoja A4 con las etiquetas de las piezas: receta, peso y código de 14 dígitos (y vencimiento si se quiere).
    Sin tandas elegidas muestra la lista para elegir."""
    ids = [int(x) for x in request.GET.getlist('tanda') if x.isdigit()]
    vigentes = (Tanda.objects.filter(estado__in=[Tanda.ACTIVA, Tanda.PAUSADA]).select_related('receta')
                .con_stock().order_by('-fecha_produccion', '-numero_dia'))
    formato = request.GET.get('formato', '24')
    if formato not in FORMATOS_ETIQUETA:
        formato = '24'
    _, cols, filas = FORMATOS_ETIQUETA[formato]
    por_hoja = cols * filas
    cuales = request.GET.get('piezas', 'todas')
    try:
        inicio = max(1, min(por_hoja, int(request.GET.get('inicio', 1))))
    except ValueError:
        inicio = 1
    con_venc = request.GET.get('venc', '1') == '1'
    con_cortes = request.GET.get('cortes', '1') == '1'

    tandas_sel = list(Tanda.objects.filter(pk__in=ids).select_related('receta').fifo())
    piezas = Pieza.objects.filter(tanda__in=tandas_sel).select_related('tanda__receta').fifo()
    piezas = piezas.filter(estado=Pieza.DISPONIBLE) if cuales == 'libres' else piezas.exclude(estado=Pieza.BAJA)
    piezas = list(piezas)

    celdas = [None] * (inicio - 1) + piezas
    hojas = [celdas[i:i + por_hoja] for i in range(0, len(celdas), por_hoja)] if piezas else []
    if hojas:  # completar la última hoja con lugares vacíos para que la grilla no se deforme
        hojas[-1] += [None] * (por_hoja - len(hojas[-1]))
    return render(request, 'panel/etiquetas.html', {
        'vigentes': vigentes, 'ids': ids, 'tandas_sel': tandas_sel, 'hojas': hojas, 'n_piezas': len(piezas),
        'formatos': [(k, v[0]) for k, v in FORMATOS_ETIQUETA.items()], 'formato': formato,
        'cols': cols, 'filas': filas, 'por_hoja': por_hoja, 'cuales': cuales, 'inicio': inicio,
        'con_venc': con_venc, 'con_cortes': con_cortes, 'posiciones': range(1, por_hoja + 1),
    })


# ---------------------------------------------------------------------------
# Trazabilidad
# ---------------------------------------------------------------------------
@productor
def traza(request):
    """Buscar por código de pieza/tanda o elegir un lote de materia prima."""
    from produccion.models import LoteMateriaPrima
    q = request.GET.get('q', '').strip()
    digitos = ''.join(ch for ch in q if ch.isdigit())
    if digitos and len(digitos) == len(q.replace(' ', '')):
        if len(digitos) == 14:
            pieza = Pieza.objects.filter(codigo=digitos).first()
            if pieza:
                return redirect(f"{reverse('panel:traza_tanda', args=[pieza.tanda_id])}?pieza={digitos}")
        elif len(digitos) == 10:
            tanda = Tanda.objects.filter(codigo=digitos).first()
            if tanda:
                return redirect('panel:traza_tanda', tanda.pk)
        messages.error(request, f'No hay ninguna pieza ni tanda con el código {digitos}.')
    tipo = request.GET.get('tipo', '')
    lotes = (LoteMateriaPrima.objects.select_related('tipo__unidad', 'compra__proveedor')
             .annotate(n_tandas=Count('consumos__tanda', distinct=True)).order_by('-compra__fecha', '-id'))
    if tipo.isdigit():
        lotes = lotes.filter(tipo_id=tipo)
    if q and not digitos:
        lotes = lotes.filter(Q(tipo__nombre__icontains=q) | Q(compra__proveedor__nombre__icontains=q) |
                             Q(lote__icontains=q) | Q(compra__numero_comprobante__icontains=q))
    return render(request, 'panel/traza.html', {
        'lotes': lotes[:150], 'q': q, 'tipo': tipo,
        'tipos': TipoMateriaPrima.objects.order_by('nombre'),
    })


@productor
def traza_lote(request, pk):
    from produccion.models import LoteMateriaPrima

    from .trazabilidad import hacia_adelante
    lote = get_object_or_404(LoteMateriaPrima.objects.select_related('tipo__unidad', 'compra__proveedor'), pk=pk)
    datos = hacia_adelante(lote)
    if request.GET.get('pdf'):
        from . import pdf
        return pdf.traza_lote(lote, datos['tandas'], datos['pedidos'], datos['personas'], datos['fuera'])
    return render(request, 'panel/traza_lote.html', {'lote': lote, **datos})


@productor
def traza_tanda(request, pk):
    from .trazabilidad import clientes_de, fuera_de, hacia_atras, personas, resumen_piezas
    tanda = get_object_or_404(Tanda.objects.select_related('receta'), pk=pk)
    pieza = None
    codigo = request.GET.get('pieza', '')
    if codigo:
        pieza = tanda.piezas.filter(codigo=codigo).select_related('venta__item__pedido__cliente').first()
    filas = clientes_de([tanda])
    fuera = fuera_de([tanda])
    if request.GET.get('pdf'):
        from . import pdf
        return pdf.traza_tanda(tanda, hacia_atras(tanda), filas, personas(filas), resumen_piezas(tanda), pieza, fuera)
    return render(request, 'panel/traza_tanda.html', {
        'tanda': tanda, 'consumos': hacia_atras(tanda), 'pedidos': filas, 'personas': personas(filas), 'fuera': fuera,
        'resumen': resumen_piezas(tanda), 'pieza': pieza,
    })


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
@productor
def reportes(request):
    from . import reportes as rep
    desde, hasta, preset = rep.periodo(request.GET)
    datos = rep.calcular(desde, hasta)
    seccion = request.GET.get('pdf')
    if seccion:
        from . import pdf
        return pdf.reporte(seccion if seccion in pdf.SECCIONES else 'completo', datos, desde, hasta)
    consulta = request.GET.copy()
    consulta.pop('pdf', None)
    return render(request, 'panel/reportes.html', {
        **datos, 'desde': desde, 'hasta': hasta, 'preset': preset, 'presets': rep.PRESETS,
        'fuera_grupos': [('Por canal', datos['fuera']['canales']), ('Por receta', datos['fuera']['recetas'])],
        'consulta': consulta.urlencode(),
    })



@productor
@require_POST
def traza_lote_pausar(request, pk):
    from produccion.models import LoteMateriaPrima
    lote = get_object_or_404(LoteMateriaPrima.objects.select_related('tipo'), pk=pk)
    if request.POST.get('accion') == 'reanudar':
        reanudadas = reanudar_tandas_de_lote(lote)
        messages.success(request, f'{len(reanudadas)} tanda{"s" if len(reanudadas) != 1 else ""} volvi'
                                  f'{"eron" if len(reanudadas) != 1 else "ó"} a la venta.' if reanudadas
                         else 'No había tandas pausadas de este lote.')
        return redirect('panel:traza_lote', pk)
    try:
        pausadas, cancelados = pausar_tandas_de_lote(lote, request.POST.get('motivo', ''))
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('panel:traza_lote', pk)
    if pausadas:
        texto = (f'Se pausaron {len(pausadas)} tanda{"s" if len(pausadas) != 1 else ""} '
                 f'({", ".join(t.codigo for t in pausadas)}): ya no se venden.')
        if cancelados:
            texto += f' Se cancelaron {cancelados} pago{"s" if cancelados != 1 else ""} en curso con esas piezas.'
        messages.warning(request, texto)
    else:
        messages.info(request, 'No había tandas activas de este lote para pausar.')
    return redirect('panel:traza_lote', pk)
