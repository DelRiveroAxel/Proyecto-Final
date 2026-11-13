"""Carga datos de ejemplo para probar el sistema.

    python manage.py cargar_demo

Crea el productor (usuario: productor / clave: Iurko2026!) y un cliente
(usuario: cliente / clave: Iurko2026!), materias primas, compras, recetas y tandas.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from produccion.models import (CompraMateriaPrima, IngredienteReceta, LoteMateriaPrima, Proveedor, Receta, Tanda,
                               TipoMateriaPrima, UnidadMedida)
from produccion.services import crear_tanda, pausar_tanda
from tienda.models import Cliente, ItemCarrito, TipoCliente
from tienda.services import confirmar_pago, crear_pedido_con_qr
from users.models import User

CLAVE = 'Iurko2026!'
PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj '
       b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF')

RECETAS = [
    # nombre, categoría, subtítulo, etiqueta, descripción, $/kg, g/pieza, presentación, días
    ('Jamón Crudo Reserva', 'CURADO', 'Corte noble superior', '18 meses cava',
     'Curado a temperatura constante en sótano de piedra. Salado bajo, veteado graso untuoso y aroma a frutos secos.',
     16800, 500, 'Porción al vacío', 0),
    ('Salame Criollo', 'CURADO', 'Receta colonial 1920', 'Picado grueso',
     'Pulpa de cerdo y vaca, tocino dorsal, pimienta negra en grano machacada y vino blanco macerado.',
     12000, 350, 'Pieza entera', 0),
    ('Bondiola al Vino Tinto', 'CURADO', 'Corte estacionado', 'Maceración Malbec',
     'Macerada durante 14 días en barrica con vino tinto de guarda y especias seleccionadas.',
     17200, 400, 'Trozo al vacío', 0),
    ('Panceta Ahumada Artesanal', 'AHUMADO', 'Horno de barro', 'Humo quebracho',
     'Ahumada en frío durante 48 horas continuas con leña de quebracho.', 11300, 450, 'Pieza envasada', 0),
    ('Cantimpalo Tradicional', 'CURADO', 'Herencia española', 'Pimentón dulce',
     'Elaborado con pimentón ahumado, ajo asado y un toque de orégano.', 12600, 380, 'Rosca', 21),
    ('Salame con Finas Hierbas', 'CURADO', 'Edición de huerta', 'Costra aromática',
     'Rebozado exterior en tomillo silvestre, romero fresco y pimienta.', 14400, 320, 'Bastón', 0),
]


class Command(BaseCommand):
    help = 'Carga datos de ejemplo (productor, cliente, insumos, recetas, tandas y un pedido).'

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
        for n, cat, sub, et, desc, precio, peso, pres, dias in RECETAS:
            r = Receta.objects.create(nombre=n, categoria=cat, subtitulo=sub, etiqueta=et, descripcion=desc,
                                      precio_kg=precio, peso_nominal_g=peso, presentacion=pres,
                                      rendimiento_base_kg=10, dias_estacionamiento=dias)
            recetas[n] = r
            for ins, cant in [('Carne de cerdo', 9), ('Tocino dorsal', 2), ('Sal entrefina', Decimal('0.28')),
                              ('Pimienta negra', Decimal('0.03')), ('Tripa natural', 25)]:
                IngredienteReceta.objects.create(receta=r, tipo=insumos[ins], cantidad=cant)
        IngredienteReceta.objects.create(receta=recetas['Cantimpalo Tradicional'], tipo=insumos['Pimentón'], cantidad=Decimal('0.25'))
        IngredienteReceta.objects.create(receta=recetas['Bondiola al Vino Tinto'], tipo=insumos['Vino tinto'], cantidad=1)

        def tanda(nombre, dias, kgs, piezas):
            return crear_tanda(receta=recetas[nombre], kg_producidos=kgs, cantidad_piezas=piezas,
                               fecha_produccion=hoy - timedelta(days=dias), usuario=prod)
        tanda('Salame Criollo', 30, 28, 80)
        tanda('Jamón Crudo Reserva', 28, 25, 50)
        tanda('Bondiola al Vino Tinto', 20, Decimal('16.8'), 42)
        t_pausa = tanda('Panceta Ahumada Artesanal', 15, 18, 40)
        tanda('Salame Criollo', 10, 21, 60)
        tanda('Salame con Finas Hierbas', 8, Decimal('3.2'), 10)
        tanda('Cantimpalo Tradicional', 2, 19, 50)          # estacionando 21 días
        pausar_tanda(t_pausa, 'Control de calidad: revisar temperatura del ahumado')

        # Un pedido pagado del cliente demo
        ItemCarrito.objects.create(user=cli, receta=recetas['Salame Criollo'], cantidad=2)
        ItemCarrito.objects.create(user=cli, receta=recetas['Jamón Crudo Reserva'], cantidad=1)
        confirmar_pago(crear_pedido_con_qr(cli, 'Retiro el sábado'))
        # Agota el salame con hierbas para mostrar "sin stock"
        ItemCarrito.objects.create(user=cli, receta=recetas['Salame con Finas Hierbas'], cantidad=10)
        confirmar_pago(crear_pedido_con_qr(cli))
        ItemCarrito.objects.create(user=cli, receta=recetas['Bondiola al Vino Tinto'], cantidad=1)

        self.stdout.write(self.style.SUCCESS(
            f'Listo. Productor: productor / {CLAVE} · Cliente: cliente / {CLAVE}'))
