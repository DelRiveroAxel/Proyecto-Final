from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from produccion.models import (CompraMateriaPrima, IngredienteReceta, LoteMateriaPrima, Pieza, Proveedor, Receta,
                               Tanda, TipoMateriaPrima, UnidadMedida)
from produccion.services import FaltanInsumos, crear_tanda, pausar_tanda, reanudar_tanda, repartir_pesos
from users.models import User

from .models import AvisoStock, ItemCarrito, Pedido
from .services import (StockInsuficiente, confirmar_pago, crear_pedido_con_qr, enviar_avisos_stock,
                       liberar_reservas_vencidas, pedir_aviso)


class Base(TestCase):
    def setUp(self):
        kg = UnidadMedida.objects.get(abreviatura='kg')   # la crea la migración 0004
        self.cerdo = TipoMateriaPrima.objects.create(nombre='Carne de cerdo', unidad=kg)
        self.sal = TipoMateriaPrima.objects.create(nombre='Sal', unidad=kg)
        prov = Proveedor.objects.create(nombre='Frigorífico Oberá')
        c1 = CompraMateriaPrima.objects.create(fecha=date(2026, 10, 1), proveedor=prov, comprobante='x.pdf')
        c2 = CompraMateriaPrima.objects.create(fecha=date(2026, 10, 20), proveedor=prov, comprobante='y.pdf')
        self.lote_viejo = LoteMateriaPrima.objects.create(compra=c1, tipo=self.cerdo, cantidad=50,
                                                          cantidad_disponible=50, precio_unitario=4000)
        self.lote_nuevo = LoteMateriaPrima.objects.create(compra=c2, tipo=self.cerdo, cantidad=100,
                                                          cantidad_disponible=100, precio_unitario=4500)
        LoteMateriaPrima.objects.create(compra=c1, tipo=self.sal, cantidad=10, cantidad_disponible=10,
                                        precio_unitario=800)
        self.salame = Receta.objects.create(nombre='Salame Criollo', precio_kg=12000, peso_nominal_g=500,
                                            rendimiento_base_kg=10)
        IngredienteReceta.objects.create(receta=self.salame, tipo=self.cerdo, cantidad=11)   # 11 kg cada 10 kg
        IngredienteReceta.objects.create(receta=self.salame, tipo=self.sal, cantidad=Decimal('0.3'))
        self.cliente = User.objects.create_user('lucia', 'lucia@test.com', 'Clave-segura-123')


class CodigosYFifoTests(Base):
    def test_codigo_14_digitos_y_numero_de_tanda_del_dia(self):
        t1 = crear_tanda(receta=self.salame, kg_producidos=80, cantidad_piezas=160, fecha_produccion=date(2026, 11, 5))
        t2 = crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10, fecha_produccion=date(2026, 11, 5),
                         descontar_insumos=False)
        self.assertEqual(t1.codigo, '0511202601')
        self.assertEqual(t2.codigo, '0511202602')
        p = t1.piezas.get(numero=21)
        self.assertEqual(p.codigo, '05112026010021')
        self.assertEqual(len(p.codigo), 14)
        self.assertTrue(p.codigo.isdigit())
        self.assertEqual(t1.piezas.count(), 160)
        self.assertEqual(sum(t1.piezas.values_list('peso_g', flat=True)), 80000)

    def test_insumos_se_descuentan_del_lote_mas_viejo(self):
        t = crear_tanda(receta=self.salame, kg_producidos=80, cantidad_piezas=160)
        self.lote_viejo.refresh_from_db(); self.lote_nuevo.refresh_from_db()
        # 80 kg necesitan 88 kg de cerdo: 50 del lote viejo + 38 del nuevo
        self.assertEqual(self.lote_viejo.cantidad_disponible, 0)
        self.assertEqual(self.lote_nuevo.cantidad_disponible, 62)
        self.assertEqual(t.costo_insumos, Decimal(50 * 4000 + 38 * 4500 + Decimal('2.4') * 800))

    def test_faltan_insumos(self):
        with self.assertRaises(FaltanInsumos):
            crear_tanda(receta=self.salame, kg_producidos=500, cantidad_piezas=100)
        self.assertFalse(Tanda.objects.exists())

    def test_fifo_usa_fecha_real_no_el_texto(self):
        # 01-12 como texto ("0112...") es menor que "0511...", pero es más nueva
        nueva = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, fecha_produccion=date(2026, 12, 1),
                            descontar_insumos=False)
        vieja = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, fecha_produccion=date(2026, 11, 5),
                            descontar_insumos=False)
        self.assertLess(nueva.codigo, vieja.codigo)
        primera = Pieza.objects.disponibles().first()
        self.assertEqual(primera.tanda, vieja)

    def test_repartir_pesos_no_pierde_gramos(self):
        self.assertEqual(sum(repartir_pesos(Decimal('10'), 3)), 10000)


class ReservaYPagoTests(Base):
    def setUp(self):
        super().setUp()
        self.t_vieja = crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4,
                                   fecha_produccion=date(2026, 10, 1), descontar_insumos=False)
        self.t_nueva = crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4,
                                   fecha_produccion=date(2026, 11, 1), descontar_insumos=False)

    def carrito(self, n, user=None):
        ItemCarrito.objects.update_or_create(user=user or self.cliente, receta=self.salame, defaults={'cantidad': n})

    def test_qr_reserva_piezas_fifo_y_cobra_peso_real(self):
        Pieza.objects.filter(tanda=self.t_vieja, numero=1).update(peso_g=520)
        self.carrito(3)
        pedido = crear_pedido_con_qr(self.cliente)
        reservadas = Pieza.objects.filter(estado=Pieza.RESERVADA)
        self.assertEqual(reservadas.count(), 3)
        self.assertEqual(set(reservadas.values_list('tanda', flat=True)), {self.t_vieja.pk})
        self.assertEqual(pedido.total, Decimal(12000 * (520 + 500 + 500) / 1000))
        self.assertTrue(pedido.qr_data)
        # otro cliente no puede llevarse las reservadas
        otro = User.objects.create_user('juan', 'j@t.com', 'Clave-segura-123')
        self.carrito(6, otro)   # quedan 5 libres (3 reservadas)
        with self.assertRaises(StockInsuficiente):
            crear_pedido_con_qr(otro)
        self.assertEqual(Pieza.objects.filter(estado=Pieza.RESERVADA).count(), 3)   # nada nuevo reservado

    def test_pago_confirma_y_vacia_carrito(self):
        self.carrito(2)
        pedido = confirmar_pago(crear_pedido_con_qr(self.cliente))
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(Pieza.objects.filter(estado=Pieza.VENDIDA).count(), 2)
        self.assertFalse(ItemCarrito.objects.filter(user=self.cliente).exists())

    def test_qr_vencido_libera_piezas(self):
        self.carrito(4)
        pedido = crear_pedido_con_qr(self.cliente)
        Pedido.objects.filter(pk=pedido.pk).update(reserva_hasta=timezone.now() - timedelta(seconds=1))
        liberar_reservas_vencidas()
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.VENCIDO)
        self.assertEqual(Pieza.objects.filter(estado=Pieza.RESERVADA).count(), 0)
        self.t_vieja.refresh_from_db()
        self.assertEqual(self.t_vieja.estado, Tanda.ACTIVA)

    def test_pausar_tanda_cancela_qr_pendiente_y_no_se_vende(self):
        self.carrito(2)
        pedido = crear_pedido_con_qr(self.cliente)
        pausar_tanda(self.t_vieja, 'Olor raro en cámara')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertFalse(Pieza.objects.a_la_venta().filter(tanda=self.t_vieja).exists())
        self.assertEqual(Pieza.objects.a_la_venta().first().tanda, self.t_nueva)
        reanudar_tanda(self.t_vieja)
        self.assertEqual(Pieza.objects.a_la_venta().first().tanda, self.t_vieja)

    def test_tanda_agotada(self):
        self.carrito(8)
        confirmar_pago(crear_pedido_con_qr(self.cliente))
        self.t_vieja.refresh_from_db(); self.t_nueva.refresh_from_db()
        self.assertEqual(self.t_vieja.estado, Tanda.AGOTADA)
        self.assertEqual(self.t_nueva.estado, Tanda.AGOTADA)

    def test_aviso_stock_cuando_llega_tanda_nueva(self):
        self.carrito(8)
        confirmar_pago(crear_pedido_con_qr(self.cliente))
        otro = User.objects.create_user('ana', 'ana@t.com', 'Clave-segura-123')
        self.assertTrue(pedir_aviso(otro, self.salame))
        self.assertEqual(enviar_avisos_stock(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, descontar_insumos=False)
        self.assertIsNotNone(AvisoStock.objects.get(user=otro).avisado_en)

    def test_estacionando_no_se_vende(self):
        self.salame.dias_estacionamiento = 20
        self.salame.save()
        t = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, descontar_insumos=False)
        self.assertTrue(t.estacionando)
        self.assertFalse(Pieza.objects.a_la_venta().filter(tanda=t).exists())


class VistasTests(Base):
    def setUp(self):
        super().setUp()
        crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4, descontar_insumos=False)
        self.productor = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)

    def test_paginas_publicas_y_flujo_de_compra(self):
        self.assertEqual(self.client.get(reverse('tienda:catalogo')).status_code, 200)
        self.assertEqual(self.client.get(reverse('tienda:producto', args=[self.salame.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        self.assertEqual(self.client.get(reverse('registro')).status_code, 200)
        self.client.force_login(self.cliente)
        self.client.post(reverse('tienda:carrito_agregar'), {'receta': self.salame.pk, 'cantidad': 2})
        self.assertEqual(self.client.get(reverse('tienda:carrito')).status_code, 200)
        r = self.client.post(reverse('tienda:checkout'))
        pedido = Pedido.objects.get()
        self.assertRedirects(r, reverse('tienda:pago', args=[pedido.pk]))
        self.assertContains(self.client.get(reverse('tienda:pago', args=[pedido.pk])), '<svg')
        self.client.post(reverse('tienda:simular_pago', args=[pedido.pk]))
        estado = self.client.get(reverse('tienda:pago_estado', args=[pedido.pk])).json()
        self.assertEqual(estado['estado'], Pedido.PAGADO)
        self.assertEqual(self.client.get(reverse('tienda:mis_pedidos')).status_code, 200)

    def test_panel_solo_para_productor(self):
        self.client.force_login(self.cliente)
        self.assertNotEqual(self.client.get(reverse('panel:inicio')).status_code, 200)
        self.client.force_login(self.productor)
        for nombre in ['inicio', 'tandas', 'tanda_nueva', 'insumos', 'compras', 'compra_nueva', 'recetas',
                       'receta_nueva', 'pedidos']:
            self.assertEqual(self.client.get(reverse(f'panel:{nombre}')).status_code, 200, nombre)
        t = Tanda.objects.get()
        self.assertEqual(self.client.get(reverse('panel:tanda', args=[t.pk])).status_code, 200)
        self.client.post(reverse('panel:tanda_pausar', args=[t.pk]), {'motivo': 'Control de calidad'})
        t.refresh_from_db()
        self.assertEqual(t.estado, Tanda.PAUSADA)


class NovedadesTests(Base):
    """Cambios de la segunda tanda de pedidos del cliente."""

    def setUp(self):
        super().setUp()
        self.productor = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        self.client.force_login(self.productor)

    def test_unidades_basicas_y_conversion_kg_a_g(self):
        self.assertTrue(UnidadMedida.objects.filter(abreviatura='g').exists())
        self.assertTrue(UnidadMedida.objects.filter(abreviatura='u').exists())
        crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)   # consume 0,3 kg de sal
        g = UnidadMedida.objects.get(abreviatura='g')
        self.client.post(reverse('panel:insumo_unidad', args=[self.sal.pk]), {'unidad': g.pk})
        self.sal.refresh_from_db()
        lote = self.sal.lotes.get()
        ing = self.salame.ingredientes.get(tipo=self.sal)
        self.assertEqual(self.sal.unidad, g)
        self.assertEqual(lote.cantidad, 10000)
        self.assertEqual(lote.cantidad_disponible, Decimal('9700'))
        self.assertEqual(lote.precio_unitario, Decimal('0.80'))
        self.assertEqual(ing.cantidad, 300)
        # Con la misma receta en gramos, la tanda sigue funcionando igual
        crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_disponible, Decimal('9400'))

    def test_dias_de_estacionamiento_por_tanda_y_liberar(self):
        r = self.client.post(reverse('panel:tanda_nueva'), {
            'receta': self.salame.pk, 'fecha_produccion': timezone.localdate().isoformat(),
            'kg_producidos': '2', 'cantidad_piezas': '4', 'dias_estacionamiento': '15'})
        t = Tanda.objects.get()
        self.assertEqual(t.fecha_disponible, timezone.localdate() + timedelta(days=15))
        self.assertTrue(t.estacionando)
        self.client.post(reverse('panel:tanda_fecha_venta', args=[t.pk]), {'ahora': '1'})
        t.refresh_from_db()
        self.assertTrue(t.a_la_venta)

    def test_quitar_ingrediente_y_renglones_nuevos(self):
        ing_sal = self.salame.ingredientes.get(tipo=self.sal)
        ing_cerdo = self.salame.ingredientes.get(tipo=self.cerdo)
        datos = {f: getattr(self.salame, f) for f in ['nombre', 'categoria', 'precio_kg', 'peso_nominal_g',
                                                      'rendimiento_base_kg', 'dias_estacionamiento', 'stock_minimo_piezas']}
        datos.update({'publicada': 'on', 'subtitulo': '', 'etiqueta': '', 'descripcion': '', 'presentacion': '',
                      'ing-TOTAL_FORMS': '4', 'ing-INITIAL_FORMS': '2', 'ing-MIN_NUM_FORMS': '0', 'ing-MAX_NUM_FORMS': '1000',
                      'ing-0-id': ing_cerdo.pk, 'ing-0-tipo': self.cerdo.pk, 'ing-0-cantidad': '11',
                      'ing-1-id': ing_sal.pk, 'ing-1-tipo': '', 'ing-1-cantidad': '', 'ing-1-DELETE': 'on',
                      'ing-2-tipo': self.sal.pk, 'ing-2-cantidad': '0.25',         # renglón nuevo
                      'ing-3-tipo': '', 'ing-3-cantidad': ''})                      # vacío: se ignora
        r = self.client.post(reverse('panel:receta', args=[self.salame.pk]), datos)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.salame.ingredientes.count(), 2)
        self.assertEqual(self.salame.ingredientes.get(tipo=self.sal).cantidad, Decimal('0.25'))
        # Repetir una materia prima da un error claro
        datos.update({'ing-TOTAL_FORMS': '2', 'ing-INITIAL_FORMS': '2',
                      'ing-0-id': self.salame.ingredientes.get(tipo=self.cerdo).pk,
                      'ing-1-id': self.salame.ingredientes.get(tipo=self.sal).pk, 'ing-1-tipo': self.cerdo.pk,
                      'ing-1-cantidad': '1', 'ing-1-DELETE': ''})
        datos.pop('ing-2-tipo'); datos.pop('ing-2-cantidad'); datos.pop('ing-3-tipo'); datos.pop('ing-3-cantidad')
        self.assertContains(self.client.post(reverse('panel:receta', args=[self.salame.pk]), datos), 'está dos veces')

    def test_alertas_novedades_y_comprobante(self):
        self.salame.stock_minimo_piezas = 10
        self.salame.save()
        crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4, descontar_insumos=False)
        self.sal.stock_minimo = 50
        self.sal.save()
        r = self.client.get(reverse('panel:inicio'))
        self.assertEqual([x.nombre for x in r.context['alertas']['recetas_bajo']], ['Salame Criollo'])
        self.assertEqual([x.nombre for x in r.context['alertas']['insumos_bajo']], ['Sal'])
        ItemCarrito.objects.create(user=self.cliente, receta=self.salame, cantidad=1)
        pedido = confirmar_pago(crear_pedido_con_qr(self.cliente))
        self.assertTrue(pedido.novedad)
        self.assertEqual(pedido.mp_metodo, 'demo')
        self.assertEqual(self.client.get(reverse('panel:inicio')).context['alertas']['pedidos_nuevos'], 1)
        self.client.get(reverse('panel:pedido', args=[pedido.pk]))
        self.assertEqual(self.client.get(reverse('panel:inicio')).context['alertas']['pedidos_nuevos'], 0)
        self.assertContains(self.client.get(reverse('panel:pedido_comprobante', args=[pedido.pk])), pedido.mp_payment_id)
        self.client.post(reverse('panel:pedido_avanzar', args=[pedido.pk]))
        self.client.post(reverse('panel:pedido_avanzar', args=[pedido.pk]))
        self.client.post(reverse('panel:pedido_avanzar', args=[pedido.pk]))
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.entregado_en)
        r = self.client.get(reverse('panel:pedidos') + '?estado=ENTREGADO')
        self.assertContains(r, pedido.entregado_en.astimezone(timezone.get_current_timezone()).strftime('%H:%M'))
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get(reverse('tienda:pedido_comprobante', args=[pedido.pk])).status_code, 200)

    def test_pagina_nosotros_editable(self):
        r = self.client.post(reverse('panel:nosotros'), {
            'titulo': 'Somos Iurko', 'bajada': 'x', 'anio_fundacion': '1965', 'historia': 'Historia nueva',
            'mision': 'm', 'valores': 'Uno\nDos', 'equipo': 'Iván — Maestro', 'direccion': 'Oberá',
            'horario': '', 'telefono': '3755 123456', 'email': '', 'instagram': '', 'facebook': ''})
        self.assertEqual(r.status_code, 302)
        self.client.logout()
        r = self.client.get(reverse('tienda:nosotros'))
        self.assertContains(r, 'Somos Iurko')
        self.assertContains(r, 'Historia nueva')
        self.assertContains(r, '1965')


import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

MEDIA_TMP = tempfile.mkdtemp()


def imagen(nombre='foto.jpg', color=(140, 40, 50)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), color).save(buf, 'JPEG')
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class FotosYPreguntasTests(Base):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.productor = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        self.client.force_login(self.productor)

    def datos_receta(self):
        d = {f: getattr(self.salame, f) for f in ['nombre', 'categoria', 'precio_kg', 'peso_nominal_g',
                                                  'rendimiento_base_kg', 'dias_estacionamiento', 'stock_minimo_piezas']}
        d.update({'publicada': 'on', 'subtitulo': '', 'etiqueta': '', 'descripcion': '', 'presentacion': '',
                  'ing-TOTAL_FORMS': '0', 'ing-INITIAL_FORMS': '0', 'ing-MIN_NUM_FORMS': '0', 'ing-MAX_NUM_FORMS': '1000'})
        return d

    def test_varias_fotos_en_receta_y_tanda_y_galeria(self):
        from produccion.models import FotoReceta, FotoTanda
        d = self.datos_receta()
        d['ing-TOTAL_FORMS'] = '2'; d['ing-INITIAL_FORMS'] = '2'
        for i, ing in enumerate(self.salame.ingredientes.all()):
            d[f'ing-{i}-id'] = ing.pk; d[f'ing-{i}-tipo'] = ing.tipo_id; d[f'ing-{i}-cantidad'] = str(ing.cantidad)
        d['foto'] = imagen('principal.jpg')
        d['fotos_extra'] = [imagen('a.jpg'), imagen('b.jpg')]
        r = self.client.post(reverse('panel:receta', args=[self.salame.pk]), d)
        self.assertEqual(r.status_code, 302)
        self.salame.refresh_from_db()
        self.assertTrue(self.salame.foto)
        self.assertEqual(self.salame.fotos.count(), 2)
        # La tanda hereda las de la receta y suma las propias
        t = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, descontar_insumos=False)
        self.client.post(reverse('panel:tanda_fotos', args=[t.pk]), {'fotos': [imagen('t1.jpg'), imagen('t2.jpg')]})
        self.assertEqual(t.fotos.count(), 2)
        self.assertEqual(len(self.salame.galeria()), 5)
        r = self.client.get(reverse('panel:tanda', args=[t.pk]))
        self.assertContains(r, 'De la receta', count=3)
        self.assertContains(self.client.get(reverse('tienda:producto', args=[self.salame.slug])), 'data-galeria-foto', count=5)
        # Cambiar la principal y borrar
        extra = self.salame.fotos.first()
        nueva_principal = extra.imagen.name
        self.client.post(reverse('panel:receta_foto_principal', args=[extra.pk]))
        self.salame.refresh_from_db()
        self.assertEqual(self.salame.foto.name, nueva_principal)
        self.assertEqual(self.salame.fotos.count(), 2)
        self.client.post(reverse('panel:foto_borrar', args=['tanda', t.fotos.first().pk]))
        self.assertEqual(FotoTanda.objects.count(), 1)
        # Un archivo que no es imagen se rechaza
        r = self.client.post(reverse('panel:tanda_fotos', args=[t.pk]),
                             {'fotos': [SimpleUploadedFile('x.jpg', b'no es una foto', content_type='image/jpeg')]})
        self.assertEqual(FotoTanda.objects.count(), 1)

    def test_preguntas_frecuentes_editables_y_whatsapp(self):
        from core.models import PaginaNosotros, PreguntaFrecuente
        self.assertGreaterEqual(PreguntaFrecuente.objects.count(), 4)   # las crea la migración
        qs = list(PreguntaFrecuente.objects.all())
        d = {'faq-TOTAL_FORMS': str(len(qs) + 1), 'faq-INITIAL_FORMS': str(len(qs)), 'faq-MIN_NUM_FORMS': '0',
             'faq-MAX_NUM_FORMS': '1000'}
        for i, p in enumerate(qs):
            d.update({f'faq-{i}-id': p.pk, f'faq-{i}-pregunta': p.pregunta, f'faq-{i}-respuesta': p.respuesta,
                      f'faq-{i}-orden': p.orden, f'faq-{i}-visible': 'on'})
        d['faq-0-DELETE'] = 'on'
        d['faq-1-visible'] = ''
        n = len(qs)
        d.update({f'faq-{n}-pregunta': '¿Venden al por mayor?', f'faq-{n}-respuesta': 'Sí, consultanos.',
                  f'faq-{n}-orden': '1', f'faq-{n}-visible': 'on'})
        self.assertEqual(self.client.post(reverse('panel:preguntas'), d).status_code, 302)
        self.client.logout()
        r = self.client.get(reverse('tienda:ayuda'))
        self.assertContains(r, '¿Venden al por mayor?')
        self.assertNotContains(r, qs[0].pregunta)
        self.assertNotContains(r, qs[1].pregunta)
        self.assertNotContains(r, 'wa.me')                     # el teléfono de ejemplo no genera botón
        pag = PaginaNosotros.obtener(); pag.telefono = '3755 412345'; pag.save()
        r = self.client.get(reverse('tienda:ayuda'))
        self.assertContains(r, 'https://wa.me/5493755412345?text=')

    def test_motivo_de_las_alertas(self):
        self.salame.stock_minimo_piezas = 5
        self.salame.dias_estacionamiento = 10
        self.salame.save()
        crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=3, descontar_insumos=False)
        r = self.client.get(reverse('panel:tandas'))
        self.assertContains(r, 'Por qué aparece el aviso en Inventario')
        self.assertContains(r, 'No quedan piezas a la venta')
        self.assertContains(r, '3 estacionando hasta el')
        self.sal.stock_minimo = 50
        self.sal.save()
        self.assertContains(self.client.get(reverse('panel:insumos')), 'quedan 10 kg y el aviso es con menos de 50 kg')
        self.assertEqual(self.client.get(reverse('panel:tanda_nueva') + f'?receta={self.salame.pk}').context['form'].initial['receta'],
                         str(self.salame.pk))


class OrdenFifoTests(Base):
    def test_orden_de_salida_por_receta(self):
        prod = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        self.client.force_login(prod)
        vieja = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, fecha_produccion=date(2026, 10, 6), descontar_insumos=False)
        nueva = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, fecha_produccion=date(2026, 10, 26), descontar_insumos=False)
        r = self.client.get(reverse('panel:tandas'))
        turnos = {t.pk: t.turno for t in r.context['tandas']}
        self.assertEqual(turnos, {vieja.pk: 1, nueva.pk: 2})
        # Si se vende todo lo de la vieja, la siguiente pasa a ser la 1.ª
        Pieza.objects.filter(tanda=vieja).update(estado=Pieza.VENDIDA)
        r = self.client.get(reverse('panel:tandas') + '?estado=todas')
        self.assertEqual({t.pk: t.turno for t in r.context['tandas']}[nueva.pk], 1)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CarruselTests(Base):
    def setUp(self):
        super().setUp()
        self.productor = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)

    def test_productor_crea_slide_y_se_ve_en_el_inicio(self):
        from core.models import SlideInicio
        self.client.force_login(self.productor)
        self.assertEqual(self.client.get(reverse('panel:slide_nueva')).status_code, 200)
        r = self.client.post(reverse('panel:slide_nueva'), {
            'receta': self.salame.pk, 'etiqueta': 'Recién estacionado', 'icono': 'local_fire_department',
            'titulo': 'Salame de la casa', 'texto': 'Curado 30 días.', 'boton_texto': 'Comprar', 'boton_url': '',
            'activo': 'on', 'imagen': imagen('fondo.jpg')})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        s = SlideInicio.objects.get()
        self.assertEqual(s.enlace, self.salame.get_absolute_url())
        self.assertTrue(s.imagen.name.startswith('carrusel/'))
        html = self.client.get(reverse('tienda:catalogo')).content.decode()
        self.assertIn('Salame de la casa', html)
        self.assertIn('Recién estacionado', html)

    def test_ordenar_ocultar_y_borrar(self):
        from core.models import SlideInicio
        self.client.force_login(self.productor)
        a = SlideInicio.objects.create(titulo='Primera', orden=10)
        b = SlideInicio.objects.create(titulo='Segunda', orden=20)
        self.client.post(reverse('panel:slide_accion', args=[b.pk, 'subir']))
        self.assertEqual(list(SlideInicio.objects.values_list('titulo', flat=True)), ['Segunda', 'Primera'])
        self.client.post(reverse('panel:slide_accion', args=[a.pk, 'visible']))
        a.refresh_from_db()
        self.assertFalse(a.activo)
        self.assertNotIn('Primera', self.client.get(reverse('tienda:catalogo')).content.decode())
        self.client.post(reverse('panel:slide_accion', args=[a.pk, 'borrar']))
        self.assertEqual(SlideInicio.objects.count(), 1)

    def test_sin_slides_usa_los_productos_con_stock(self):
        crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10)
        html = self.client.get(reverse('tienda:catalogo')).content.decode()
        self.assertIn('Salame Criollo', html)

    def test_cliente_no_entra_al_carrusel(self):
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get(reverse('panel:carrusel')).status_code, 302)


class ClaveYContactoTests(Base):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()

    def test_recuperar_contrasena_por_correo(self):
        import re

        from django.core import mail
        self.assertContains(self.client.get(reverse('login')), reverse('clave_olvidada'))
        r = self.client.post(reverse('clave_olvidada'), {'email': 'LUCIA@test.com'})
        self.assertRedirects(r, reverse('clave_enviada'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['lucia@test.com'])
        enlace = re.search(r'https?://[^/]+(/\S+)', mail.outbox[0].body).group(1)
        r = self.client.get(enlace, follow=True)          # Django redirige a la URL con 'set-password'
        self.assertContains(r, 'Elegí tu contraseña nueva')
        r = self.client.post(r.redirect_chain[-1][0], {'new_password1': 'Otra-clave-456', 'new_password2': 'Otra-clave-456'})
        self.assertRedirects(r, reverse('clave_lista'))
        self.assertTrue(self.client.login(username='lucia', password='Otra-clave-456'))

    def test_correo_desconocido_no_revela_nada(self):
        from django.core import mail
        r = self.client.post(reverse('clave_olvidada'), {'email': 'nadie@test.com'})
        self.assertRedirects(r, reverse('clave_enviada'))
        self.assertEqual(len(mail.outbox), 0)

    def test_cambiar_contrasena_logueado(self):
        self.client.force_login(self.cliente)
        r = self.client.post(reverse('clave_cambiar'), {'old_password': 'Clave-segura-123',
                                                       'new_password1': 'Nueva-clave-789', 'new_password2': 'Nueva-clave-789'})
        self.assertEqual(r.status_code, 302)
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.check_password('Nueva-clave-789'))

    def test_contacto_guarda_y_manda_correo_a_iurko(self):
        from django.core import mail

        from core.models import ConsultaContacto, PaginaNosotros
        pag = PaginaNosotros.obtener(); pag.email = 'iurko@test.com'; pag.save()
        r = self.client.post(reverse('tienda:ayuda'), {'nombre': 'Ana', 'email': 'ana@test.com', 'telefono': '',
                                                       'mensaje': 'Quiero 10 kg de salame para un evento.'})
        self.assertEqual(r.status_code, 302)
        c = ConsultaContacto.objects.get()
        self.assertTrue(c.correo_enviado)
        self.assertEqual(mail.outbox[0].to, ['iurko@test.com'])
        self.assertEqual(mail.outbox[0].reply_to, ['ana@test.com'])
        self.assertIn('10 kg de salame', mail.outbox[0].body)

    def test_contacto_trampa_y_limite(self):
        from core.models import ConsultaContacto
        datos = {'nombre': 'Bot', 'email': 'b@test.com', 'mensaje': 'Compre seguidores baratos ya', 'sitio': 'spam.com'}
        self.client.post(reverse('tienda:ayuda'), datos)
        self.assertEqual(ConsultaContacto.objects.count(), 0)       # el robot no se guarda
        datos.pop('sitio')
        for _ in range(5):
            self.client.post(reverse('tienda:ayuda'), datos)
        self.assertEqual(ConsultaContacto.objects.count(), 3)       # máximo 3 cada 10 minutos

    def test_panel_consultas_y_correo_de_prueba(self):
        from django.core import mail

        from core.models import ConsultaContacto
        prod = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        c = ConsultaContacto.objects.create(nombre='Ana', email='ana@test.com', mensaje='Hola, ¿hacen envíos?')
        self.client.force_login(prod)
        r = self.client.get(reverse('panel:consultas'))
        self.assertContains(r, '¿hacen envíos?')
        self.assertEqual(r.context['alertas']['consultas_nuevas'], 1)
        self.client.post(reverse('panel:consulta_respondida', args=[c.pk]))
        c.refresh_from_db()
        self.assertTrue(c.respondida)
        self.client.post(reverse('panel:correo_prueba'))
        self.assertEqual(len(mail.outbox), 1)

    def test_pantallas_de_acceso_tienen_boton_de_tema(self):
        for nombre in ['login', 'registro', 'clave_olvidada', 'clave_enviada', 'clave_lista']:
            self.assertContains(self.client.get(reverse(nombre)), 'data-tema', msg_prefix=nombre)


class VencimientoEtiquetasTrazaReportesTests(Base):
    """Vencimiento por tanda, etiquetas A4, trazabilidad y reportes."""

    def setUp(self):
        super().setUp()
        self.prod = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        self.hoy = timezone.localdate()

    def vender(self, n):
        ItemCarrito.objects.update_or_create(user=self.cliente, receta=self.salame, defaults={'cantidad': n})
        return confirmar_pago(crear_pedido_con_qr(self.cliente))

    # ---- vencimiento
    def test_la_receta_define_el_vencimiento_de_la_tanda(self):
        self.salame.dias_vencimiento = 60
        self.salame.save()
        t = crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10, descontar_insumos=False)
        self.assertEqual(t.fecha_vencimiento, self.hoy + timedelta(days=60))
        sin = Receta.objects.create(nombre='Chorizo', precio_kg=9000, peso_nominal_g=100)
        self.assertIsNone(crear_tanda(receta=sin, kg_producidos=1, cantidad_piezas=10, descontar_insumos=False)
                          .fecha_vencimiento)

    def test_lo_vencido_no_se_vende_y_sale_la_siguiente_tanda(self):
        from tienda.services import stock_por_receta
        vieja = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, descontar_insumos=False,
                            fecha_produccion=self.hoy - timedelta(days=40))
        nueva = crear_tanda(receta=self.salame, kg_producidos=1, cantidad_piezas=2, descontar_insumos=False)
        self.assertEqual(Pieza.objects.a_la_venta().first().tanda, vieja)     # FIFO normal
        vieja.fecha_vencimiento = self.hoy            # el día del vencimiento ya no se vende
        vieja.save()
        self.assertEqual(Pieza.objects.a_la_venta().first().tanda, nueva)
        self.assertEqual(stock_por_receta([self.salame])[self.salame.pk], 2)
        self.assertIn('Vencida', vieja.etiqueta_estado()[0])
        pedido = self.vender(2)
        self.assertEqual({a.pieza.tanda_id for it in pedido.items.all() for a in it.asignaciones.all()}, {nueva.pk})

    def test_aviso_en_el_panel_y_baja_de_vencidas(self):
        from django.core import mail
        t = crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4, descontar_insumos=False,
                        fecha_produccion=self.hoy - timedelta(days=30))
        t.fecha_vencimiento = self.hoy + timedelta(days=3)
        t.save()
        self.client.force_login(self.prod)
        r = self.client.get(reverse('panel:tandas'))
        self.assertContains(r, 'Vence en 3 días')
        self.assertEqual(len(r.context['alertas']['vencen']), 1)
        self.assertEqual(len(mail.outbox), 0)                     # solo aviso dentro de la plataforma
        # cambia la fecha a ayer -> vencida -> se dan de baja las piezas libres
        self.client.post(reverse('panel:tanda_vencimiento', args=[t.pk]),
                         {'fecha': (self.hoy - timedelta(days=1)).isoformat()})
        t.refresh_from_db()
        self.assertTrue(t.vencida)
        self.client.post(reverse('panel:tanda_baja_vencidas', args=[t.pk]))
        self.assertEqual(t.piezas.filter(estado=Pieza.BAJA).count(), 4)
        t.refresh_from_db()
        self.assertEqual(t.estado, Tanda.AGOTADA)
        self.assertEqual(len(self.client.get(reverse('panel:tandas')).context['alertas']['vencen']), 0)

    def test_cargar_tanda_con_dias_de_vencimiento(self):
        self.client.force_login(self.prod)
        r = self.client.post(reverse('panel:tanda_nueva'), {
            'receta': self.salame.pk, 'fecha_produccion': self.hoy.isoformat(), 'kg_producidos': '2',
            'cantidad_piezas': '4', 'dias_estacionamiento': '0', 'dias_vencimiento': '45'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Tanda.objects.get().fecha_vencimiento, self.hoy + timedelta(days=45))

    # ---- etiquetas
    def test_etiquetas_a4_con_codigo_peso_y_receta(self):
        t = crear_tanda(receta=self.salame, kg_producidos=3, cantidad_piezas=30, descontar_insumos=False)
        Pieza.objects.filter(tanda=t, numero=1).update(peso_g=352)
        self.client.force_login(self.prod)
        self.assertContains(self.client.get(reverse('panel:etiquetas')), 'Elegí arriba las tandas')
        r = self.client.get(reverse('panel:etiquetas'), {'tanda': t.pk, 'formato': '24', 'inicio': '5'})
        self.assertEqual(r.context['n_piezas'], 30)
        hojas = r.context['hojas']
        self.assertEqual(len(hojas), 2)                          # 4 vacías + 30 = 34 -> 2 hojas de 24
        self.assertEqual(hojas[0][:4], [None] * 4)
        self.assertEqual(hojas[0][4].codigo, f'{t.codigo}0001')
        html = r.content.decode()
        self.assertIn('352 g', html)
        self.assertIn('Salame Criollo', html)
        self.assertIn(t.codigo[:8], html)
        # solo las libres
        self.vender(2)
        r = self.client.get(reverse('panel:etiquetas'), {'tanda': t.pk, 'piezas': 'libres'})
        self.assertEqual(r.context['n_piezas'], 28)

    # ---- trazabilidad
    def test_trazabilidad_desde_el_lote_hasta_el_cliente(self):
        t = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)   # usa el lote viejo de cerdo
        self.vender(3)
        self.client.force_login(self.prod)
        r = self.client.get(reverse('panel:traza_lote', args=[self.lote_viejo.pk]))
        self.assertEqual([x.pk for x in r.context['tandas']], [t.pk])
        self.assertEqual(len(r.context['personas']), 1)
        self.assertContains(r, 'lucia@test.com')
        # hacia atrás: desde el código de una pieza vendida
        vendida = Pieza.objects.filter(estado=Pieza.VENDIDA).first()
        r = self.client.get(reverse('panel:traza'), {'q': vendida.codigo}, follow=True)
        self.assertEqual(r.context['tanda'], t)
        self.assertEqual(r.context['pieza'], vendida)
        self.assertContains(r, 'Frigorífico Oberá')
        self.assertContains(self.client.get(reverse('panel:traza'), {'q': 'Frigor'}), 'Ver camino')

    # ---- reportes
    def test_reportes_y_exportar_a_excel(self):
        t = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)   # 20 piezas de 500 g
        pedido = self.vender(4)                                                     # 2 kg x 12000
        Pieza.objects.filter(tanda=t, numero=20).update(estado=Pieza.BAJA)
        self.client.force_login(self.prod)
        r = self.client.get(reverse('panel:reportes'))
        tot = r.context['tot']
        self.assertEqual(tot['ventas'], pedido.total)
        self.assertEqual(tot['ventas'], Decimal('24000'))
        self.assertEqual(tot['kg_prod'], Decimal('10'))
        self.assertEqual(tot['kg_vend'], Decimal('2'))
        self.assertEqual(tot['kg_baja'], Decimal('0.5'))
        costo_kg = t.costo_insumos / 10
        self.assertEqual(tot['costo_vend'], (costo_kg * 2).quantize(Decimal('1')))
        self.assertEqual(tot['ganancia'], tot['ventas'] - tot['costo_vend'])
        for seccion in ['completo', 'recetas', 'meses', 'insumos', 'clientes']:
            r = self.client.get(reverse('panel:reportes'), {'pdf': seccion})
            self.assertEqual(r['Content-Type'], 'application/pdf')
            self.assertTrue(r.content.startswith(b'%PDF'))
            self.assertIn(f'iurko-{seccion}-', r['Content-Disposition'])
        self.assertNotContains(self.client.get(reverse('panel:reportes')), 'exportar=')     # ya no hay Excel
        self.assertEqual(self.client.get(reverse('panel:reportes'), {'periodo': '12m'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('panel:reportes'), {'desde': 'x', 'hasta': 'y'}).status_code, 200)



class BajasPausaWhatsappTests(Base):
    def setUp(self):
        super().setUp()
        self.prod = User.objects.create_user('prod', 'p@t.com', 'Clave-segura-123', is_staff=True)
        self.client.force_login(self.prod)

    def vender(self, n):
        ItemCarrito.objects.update_or_create(user=self.cliente, receta=self.salame, defaults={'cantidad': n})
        return confirmar_pago(crear_pedido_con_qr(self.cliente))

    def test_numeros_de_whatsapp(self):
        from core.whatsapp import enlace, numero_wa
        for tel in ['3755 412345', '03755-15-412345', '+54 9 3755 412345', '(3755) 15412345']:
            self.assertEqual(numero_wa(tel), '5493755412345', tel)
        self.assertEqual(numero_wa('011 15 1234 5678'), '5491112345678')
        self.assertEqual(numero_wa('12345'), '')
        self.assertTrue(enlace('3755 412345', 'Hola').endswith('?text=Hola'))

    def test_whatsapp_clickeable_en_pedidos(self):
        from tienda.models import Cliente
        Cliente.objects.update_or_create(user=self.cliente, defaults={'telefono': '3755 15 412345'})
        crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10, descontar_insumos=False)
        pedido = self.vender(1)
        html = self.client.get(reverse('panel:pedidos')).content.decode()
        self.assertIn('href="https://wa.me/5493755412345?text=', html)
        self.assertIn(f'pedido%20%23{pedido.numero}', html)
        self.assertIn('https://wa.me/5493755412345', self.client.get(reverse('panel:pedido', args=[pedido.pk])).content.decode())

    def test_pausar_y_reanudar_todas_las_tandas_de_un_lote(self):
        t1 = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)     # usan el lote viejo de cerdo
        t2 = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)
        otra = crear_tanda(receta=self.salame, kg_producidos=2, cantidad_piezas=4, descontar_insumos=False)
        ItemCarrito.objects.create(user=self.cliente, receta=self.salame, cantidad=2)
        pendiente = crear_pedido_con_qr(self.cliente)                                   # QR en curso
        url = reverse('panel:traza_lote_pausar', args=[self.lote_viejo.pk])
        self.assertContains(self.client.get(reverse('panel:traza_lote', args=[self.lote_viejo.pk])), 'Pausar 2 tandas')
        self.client.post(url, {'motivo': ''})
        self.assertEqual(Tanda.objects.get(pk=t1.pk).estado, Tanda.ACTIVA)              # sin motivo no pausa
        self.client.post(url, {'motivo': 'El frigorífico avisó un problema'})
        for t in (t1, t2):
            t.refresh_from_db()
            self.assertEqual(t.estado, Tanda.PAUSADA)
            self.assertEqual(t.motivo_pausa, 'El frigorífico avisó un problema')
        otra.refresh_from_db()
        self.assertEqual(otra.estado, Tanda.ACTIVA)                                     # no usó ese lote
        pendiente.refresh_from_db()
        self.assertNotEqual(pendiente.estado, Pedido.PENDIENTE_PAGO)                    # se canceló el QR
        self.client.post(url, {'accion': 'reanudar'})
        self.assertEqual(Tanda.objects.filter(pk__in=[t1.pk, t2.pk], estado=Tanda.ACTIVA).count(), 2)

    def test_eliminar_tanda_devuelve_insumos(self):
        antes = LoteMateriaPrima.objects.get(pk=self.lote_viejo.pk).cantidad_disponible
        t = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)
        self.assertLess(LoteMateriaPrima.objects.get(pk=self.lote_viejo.pk).cantidad_disponible, antes)
        r = self.client.post(reverse('panel:tanda_eliminar', args=[t.pk]), {'confirmar': 'mal'})
        self.assertTrue(Tanda.objects.filter(pk=t.pk).exists())                         # pide el código
        r = self.client.post(reverse('panel:tanda_eliminar', args=[t.pk]), {'confirmar': t.codigo})
        self.assertRedirects(r, reverse('panel:tandas'))
        self.assertFalse(Tanda.objects.filter(pk=t.pk).exists())
        self.assertFalse(Pieza.objects.exists())
        self.assertEqual(LoteMateriaPrima.objects.get(pk=self.lote_viejo.pk).cantidad_disponible, antes)

    def test_no_se_elimina_una_tanda_con_ventas(self):
        t = crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10, descontar_insumos=False)
        self.vender(1)
        self.assertNotContains(self.client.get(reverse('panel:tanda', args=[t.pk])), 'Eliminar para siempre')
        self.client.post(reverse('panel:tanda_eliminar', args=[t.pk]), {'confirmar': t.codigo})
        self.assertTrue(Tanda.objects.filter(pk=t.pk).exists())
        # dar de baja (anular) sí se puede
        self.client.post(reverse('panel:tanda_anular', args=[t.pk]), {'motivo': 'Se cayó la cámara'})
        t.refresh_from_db()
        self.assertEqual(t.estado, Tanda.ANULADA)
        self.assertEqual(t.piezas.filter(estado=Pieza.BAJA).count(), 9)

    def test_baja_reactivar_y_eliminar_piezas(self):
        t = crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10, descontar_insumos=False)
        url = reverse('panel:tanda_piezas', args=[t.pk])
        ids = list(t.piezas.order_by('numero').values_list('pk', flat=True))
        self.client.post(url, {'accion': 'baja', 'pieza': ids[:3], 'motivo_baja': ''})
        self.assertEqual(t.piezas.filter(estado=Pieza.BAJA).count(), 0)                 # falta el motivo
        self.client.post(url, {'accion': 'baja', 'pieza': ids[:3], 'motivo_baja': 'otro', 'motivo_otro': 'Se cortó la tripa'})
        self.assertEqual(set(t.piezas.filter(estado=Pieza.BAJA).values_list('motivo_baja', flat=True)),
                         {'Se cortó la tripa'})
        self.client.post(url, {'accion': 'reactivar', 'pieza': [ids[0]]})
        self.assertEqual(t.piezas.filter(estado=Pieza.BAJA).count(), 2)
        self.client.post(url, {'accion': 'eliminar', 'pieza': ids[8:]})
        t.refresh_from_db()
        self.assertEqual(t.cantidad_piezas, 8)
        self.assertEqual(t.piezas.count(), 8)
        # una pieza vendida no se puede eliminar
        self.vender(1)
        vendida = t.piezas.get(estado=Pieza.VENDIDA)
        self.client.post(url, {'accion': 'eliminar', 'pieza': [vendida.pk]})
        self.assertTrue(Pieza.objects.filter(pk=vendida.pk).exists())

    def test_pdf_de_trazabilidad(self):
        t = crear_tanda(receta=self.salame, kg_producidos=10, cantidad_piezas=20)
        self.vender(2)
        vendida = Pieza.objects.filter(estado=Pieza.VENDIDA).first()
        for url in [reverse('panel:traza_lote', args=[self.lote_viejo.pk]) + '?pdf=1',
                    reverse('panel:traza_tanda', args=[t.pk]) + f'?pdf=1&pieza={vendida.codigo}']:
            r = self.client.get(url)
            self.assertEqual(r['Content-Type'], 'application/pdf')
            self.assertTrue(r.content.startswith(b'%PDF'))


class VentaFueraDeLaWebTests(Base):
    """Piezas vendidas por WhatsApp, en el local o en ferias: se registran a mano en la tanda."""

    def setUp(self):
        super().setUp()
        self.prod = User.objects.create_user('prod2', 'p2@t.com', 'Clave-segura-123', is_staff=True)
        self.client.force_login(self.prod)
        self.t = crear_tanda(receta=self.salame, kg_producidos=5, cantidad_piezas=10)
        self.piezas = list(self.t.piezas.order_by('numero'))
        self.url = reverse('panel:tanda_piezas', args=[self.t.pk])

    def test_registrar_venta_externa_con_precio_de_lista(self):
        ids = [self.piezas[0].pk, self.piezas[1].pk]
        self.client.post(self.url, {'accion': 'vender', 'pieza': ids, 'venta_canal': ''})
        self.assertFalse(Pieza.objects.filter(pk__in=ids, estado=Pieza.VENDIDA).exists())   # sin canal no registra
        r = self.client.post(self.url, {'accion': 'vender', 'pieza': ids, 'venta_canal': 'WhatsApp',
                                        'venta_cliente': 'Marta', 'venta_fecha': ''})
        self.assertEqual(r.status_code, 302)
        for p in Pieza.objects.filter(pk__in=ids):
            self.assertEqual(p.estado, Pieza.VENDIDA)
            self.assertTrue(p.vendida_fuera)
            self.assertEqual(p.motivo_baja, 'Venta por fuera de la web · WhatsApp · Marta')
            self.assertEqual(p.venta_externa_fecha, timezone.localdate())
            esperado = (Decimal(self.salame.precio_kg) * p.peso_g / 1000).quantize(Decimal('1'))
            self.assertEqual(p.venta_externa_monto, esperado)
        # ya no se ofrecen en la tienda
        self.assertFalse(Pieza.objects.a_la_venta().filter(pk__in=ids).exists())
        html = self.client.get(reverse('panel:tanda', args=[self.t.pk])).content.decode()
        self.assertIn('vendida por fuera', html)

    def test_total_cobrado_se_reparte_y_cuenta_en_reportes(self):
        ids = [p.pk for p in self.piezas[:3]]
        self.client.post(self.url, {'accion': 'vender', 'pieza': ids, 'venta_canal': 'Feria o evento',
                                    'venta_importe': '10.000'})
        montos = list(Pieza.objects.filter(pk__in=ids).values_list('venta_externa_monto', flat=True))
        self.assertEqual(sum(montos), Decimal('10000'))
        from panel.reportes import calcular
        hoy_ = timezone.localdate()
        datos = calcular(hoy_.replace(day=1), hoy_)
        self.assertEqual(datos['tot']['fuera_piezas'], 3)
        self.assertEqual(datos['tot']['ventas'], Decimal('10000'))
        self.assertEqual(datos['tot']['piezas_vend'], 3)
        self.assertEqual(datos['tot']['pedidos'], 0)
        r = self.client.get(reverse('panel:reportes'))
        self.assertContains(r, 'por fuera de la web')
        self.assertEqual(self.client.get(reverse('panel:reportes') + '?pdf=completo')['Content-Type'], 'application/pdf')

    def test_apartado_y_pdf_de_ventas_por_fuera(self):
        self.client.post(self.url, {'accion': 'vender', 'pieza': [p.pk for p in self.piezas[:2]],
                                    'venta_canal': 'WhatsApp', 'venta_cliente': 'Marta', 'venta_importe': '9000'})
        self.client.post(self.url, {'accion': 'vender', 'pieza': [self.piezas[2].pk], 'venta_canal': 'Feria o evento',
                                    'venta_importe': '3000'})
        from panel.reportes import calcular
        hoy_ = timezone.localdate()
        f = calcular(hoy_.replace(day=1), hoy_)['fuera']
        self.assertEqual((f['piezas'], f['total'], f['pct_ventas']), (3, Decimal('12000'), 100))
        self.assertEqual([(c['nombre'], c['piezas'], c['total']) for c in f['canales']],
                         [('WhatsApp', 2, Decimal('9000')), ('Feria o evento', 1, Decimal('3000'))])
        self.assertEqual(f['detalle'][0]['cliente'], 'Marta')
        r = self.client.get(reverse('panel:reportes'))
        self.assertContains(r, 'Ventas por fuera de la web')
        self.assertContains(r, 'pdf=fuera')
        r = self.client.get(reverse('panel:reportes') + '?pdf=fuera')
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('fuera', r['Content-Disposition'])

    def test_trazabilidad_y_volver_a_la_venta(self):
        p = self.piezas[0]
        self.client.post(self.url, {'accion': 'vender', 'pieza': [p.pk], 'venta_canal': 'En el local / en persona'})
        r = self.client.get(reverse('panel:traza_tanda', args=[self.t.pk]) + f'?pieza={p.codigo}')
        self.assertContains(r, 'Vendidas por fuera de la web')
        self.assertContains(r, 'En el local / en persona')
        self.assertEqual(self.client.get(reverse('panel:traza_tanda', args=[self.t.pk]) + '?pdf=1')['Content-Type'],
                         'application/pdf')
        self.assertContains(self.client.get(reverse('panel:traza_lote', args=[self.lote_viejo.pk])),
                            'por fuera de la web')
        # no se puede eliminar una pieza vendida, pero sí volverla a la venta si fue un error
        self.client.post(self.url, {'accion': 'eliminar', 'pieza': [p.pk]})
        self.assertTrue(Pieza.objects.filter(pk=p.pk).exists())
        self.client.post(self.url, {'accion': 'reactivar', 'pieza': [p.pk]})
        p.refresh_from_db()
        self.assertEqual((p.estado, p.motivo_baja, p.venta_externa_fecha, p.venta_externa_monto),
                         (Pieza.DISPONIBLE, '', None, None))

    def test_no_toca_piezas_vendidas_por_la_web(self):
        ItemCarrito.objects.create(user=self.cliente, receta=self.salame, cantidad=1)
        confirmar_pago(crear_pedido_con_qr(self.cliente))
        web = Pieza.objects.get(estado=Pieza.VENDIDA)
        self.client.post(self.url, {'accion': 'reactivar', 'pieza': [web.pk]})
        self.assertEqual(Pieza.objects.get(pk=web.pk).estado, Pieza.VENDIDA)            # la venta web no se deshace
        self.client.post(self.url, {'accion': 'vender', 'pieza': [web.pk], 'venta_canal': 'WhatsApp'})
        self.assertIsNone(Pieza.objects.get(pk=web.pk).venta_externa_fecha)

    def test_no_se_vende_desde_el_vencimiento(self):
        from produccion.services import vender_fuera_de_web
        hoy_ = timezone.localdate()
        Tanda.objects.filter(pk=self.t.pk).update(fecha_produccion=hoy_ - timedelta(days=20),
                                                 fecha_vencimiento=hoy_ - timedelta(days=2))
        self.t.refresh_from_db()
        with self.assertRaises(ValidationError):
            vender_fuera_de_web(self.t, [self.piezas[0].pk], 'WhatsApp', fecha=hoy_)
        self.assertEqual(vender_fuera_de_web(self.t, [self.piezas[0].pk], 'WhatsApp', fecha=hoy_ - timedelta(days=5)), 1)
