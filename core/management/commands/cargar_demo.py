"""Carga datos de ejemplo para probar el sistema.

    python manage.py cargar_demo

Crea el productor (usuario: productor / clave: Iurko2026!), tres clientes (cliente, martin y sofia, todos con
clave Iurko2026!), materias primas, compras, recetas y tandas. Además deja ejemplos de todo lo que muestra el
panel: pedidos en cada estado, una tanda por vencer, una pausada y otra estacionándose, ventas por fuera de la
web, una baja, un "avisame cuando haya" y una consulta del formulario de contacto.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from produccion.models import (CompraMateriaPrima, IngredienteReceta, LoteMateriaPrima, Proveedor, Receta, Tanda,
                               TipoMateriaPrima, UnidadMedida)
from core.models import ConsultaContacto
from produccion.services import crear_tanda, dar_baja_piezas, pausar_tanda, vender_fuera_de_web
from tienda.models import Cliente, ItemCarrito, Pedido, TipoCliente
from tienda.services import confirmar_pago, crear_pedido_con_qr, pedir_aviso
from users.models import User

CLAVE = 'Iurko2026!'
PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj '
       b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF')

RECETAS = [
    # nombre, categoría, subtítulo, etiqueta, descripción, $/kg, g/pieza, presentación,
    # días de estacionamiento, días hasta el vencimiento (desde la producción)
    ('Jamón Crudo Reserva', 'CURADO', 'Corte noble superior', '18 meses cava',
     'Curado a temperatura constante en sótano de piedra. Salado bajo, veteado graso untuoso y aroma a frutos secos.',
     16800, 500, 'Porción al vacío', 0, 180),
    ('Salame Criollo', 'CURADO', 'Receta colonial 1920', 'Picado grueso',
     'Pulpa de cerdo y vaca, tocino dorsal, pimienta negra en grano machacada y vino blanco macerado.',
     12000, 350, 'Pieza entera', 0, 60),
    ('Bondiola al Vino Tinto', 'CURADO', 'Corte estacionado', 'Maceración Malbec',
     'Macerada durante 14 días en barrica con vino tinto de guarda y especias seleccionadas.',
     17200, 400, 'Trozo al vacío', 0, 25),
    ('Panceta Ahumada Artesanal', 'AHUMADO', 'Horno de barro', 'Humo quebracho',
     'Ahumada en frío durante 48 horas continuas con leña de quebracho.', 11300, 450, 'Pieza envasada', 0, 40),
    ('Cantimpalo Tradicional', 'CURADO', 'Herencia española', 'Pimentón dulce',
     'Elaborado con pimentón ahumado, ajo asado y un toque de orégano.', 12600, 380, 'Rosca', 21, 60),
    ('Salame con Finas Hierbas', 'CURADO', 'Edición de huerta', 'Costra aromática',
     'Rebozado exterior en tomillo silvestre, romero fresco y pimienta.', 14400, 320, 'Bastón', 0, 45),
]


class Command(BaseCommand):
    help = 'Carga datos de ejemplo (productor, clientes, insumos, recetas, tandas, pedidos y ventas).'

    @transaction.atomic
    def handle(self, *args, **opts):
        if Receta.objects.exists():
            self.stdout.write(self.style.WARNING('Ya hay recetas cargadas: no se cargó nada.'))
            return
        hoy = timezone.localdate()
        prod, _ = User.objects.get_or_create(username='productor', defaults={
            'first_name': 'Iván', 'last_name': 'Iurko', 'email': 'productor@iurko.com', 'is_staff': True})
        prod.set_password(CLAVE); prod.is_staff = True; prod.save()
        cli, _ = User.objects.get_or_create(username='cliente', defaults={
            'first_name': 'Lucía', 'last_name': 'Benítez', 'email': 'cliente@iurko.com'})
        cli.set_password(CLAVE); cli.save()
        minorista, _ = TipoCliente.objects.get_or_create(nombre='Minorista')
        TipoCliente.objects.get_or_create(nombre='Mayorista')
        Cliente.objects.get_or_create(user=cli, defaults={'telefono': '3755 412345', 'localidad': 'Oberá', 'tipo': minorista})
        otros = {}
        for usuario, nombre, apellido, tel, loc in [('martin', 'Martín', 'Duarte', '3755 400111', 'Oberá'),
                                                    ('sofia', 'Sofía', 'Krause', '3764 223344', 'Posadas')]:
            u, _ = User.objects.get_or_create(username=usuario, defaults={
                'first_name': nombre, 'last_name': apellido, 'email': f'{usuario}@ejemplo.com'})
            u.set_password(CLAVE); u.save()
            Cliente.objects.get_or_create(user=u, defaults={'telefono': tel, 'localidad': loc, 'tipo': minorista})
            otros[usuario] = u

        kg, _ = UnidadMedida.objects.get_or_create(nombre='Kilogramo', abreviatura='kg')
        m, _ = UnidadMedida.objects.get_or_create(nombre='Metro', abreviatura='m')
        insumos = {}
        for nombre, unidad, minimo in [('Carne de cerdo', kg, 50), ('Carne vacuna', kg, 20), ('Tocino dorsal', kg, 15),
                                       ('Sal entrefina', kg, 5), ('Pimienta negra', kg, 1), ('Pimentón', kg, 2),
                                       ('Vino tinto', kg, 5), ('Tripa natural', m, 100)]:
            insumos[nombre], _ = TipoMateriaPrima.objects.get_or_create(nombre=nombre, defaults={'unidad': unidad, 'stock_minimo': minimo})

        def compra(dias, vendedor, renglones, num):
            c = CompraMateriaPrima(fecha=hoy - timedelta(days=dias), tipo_comprobante='FACTURA', numero_comprobante=num,
                                   proveedor=Proveedor.objects.get_or_create(nombre=vendedor)[0], creado_por=prod)
            c.comprobante.save(f'demo_{num}.pdf', ContentFile(PDF), save=False)
            c.save()
            for nombre, cant, precio in renglones:
                LoteMateriaPrima.objects.create(compra=c, tipo=insumos[nombre], cantidad=cant, cantidad_disponible=cant,
                                                precio_unitario=precio, lote=f'L{num[-3:]}')
        compra(40, 'Frigorífico Oberá', [('Carne de cerdo', 300, 4200), ('Carne vacuna', 80, 6900), ('Tocino dorsal', 60, 2600)], '0003-00012345')
        compra(25, 'Distribuidora del Alto Paraná', [('Sal entrefina', 25, 650), ('Pimienta negra', 4, 21000), ('Pimentón', 6, 9800), ('Tripa natural', 800, 140)], '0001-00004521')
        compra(6, 'Frigorífico Oberá', [('Carne de cerdo', 200, 4450), ('Vino tinto', 30, 3200)], '0003-00012988')

        recetas = {}
        for n, cat, sub, et, desc, precio, peso, pres, dias, vence in RECETAS:
            r = Receta.objects.create(nombre=n, categoria=cat, subtitulo=sub, etiqueta=et, descripcion=desc,
                                      precio_kg=precio, peso_nominal_g=peso, presentacion=pres,
                                      rendimiento_base_kg=10, dias_estacionamiento=dias, dias_vencimiento=vence)
            recetas[n] = r
            for ins, cant in [('Carne de cerdo', 9), ('Tocino dorsal', 2), ('Sal entrefina', Decimal('0.28')),
                              ('Pimienta negra', Decimal('0.03')), ('Tripa natural', 25)]:
                IngredienteReceta.objects.create(receta=r, tipo=insumos[ins], cantidad=cant)
        IngredienteReceta.objects.create(receta=recetas['Cantimpalo Tradicional'], tipo=insumos['Pimentón'], cantidad=Decimal('0.25'))
        IngredienteReceta.objects.create(receta=recetas['Bondiola al Vino Tinto'], tipo=insumos['Vino tinto'], cantidad=1)

        def tanda(nombre, dias, kgs, piezas):
            return crear_tanda(receta=recetas[nombre], kg_producidos=kgs, cantidad_piezas=piezas,
                               fecha_produccion=hoy - timedelta(days=dias), usuario=prod)
        t_salame = tanda('Salame Criollo', 30, 28, 80)
        t_jamon = tanda('Jamón Crudo Reserva', 28, 25, 50)
        tanda('Bondiola al Vino Tinto', 20, Decimal('16.8'), 42)   # vence en 5 días: aparece el aviso "por vencer"
        t_pausa = tanda('Panceta Ahumada Artesanal', 15, 18, 40)
        tanda('Salame Criollo', 10, 21, 60)
        tanda('Salame con Finas Hierbas', 8, Decimal('3.2'), 10)
        tanda('Cantimpalo Tradicional', 2, 19, 50)          # estacionando 21 días
        pausar_tanda(t_pausa, 'Control de calidad: revisar temperatura del ahumado')

        # Pedidos pagados por la tienda, uno en cada estado del seguimiento
        def pedido(user, items, notas='', estado=None):
            for nombre, cant in items:
                ItemCarrito.objects.create(user=user, receta=recetas[nombre], cantidad=cant)
            p = crear_pedido_con_qr(user, notas)
            confirmar_pago(p)
            if estado:
                p.refresh_from_db()
                p.estado = estado
                if estado == Pedido.ENTREGADO:
                    p.entregado_en = timezone.now()
                p.save(update_fields=['estado', 'entregado_en'])
            return p
        pedido(cli, [('Salame Criollo', 2), ('Jamón Crudo Reserva', 1)], 'Retiro el sábado', Pedido.ENTREGADO)
        pedido(cli, [('Salame con Finas Hierbas', 10)], '', Pedido.LISTO)       # agota el salame con hierbas
        pedido(otros['martin'], [('Salame Criollo', 3), ('Bondiola al Vino Tinto', 1)], 'Paso el viernes a la tarde',
               Pedido.PREPARANDO)
        pedido(otros['sofia'], [('Jamón Crudo Reserva', 2)], 'Es para un regalo')  # queda "Pagado": pedido nuevo
        ItemCarrito.objects.create(user=cli, receta=recetas['Bondiola al Vino Tinto'], cantidad=1)  # carrito abierto

        # Ventas por fuera de la web (WhatsApp y feria) y una baja
        libres = lambda t, n: list(t.piezas.filter(estado='DISPONIBLE').order_by('numero').values_list('pk', flat=True)[:n])
        vender_fuera_de_web(t_salame, libres(t_salame, 4), 'WhatsApp', 'Almacén Don Pedro', hoy - timedelta(days=3))
        vender_fuera_de_web(t_jamon, libres(t_jamon, 2), 'Feria o evento', 'Feria franca de Oberá',
                            hoy - timedelta(days=1), Decimal('17000'))
        dar_baja_piezas(t_salame, libres(t_salame, 1), 'Consumo propio / degustación')

        # "Avisame cuando haya" del producto agotado y una consulta sin responder
        pedir_aviso(otros['sofia'], recetas['Salame con Finas Hierbas'])
        ConsultaContacto.objects.create(nombre='Carlos Méndez', email='carlos@ejemplo.com', telefono='3755 456789',
                                        mensaje='Hola, ¿hacen envíos a Posadas? Quería encargar dos jamones para fin de año.')

        self.stdout.write(self.style.SUCCESS(
            f'Listo. Productor: productor / {CLAVE} · Clientes: cliente, martin y sofia / {CLAVE}'))
