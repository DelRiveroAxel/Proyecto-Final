"""
Configuración del proyecto ERP - Iurko Chacinados (Django 6.1).

Los datos sensibles (clave secreta, token de Mercado Pago) se leen de variables
de entorno o de un archivo .env en la raíz del proyecto (ver ejemplo_env.txt).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _cargar_env(ruta):
    """Lee un .env simple (CLAVE=valor) sin dependencias externas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        linea = linea.strip()
        if not linea or linea.startswith('#') or '=' not in linea:
            continue
        clave, valor = linea.split('=', 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(BASE_DIR / '.env')

# La clave de desarrollo solo sirve en tu máquina: en producción definí DJANGO_SECRET_KEY.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-solo-para-desarrollo-cambiar-en-produccion',
)

DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

ALLOWED_HOSTS = [h for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o]


# Aplicaciones
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'users',
    'produccion',   # materias primas, recetas, tandas y piezas (FIFO)
    'tienda',       # clientes, carrito, pedidos, pre-compras y pagos
    'panel',        # pantallas del productor
]

AUTH_USER_MODEL = 'users.User'

LOGIN_URL = 'login'                       # si no estás logueado te manda al login
LOGIN_REDIRECT_URL = 'inicio_segun_rol'   # productor -> panel, cliente -> tienda
LOGOUT_REDIRECT_URL = 'tienda:catalogo'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ERP.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tienda.context_processors.carrito',
                'core.context_processors.marca',
                'panel.context_processors.alertas',
            ],
        },
    },
]

WSGI_APPLICATION = 'ERP.wsgi.application'


# Base de datos (SQLite en desarrollo)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Idioma y zona horaria
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'   # Misiones usa la hora de Buenos Aires
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True


# Archivos estáticos (CSS, JS) y subidos (fotos, tickets/facturas)
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Los comprobantes de compra NO se sirven por /media/: van a una carpeta privada
# y solo los abre el productor desde el panel.
PRIVATE_MEDIA_ROOT = BASE_DIR / 'privado'

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Email
# Sin datos en el .env, los correos (recuperar contraseña, avisos de stock, contacto) se IMPRIMEN
# EN LA CONSOLA donde corre el servidor. Para mandarlos de verdad con Gmail, en el .env:
#   EMAIL_USER=tucorreo@gmail.com
#   EMAIL_PASSWORD=contraseña de aplicación de 16 letras (Cuenta de Google > Seguridad >
#                  Verificación en 2 pasos > Contraseñas de aplicaciones)
# Opcionales: EMAIL_HOST (smtp.gmail.com) y EMAIL_PORT (587).
# Django 6.1 usa MAILERS; Django 5.2 (Python 3.10/3.11) usa EMAIL_*. No se pueden poner los dos.
import django  # noqa: E402

EMAIL_USER = os.environ.get('EMAIL_USER', '')
EMAIL_REAL = bool(EMAIL_USER and os.environ.get('EMAIL_PASSWORD'))
DEFAULT_FROM_EMAIL = (f'Iurko Chacinados <{EMAIL_USER}>' if EMAIL_USER
                      else 'Iurko Chacinados <no-responder@iurko.local>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
_SMTP = {
    'host': os.environ.get('EMAIL_HOST', 'smtp.gmail.com'),
    'port': int(os.environ.get('EMAIL_PORT', '587')),
    'username': EMAIL_USER,
    'password': os.environ.get('EMAIL_PASSWORD', ''),
    'use_tls': True,
    'timeout': 15,
}
_BACKEND = ('django.core.mail.backends.smtp.EmailBackend' if EMAIL_REAL
            else 'django.core.mail.backends.console.EmailBackend')

if django.VERSION >= (6, 1):
    MAILERS = {'default': {'BACKEND': _BACKEND, 'OPTIONS': _SMTP if EMAIL_REAL else {}}}
else:
    EMAIL_BACKEND = _BACKEND
    if EMAIL_REAL:
        EMAIL_HOST, EMAIL_PORT = _SMTP['host'], _SMTP['port']
        EMAIL_HOST_USER, EMAIL_HOST_PASSWORD = _SMTP['username'], _SMTP['password']
        EMAIL_USE_TLS, EMAIL_TIMEOUT = True, 15

PASSWORD_RESET_TIMEOUT = 60 * 60 * 3   # el enlace para cambiar la contraseña vale 3 horas


# Mercado Pago - cobro con QR (API de Orders)
# Si MP_ACCESS_TOKEN está vacío, el sistema funciona en MODO DEMO: genera un QR de
# prueba y muestra un botón "Simular pago aprobado" para probar todo el circuito.
MP_ACCESS_TOKEN = os.environ.get('MP_ACCESS_TOKEN', '')
MP_EXTERNAL_POS_ID = os.environ.get('MP_EXTERNAL_POS_ID', 'IURKO_WEB_01')  # caja creada en Mercado Pago
MP_WEBHOOK_SECRET = os.environ.get('MP_WEBHOOK_SECRET', '')                  # clave secreta de Webhooks
MP_QR_MINUTOS = int(os.environ.get('MP_QR_MINUTOS', '15'))
MP_MODO_DEMO = not MP_ACCESS_TOKEN
