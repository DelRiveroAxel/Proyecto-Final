from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Usuario del sistema.

    AbstractUser ya trae username, password (guardada con hash), email,
    first_name, last_name, is_staff, etc. No hay que volver a declararlos.

    - Productor / administrador: is_staff = True  -> entra al panel.
    - Cliente: is_staff = False -> usa la tienda (tiene un perfil tienda.Cliente).
    """
    user_id = models.AutoField(primary_key=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    @property
    def es_productor(self):
        return self.is_staff

    def nombre_visible(self):
        return self.get_full_name() or self.username


class Role(models.Model):
    """Nivel de acceso por módulo (para cuando haya más de un empleado en el panel)."""

    PERMISSION_CHOICES = [
        (0, 'Sin acceso'),
        (1, 'Solo lectura'),
        (2, 'Crear y modificar'),
    ]

    role_name = models.CharField('Nombre del rol', max_length=50, primary_key=True)
    customers = models.IntegerField('Clientes', choices=PERMISSION_CHOICES, default=0)
    suppliers = models.IntegerField('Proveedores', choices=PERMISSION_CHOICES, default=0)
    materials = models.IntegerField('Materias primas', choices=PERMISSION_CHOICES, default=0)
    purchases = models.IntegerField('Compras', choices=PERMISSION_CHOICES, default=0)
    production = models.IntegerField('Producción', choices=PERMISSION_CHOICES, default=0)
    sales = models.IntegerField('Ventas', choices=PERMISSION_CHOICES, default=0)
    inventory = models.IntegerField('Inventario', choices=PERMISSION_CHOICES, default=0)
    reporting = models.IntegerField('Reportes', choices=PERMISSION_CHOICES, default=0)

    class Meta:
        db_table = 'roles'
        verbose_name = 'Rol'
        verbose_name_plural = 'Roles'

    def __str__(self):
        return self.role_name


class UserRole(models.Model):
    """Relación entre usuario y rol."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='roles')
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    class Meta:
        db_table = 'user_roles'
        verbose_name = 'Rol de usuario'
        verbose_name_plural = 'Roles de usuario'
        constraints = [
            models.UniqueConstraint(fields=['user', 'role'], name='uq_user_role'),
        ]

    def __str__(self):
        return f'{self.user.username} - {self.role.role_name}'
