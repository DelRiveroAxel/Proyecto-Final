from django.contrib import admin

from .models import (CompraMateriaPrima, ConsumoMateriaPrima, IngredienteReceta, LoteMateriaPrima, Pieza,
                     Proveedor, Receta, Tanda, TipoMateriaPrima, UnidadMedida)


class LoteInline(admin.TabularInline):
    model = LoteMateriaPrima
    extra = 0


@admin.register(CompraMateriaPrima)
class CompraAdmin(admin.ModelAdmin):
    list_display = ('id', 'fecha', 'proveedor', 'tipo_comprobante', 'numero_comprobante')
    inlines = [LoteInline]


class IngredienteInline(admin.TabularInline):
    model = IngredienteReceta
    extra = 0


@admin.register(Receta)
class RecetaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'precio_kg', 'peso_nominal_g', 'publicada')
    inlines = [IngredienteInline]


@admin.register(Tanda)
class TandaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'receta', 'fecha_produccion', 'kg_producidos', 'cantidad_piezas', 'estado')
    list_filter = ('estado', 'receta')


@admin.register(Pieza)
class PiezaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'tanda', 'peso_g', 'estado')
    list_filter = ('estado',)
    search_fields = ('codigo',)


admin.site.register([UnidadMedida, TipoMateriaPrima, Proveedor, LoteMateriaPrima, ConsumoMateriaPrima])
