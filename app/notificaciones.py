"""Notificaciones internas (in-app, sin push).

Los helpers no hacen commit: agregan a la sesión para viajar en la misma
transacción que la acción que dispara el evento (aprobar una observación,
cambiar el estado de un presupuesto, consumir el último repuesto crítico,
etc.). Si esa transacción se revierte, la notificación no llegó a existir.
"""

from app.models import ROLES_APRUEBAN, Notificacion, Usuario, db


def notificar_usuario(destinatario, tipo, titulo, empresa_id, enlace=None, remitente=None):
    """Notifica a un usuario puntual. No se auto-notifica: si el
    destinatario es quien disparó el evento, no tiene sentido avisarle."""
    if destinatario is None:
        return None
    if remitente is not None and destinatario.id == remitente.id:
        return None
    notif = Notificacion(
        empresa_id=empresa_id,
        destinatario_id=destinatario.id,
        remitente_id=remitente.id if remitente else None,
        tipo=tipo,
        titulo=titulo,
        enlace=enlace,
    )
    db.session.add(notif)
    return notif


def notificar_gestion(empresa_id, tipo, titulo, enlace=None, remitente=None):
    """Notifica a todos los Administrador/Jefe técnico de la empresa,
    salvo el propio remitente si es uno de ellos."""
    gestion = (
        Usuario.query.filter(
            Usuario.empresa_id == empresa_id,
            Usuario.activo.is_(True),
            Usuario.rol.in_(ROLES_APRUEBAN),
        ).all()
    )
    creadas = []
    for usuario in gestion:
        notif = notificar_usuario(usuario, tipo, titulo, empresa_id, enlace=enlace, remitente=remitente)
        if notif is not None:
            creadas.append(notif)
    return creadas
