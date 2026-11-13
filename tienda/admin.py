from django.contrib import admin

from .models import AvisoStock, Cliente, ItemCarrito, Pedido, PedidoItem, PiezaPedido, TipoCliente


class ItemInline(admin.TabularInline):
    model = PedidoItem
    extra = 0


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'cliente', 'creado', 'estado', 'total')
    list_filter = ('estado',)
    inlines = [ItemInline]


admin.site.register([TipoCliente, Cliente, ItemCarrito, PiezaPedido, AvisoStock])
