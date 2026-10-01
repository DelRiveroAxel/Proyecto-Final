from django.shortcuts import render
from django.contrib.auth.decorators import login_required #importo el decorador login_required para proteger las vistas que requieren autenticacion 
from users.models import UserRole, Role #importo los modelos de usuario y rol que cree en models.py

@login_required
def dashboard_view(request): #creo la vista para el dashboard
    
    user_role = UserRole.objects.filter(user=request.user).first() #obtengo el rol del usuario logueado
    role_name = user_role.role.name if user_role else None #obtengo el nombre del rol del usuario logueado

    #defino los permisos para cada rol
    permissions = {
        'customers': 0,
        'supliers': 0,
        'materials': 0,
        'purchases': 0,
        'sales': 0,
        'inventory': 0,
        'accounting': 0,
        'reporting': 0,
    }

    for user_role in user_role:
        role=user_role.role
        for module in permissions.keys():
            current_permission = getattr(role, module)
            if current_permission > permissions[module]:
                permissions[module] = current_permission

    context = {
        'user' : request.user,
        'permissions' : permissions,
        'role' : [url.role.role_name for url in user_role],
    }

    return render(request, 'core/dashboard.html', context) #renderizo la vista del dashboard con el contexto definido

