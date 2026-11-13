from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),          # admin de Django (soporte técnico)
    path('users/', include('users.urls')),
    path('panel/', include('panel.urls')),    # panel del productor
    path('', include('tienda.urls')),         # tienda para clientes
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
