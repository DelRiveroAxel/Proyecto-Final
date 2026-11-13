from django.urls import path

from . import views

app_name = 'tienda'

urlpatterns = [
    path('', views.catalogo, name='catalogo'),
    path('producto/<slug:slug>/', views.producto, name='producto'),
    path('producto/<int:pk>/avisame/', views.avisame, name='avisame'),
    path('producto/<int:pk>/avisame/quitar/', views.avisame_quitar, name='avisame_quitar'),

    path('carrito/', views.carrito, name='carrito'),
    path('carrito/agregar/', views.carrito_agregar, name='carrito_agregar'),
    path('carrito/<int:pk>/actualizar/', views.carrito_actualizar, name='carrito_actualizar'),
    path('carrito/<int:pk>/quitar/', views.carrito_quitar, name='carrito_quitar'),

    path('checkout/', views.checkout, name='checkout'),
    path('pago/<int:pk>/', views.pago, name='pago'),
    path('pago/<int:pk>/estado/', views.pago_estado, name='pago_estado'),
    path('pago/<int:pk>/simular/', views.simular_pago, name='simular_pago'),
    path('pago/<int:pk>/cancelar/', views.cancelar_pago, name='cancelar_pago'),
    path('mp/webhook/', views.webhook_mp, name='webhook_mp'),

    path('mis-pedidos/', views.mis_pedidos, name='mis_pedidos'),
    path('mis-pedidos/<int:pk>/', views.pedido_detalle, name='pedido'),
    path('mis-pedidos/<int:pk>/comprobante/', views.pedido_comprobante, name='pedido_comprobante'),
    path('ayuda/', views.ayuda, name='ayuda'),
    path('nosotros/', views.nosotros, name='nosotros'),
]
