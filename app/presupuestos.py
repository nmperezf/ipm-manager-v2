"""Creación de presupuestos.

El cambio de estado (incluida la creación automática de la OT correctiva al
aprobar) vive como método en el propio modelo (`Presupuesto.cambiar_estado`,
en app/models.py) porque es lógica de transición de estado, igual que
`OrdenTrabajo.puede_pasar_a`. Acá solo vive la creación inicial, que sí
necesita generar el código correlativo.
"""

from datetime import date

from app.models import Presupuesto, db


def crear_presupuesto(observacion, usuario):
    """Código correlativo `PRESUP-{año}-{nnnn}` por empresa y año, con
    reintento ante un choque del índice único (dos técnicos cargando una
    deficiencia con presupuesto al mismo tiempo). No hace commit: viaja en
    la misma transacción que la creación de la Observación."""
    empresa_id = usuario.empresa_id
    anio = date.today().year
    for _ in range(3):
        cuantos = Presupuesto.query.filter(
            Presupuesto.empresa_id == empresa_id,
            Presupuesto.codigo.like(f"PRESUP-{anio}-%"),
        ).count()
        codigo = f"PRESUP-{anio}-{cuantos + 1:04d}"
        if not Presupuesto.query.filter_by(codigo=codigo).first():
            break
    presupuesto = Presupuesto(
        empresa_id=empresa_id,
        observacion_id=observacion.id,
        codigo=codigo,
        creado_por_id=usuario.id if usuario else None,
    )
    db.session.add(presupuesto)
    return presupuesto
