from django.urls import path

from . import views

app_name = 'panel'

urlpatterns = [
    path('', views.inicio, name='inicio'),

    path('tandas/', views.tandas, name='tandas'),
    path('tandas/nueva/', views.tanda_nueva, name='tanda_nueva'),
    path('tandas/necesidad/', views.necesidad, name='necesidad'),
    path('tandas/<int:pk>/', views.tanda, name='tanda'),
    path('tandas/<int:pk>/pausar/', views.tanda_pausar, name='tanda_pausar'),
    path('tandas/<int:pk>/reanudar/', views.tanda_reanudar, name='tanda_reanudar'),
    path('tandas/<int:pk>/anular/', views.tanda_anular, name='tanda_anular'),
    path('tandas/<int:pk>/eliminar/', views.tanda_eliminar, name='tanda_eliminar'),
    path('tandas/<int:pk>/piezas/', views.tanda_piezas, name='tanda_piezas'),
    path('tandas/<int:pk>/pesos/', views.tanda_pesos, name='tanda_pesos'),
    path('tandas/<int:pk>/fecha-venta/', views.tanda_fecha_venta, name='tanda_fecha_venta'),
    path('tandas/<int:pk>/vencimiento/', views.tanda_vencimiento, name='tanda_vencimiento'),
    path('tandas/<int:pk>/baja-vencidas/', views.tanda_baja_vencidas, name='tanda_baja_vencidas'),
    path('tandas/<int:pk>/fotos/', views.tanda_fotos, name='tanda_fotos'),
    path('fotos/<str:tipo>/<int:pk>/borrar/', views.foto_borrar, name='foto_borrar'),
    path('recetas/fotos/<int:pk>/principal/', views.receta_foto_principal, name='receta_foto_principal'),
    path('recetas/<int:pk>/quitar-foto-principal/', views.receta_foto_quitar_principal, name='receta_foto_quitar_principal'),
    path('buscar/', views.buscar_pieza, name='buscar'),
    path('etiquetas/', views.etiquetas, name='etiquetas'),
    path('reportes/', views.reportes, name='reportes'),
    path('trazabilidad/', views.traza, name='traza'),
    path('trazabilidad/lote/<int:pk>/', views.traza_lote, name='traza_lote'),
    path('trazabilidad/lote/<int:pk>/pausar/', views.traza_lote_pausar, name='traza_lote_pausar'),
    path('trazabilidad/tanda/<int:pk>/', views.traza_tanda, name='traza_tanda'),

    path('materias-primas/', views.insumos, name='insumos'),
    path('materias-primas/<int:pk>/minimo/', views.insumo_minimo, name='insumo_minimo'),
    path('materias-primas/<int:pk>/unidad/', views.insumo_unidad, name='insumo_unidad'),
    path('compras/', views.compras, name='compras'),
    path('compras/nueva/', views.compra_nueva, name='compra_nueva'),
    path('compras/<int:pk>/', views.compra, name='compra'),
    path('compras/<int:pk>/comprobante/', views.comprobante, name='comprobante'),

    path('recetas/', views.recetas, name='recetas'),
    path('recetas/nueva/', views.receta_editar, name='receta_nueva'),
    path('recetas/<int:pk>/', views.receta_editar, name='receta'),

    path('pedidos/', views.pedidos, name='pedidos'),
    path('pedidos/<int:pk>/', views.pedido, name='pedido'),
    path('pedidos/<int:pk>/avanzar/', views.pedido_avanzar, name='pedido_avanzar'),
    path('pedidos/<int:pk>/cancelar/', views.pedido_cancelar, name='pedido_cancelar'),
    path('pedidos/<int:pk>/reintegrado/', views.pedido_reintegrado, name='pedido_reintegrado'),
    path('pedidos/<int:pk>/comprobante/', views.pedido_comprobante, name='pedido_comprobante'),
    path('pedidos/vistos/', views.pedidos_vistos, name='pedidos_vistos'),

    path('nosotros/', views.nosotros, name='nosotros'),
    path('preguntas/', views.preguntas, name='preguntas'),
    path('carrusel/', views.carrusel, name='carrusel'),
    path('carrusel/nueva/', views.slide_editar, name='slide_nueva'),
    path('carrusel/<int:pk>/', views.slide_editar, name='slide'),
    path('carrusel/<int:pk>/<str:accion>/', views.slide_accion, name='slide_accion'),
    path('consultas/', views.consultas, name='consultas'),
    path('consultas/<int:pk>/respondida/', views.consulta_respondida, name='consulta_respondida'),
    path('correo/prueba/', views.correo_prueba, name='correo_prueba'),
]
