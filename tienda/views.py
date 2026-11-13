import io
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from produccion.models import Receta

from . import mercadopago as mp
from .models import AvisoStock, ItemCarrito, Pedido
from .services import (StockInsuficiente, cancelar_pedido_pendiente, confirmar_pago, crear_pedido_con_qr,
                       enviar_avisos_stock, pedir_aviso, resumen_carrito, sincronizar_pago, stock_por_receta)

log = logging.getLogger(__name__)

ORDENES = {
    'precio': ('Precio', 'precio_kg'),
    'precio_desc': ('Mayor precio', '-precio_kg'),
    'tipo': ('Tipo de corte', 'categoria'),
    'nombre': ('A–Z', 'nombre'),
}


def solo_clientes(view):
    """El productor usa el panel; la tienda es para clientes."""
    def envoltura(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            messages.info(request, 'Estás logueado como productor. Para comprar usá una cuenta de cliente.')
            return redirect('panel:inicio')
        return view(request, *args, **kwargs)
    envoltura.__name__ = view.__name__
    return envoltura


def catalogo(request):
    enviar_avisos_stock()                    # por si terminó el estacionamiento de alguna tanda
    recetas = Receta.objects.filter(publicada=True)
    q = request.GET.get('q', '').strip()
    if q:
        recetas = recetas.filter(Q(nombre__icontains=q) | Q(descripcion__icontains=q) | Q(etiqueta__icontains=q))
    cat = request.GET.get('categoria', '')
    if cat in dict(Receta.CATEGORIAS):
        recetas = recetas.filter(categoria=cat)
    orden = request.GET.get('orden', 'precio')
    recetas = list(recetas.order_by(ORDENES.get(orden, ORDENES['precio'])[1], 'nombre'))

    stock = stock_por_receta(recetas)
    for r in recetas:
        r.stock = stock.get(r.pk, 0)
        r.proxima = None if r.stock else r.proxima_tanda()
    if request.GET.get('solo_stock'):
        recetas = [r for r in recetas if r.stock]

    from core.models import SlideInicio
    slides = list(SlideInicio.objects.filter(activo=True).select_related('receta'))
    destacados = [] if slides else [r for r in recetas if r.stock][:3]
    return render(request, 'tienda/catalogo.html', {
        'recetas': recetas, 'q': q, 'orden': orden, 'ordenes': ORDENES, 'categoria': cat,
        'categorias': Receta.CATEGORIAS, 'destacados': destacados, 'slides': slides,
        'con_stock': sum(1 for r in recetas if r.stock),
    })


def producto(request, slug):
    receta = get_object_or_404(Receta, slug=slug, publicada=True)
    receta.stock = stock_por_receta([receta]).get(receta.pk, 0)
    receta.proxima = None if receta.stock else receta.proxima_tanda()
    # FIFO: la próxima pieza que se vende es la de la tanda más vieja; mostramos su vencimiento
    primera = receta.piezas_a_la_venta().select_related('tanda').first() if receta.stock else None
    receta.vence = primera.tanda.fecha_vencimiento if primera else None
    esperando = (request.user.is_authenticated and
                 AvisoStock.objects.filter(user=request.user, receta=receta, avisado_en__isnull=True).exists())
    return render(request, 'tienda/producto.html', {'receta': receta, 'esperando_aviso': esperando,
                                                    'galeria': receta.galeria()})


# --------------------------------------------------------------------- carrito

@login_required
@solo_clientes
def carrito(request):
    return render(request, 'tienda/carrito.html', {'carrito': resumen_carrito(request.user),
                                                  'qr_minutos': settings.MP_QR_MINUTOS})


def _cantidad(request, defecto=1):
    try:
        return max(0, min(99, int(request.POST.get('cantidad', defecto))))
    except (TypeError, ValueError):
        return defecto


@login_required
@solo_clientes
@require_POST
def carrito_agregar(request):
    receta = get_object_or_404(Receta, pk=request.POST.get('receta'), publicada=True)
    stock = stock_por_receta([receta]).get(receta.pk, 0)
    if not stock:
        messages.error(request, f'{receta.nombre} no tiene stock ahora. Podés pedir que te avisemos.')
        return redirect('tienda:producto', receta.slug)
    item, _ = ItemCarrito.objects.get_or_create(user=request.user, receta=receta, defaults={'cantidad': 0})
    nueva = item.cantidad + max(1, _cantidad(request))
    if nueva > stock:
        nueva = stock
        messages.warning(request, f'Solo quedan {stock} piezas de {receta.nombre}: ajustamos la cantidad.')
    item.cantidad = nueva
    item.save()
    messages.success(request, f'Agregaste {receta.nombre} a tu carrito.')
    destino = request.POST.get('volver')
    return redirect(destino if destino and destino.startswith('/') and not destino.startswith('//') else 'tienda:carrito')


@login_required
@solo_clientes
@require_POST
def carrito_actualizar(request, pk):
    item = get_object_or_404(ItemCarrito, pk=pk, user=request.user)
    cant = _cantidad(request, item.cantidad)
    if cant == 0:
        item.delete()
    else:
        item.cantidad = cant
        item.save(update_fields=['cantidad'])
    return redirect('tienda:carrito')


@login_required
@solo_clientes
@require_POST
def carrito_quitar(request, pk):
    ItemCarrito.objects.filter(pk=pk, user=request.user).delete()
    messages.info(request, 'Quitamos el producto del carrito.')
    return redirect('tienda:carrito')


# --------------------------------------------------------------------- pago

@login_required
@solo_clientes
@require_POST
def checkout(request):
    try:
        pedido = crear_pedido_con_qr(request.user, request.POST.get('notas', '')[:500])
    except StockInsuficiente as e:
        for receta, pedidas, disp in e.faltantes:
            messages.error(request, f'{receta.nombre}: pediste {pedidas} y ahora quedan {disp}.')
        return redirect('tienda:carrito')
    except mp.ErrorMercadoPago as e:
        messages.error(request, f'{e} Probá de nuevo en un momento.')
        return redirect('tienda:carrito')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('tienda:carrito')
    return redirect('tienda:pago', pedido.pk)


def _qr_svg(texto):
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return mark_safe(svg[svg.find('<svg'):])


def _pedido_del_cliente(request, pk):
    return get_object_or_404(Pedido.objects.prefetch_related('items__asignaciones__pieza__tanda'), pk=pk,
                             cliente=request.user)


@login_required
def pago(request, pk):
    pedido = sincronizar_pago(_pedido_del_cliente(request, pk))
    qr = _qr_svg(pedido.qr_data) if pedido.esperando_pago and pedido.qr_data else None
    return render(request, 'tienda/pago.html', {'pedido': pedido, 'qr': qr, 'modo_demo': mp.modo_demo()})


@login_required
def pago_estado(request, pk):
    pedido = sincronizar_pago(_pedido_del_cliente(request, pk))
    return JsonResponse({'estado': pedido.estado, 'segundos': pedido.segundos_qr,
                         'texto': pedido.get_estado_display()})


@login_required
@require_POST
def simular_pago(request, pk):
    """Solo en MODO DEMO (sin token de Mercado Pago): aprueba el pago para probar el circuito."""
    if not mp.modo_demo():
        raise Http404
    confirmar_pago(_pedido_del_cliente(request, pk))
    return redirect('tienda:pago', pk)


@login_required
@require_POST
def cancelar_pago(request, pk):
    cancelar_pedido_pendiente(_pedido_del_cliente(request, pk), 'Cancelado por el cliente')
    messages.info(request, 'Cancelaste el pago. Tus productos siguen en el carrito.')
    return redirect('tienda:carrito')


@csrf_exempt
@require_POST
def webhook_mp(request):
    """Notificación de Mercado Pago (tópico Order). Se configura en Tus integraciones > Webhooks."""
    try:
        cuerpo = json.loads(request.body or b'{}')
    except ValueError:
        cuerpo = {}
    order_id = (cuerpo.get('data') or {}).get('id') or request.GET.get('data.id')
    if not order_id:
        return HttpResponse(status=200)
    if not mp.firma_valida(request, order_id):
        log.warning('Webhook MP con firma inválida: %s', order_id)
        return HttpResponse(status=401)
    pedido = Pedido.objects.filter(mp_order_id=order_id).first()
    if pedido:
        sincronizar_pago(pedido)
    return HttpResponse(status=200)


# --------------------------------------------------------------------- cuenta

@login_required
@solo_clientes
def mis_pedidos(request):
    pedidos = (Pedido.objects.filter(cliente=request.user)
               .exclude(estado__in=[Pedido.VENCIDO])
               .prefetch_related('items'))
    avisos = AvisoStock.objects.filter(user=request.user).select_related('receta').order_by('-creado')[:20]
    AvisoStock.objects.filter(user=request.user, avisado_en__isnull=False, visto=False).update(visto=True)
    return render(request, 'tienda/mis_pedidos.html', {'pedidos': pedidos, 'avisos': avisos})


@login_required
def pedido_detalle(request, pk):
    return render(request, 'tienda/pedido.html', {'pedido': _pedido_del_cliente(request, pk)})


@login_required
@solo_clientes
@require_POST
def avisame(request, pk):
    receta = get_object_or_404(Receta, pk=pk, publicada=True)
    if pedir_aviso(request.user, receta):
        messages.success(request, f'Listo: te avisamos por correo cuando vuelva a haber {receta.nombre}.')
    else:
        messages.info(request, f'Ya estabas anotado/a para {receta.nombre}.')
    return redirect('tienda:producto', receta.slug)


@login_required
@require_POST
def avisame_quitar(request, pk):
    AvisoStock.objects.filter(user=request.user, receta_id=pk, avisado_en__isnull=True).delete()
    messages.info(request, 'Ya no te vamos a avisar de ese producto.')
    return redirect(request.POST.get('volver') or 'tienda:mis_pedidos')


def nosotros(request):
    from core.models import PaginaNosotros
    return render(request, 'tienda/nosotros.html', {'pagina': PaginaNosotros.obtener()})


@login_required
def pedido_comprobante(request, pk):
    pedido = _pedido_del_cliente(request, pk)
    if not pedido.pagado_en:
        raise Http404
    return render(request, 'comprobante.html', {'pedido': pedido, 'volver': f'/mis-pedidos/{pk}/'})


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', '') or 'sin-ip')


def ayuda(request):
    """Preguntas frecuentes + formulario de contacto (la consulta se guarda y se manda por correo)."""
    from django.core.cache import cache

    from core.correo import enviar_consulta
    from core.forms import ContactoForm
    from core.models import PreguntaFrecuente

    inicial = {}
    u = request.user
    if u.is_authenticated:
        cliente = getattr(u, 'cliente', None)
        inicial = {'nombre': u.get_full_name() or u.username, 'email': u.email,
                   'telefono': getattr(cliente, 'telefono', '')}
    form = ContactoForm(request.POST or None, initial=inicial)

    if request.method == 'POST':
        clave = f'contacto:{_ip(request)}'
        enviados = cache.get(clave, 0)
        if enviados >= 3:
            messages.error(request, 'Ya mandaste varias consultas seguidas. Esperá unos minutos y probá de nuevo.')
        elif form.is_valid():
            if not form.es_robot():
                consulta = form.save(commit=False)
                if u.is_authenticated:
                    consulta.usuario = u
                consulta.save()
                enviar_consulta(consulta, request)
                cache.set(clave, enviados + 1, 60 * 10)
            messages.success(request, '¡Gracias! Recibimos tu consulta y te respondemos a tu correo lo antes posible.')
            return redirect(reverse('tienda:ayuda') + '#contacto')

    return render(request, 'tienda/ayuda.html', {
        'preguntas': PreguntaFrecuente.objects.filter(visible=True), 'form': form})
