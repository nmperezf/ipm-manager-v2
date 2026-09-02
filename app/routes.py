"""Rutas de IPM Manager v2.

Lo mínimo para ejercitar el motor de checklist y el banco de deficiencias:
login, elegir instalación, cargar el checklist de una categoría, y
revisar/aprobar lo que quedó pendiente.
"""

from datetime import date

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)
from flask_login import current_user, login_required, login_user, logout_user

from app.checklist import armar_bloques, guardar_checklist, nombre_campo
from app.models import (
    CAMPO_ESTADO,
    CAMPO_MULTI,
    CAMPO_NUMERO,
    CAMPO_SELECCION,
    CLASIF_CRITICA,
    FRECUENCIA_MENSUAL,
    FRECUENCIAS,
    ESTADO_CONFORME,
    ESTADO_NA,
    ESTADO_NO_CONFORME,
    GRAVEDAD_CRITICA,
    GRAVEDAD_NO_CRITICA,
    REVISION_APROBADA,
    REVISION_PENDIENTE,
    CategoriaEquipo,
    Cliente,
    Instalacion,
    ItemVisita,
    Observacion,
    Usuario,
    Visita,
    db,
)

principal = Blueprint("principal", __name__)


@principal.app_context_processor
def inyectar_constantes():
    """Las constantes de dominio viajan a las plantillas para que el HTML
    no tenga literales sueltos que se desincronicen del modelo."""
    return {
        "CAMPO_NUMERO": CAMPO_NUMERO,
        "CAMPO_ESTADO": CAMPO_ESTADO,
        "CAMPO_SELECCION": CAMPO_SELECCION,
        "CAMPO_MULTI": CAMPO_MULTI,
        "ESTADO_CONFORME": ESTADO_CONFORME,
        "ESTADO_NO_CONFORME": ESTADO_NO_CONFORME,
        "ESTADO_NA": ESTADO_NA,
        "GRAVEDAD_CRITICA": GRAVEDAD_CRITICA,
        "GRAVEDAD_NO_CRITICA": GRAVEDAD_NO_CRITICA,
        "CLASIF_CRITICA": CLASIF_CRITICA,
        "REVISION_APROBADA": REVISION_APROBADA,
        "nombre_campo": nombre_campo,
    }


def _verificar_empresa(empresa_id):
    if empresa_id != current_user.empresa_id:
        abort(403)


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------


@principal.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("principal.inicio"))
    if request.method == "POST":
        usuario = Usuario.query.filter_by(
            username=request.form.get("username", "").strip()
        ).first()
        if usuario and usuario.activo and usuario.check_password(request.form.get("password", "")):
            login_user(usuario)
            return redirect(url_for("principal.inicio"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@principal.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("principal.login"))


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------


@principal.route("/")
@login_required
def inicio():
    instalaciones = (
        Instalacion.query.join(Cliente)
        .filter(Cliente.empresa_id == current_user.empresa_id)
        .order_by(Cliente.nombre)
        .all()
    )
    pendientes = (
        Observacion.query.join(Instalacion).join(Cliente)
        .filter(
            Cliente.empresa_id == current_user.empresa_id,
            Observacion.estado_revision == REVISION_PENDIENTE,
        )
        .count()
    )
    return render_template("inicio.html", instalaciones=instalaciones, pendientes=pendientes)


@principal.route("/instalacion/<int:instalacion_id>/inspeccionar/<int:categoria_id>", methods=["POST"])
@login_required
def abrir_inspeccion(instalacion_id, categoria_id):
    """Crea la visita y su ítem para arrancar a cargar. En el sistema real
    esto lo genera la coordinación mensual a partir del contrato."""
    instalacion = db.session.get(Instalacion, instalacion_id)
    if instalacion is None:
        abort(404)
    _verificar_empresa(instalacion.cliente.empresa_id)

    rutina = request.form.get("rutina", FRECUENCIA_MENSUAL)
    if rutina not in FRECUENCIAS:
        rutina = FRECUENCIA_MENSUAL

    visita = Visita(instalacion_id=instalacion.id, fecha=date.today(), tecnico_id=current_user.id)
    db.session.add(visita)
    db.session.flush()
    item = ItemVisita(visita_id=visita.id, categoria_id=categoria_id, rutina=rutina)
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("principal.checklist", item_id=item.id))


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


@principal.route("/checklist/<int:item_id>", methods=["GET", "POST"])
@login_required
def checklist(item_id):
    item = db.session.get(ItemVisita, item_id)
    if item is None:
        abort(404)
    _verificar_empresa(item.visita.instalacion.cliente.empresa_id)

    if request.method == "POST":
        resultado = guardar_checklist(item, request.form, current_user)
        if resultado.ok:
            partes = [f"{resultado.respuestas} punto(s) guardado(s)"]
            if resultado.observaciones:
                estado = "aprobada(s)" if current_user.puede_aprobar else "pendiente(s) de aprobación"
                partes.append(f"{resultado.observaciones} deficiencia(s) {estado}")
            flash(" · ".join(partes), "ok")
            return redirect(url_for("principal.checklist", item_id=item.id))
        for error in resultado.errores:
            flash(error, "error")

    bloques = armar_bloques(item)
    return render_template("checklist.html", item=item, bloques=bloques)


# ---------------------------------------------------------------------------
# Banco de deficiencias
# ---------------------------------------------------------------------------


@principal.route("/deficiencias")
@principal.route("/deficiencias/<int:cliente_id>")
@login_required
def deficiencias(cliente_id=None):
    query = (
        Observacion.query.join(Instalacion).join(Cliente)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )
    if cliente_id:
        query = query.filter(Cliente.id == cliente_id)
    observaciones = query.order_by(
        Observacion.estado_revision.desc(),
        Observacion.fecha_carga.desc(),
        Observacion.id.desc(),
    ).all()

    clientes = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre)
        .all()
    )
    return render_template(
        "deficiencias.html",
        observaciones=observaciones,
        clientes=clientes,
        cliente_id=cliente_id,
    )


@principal.route("/observacion/<int:observacion_id>/aprobar", methods=["POST"])
@login_required
def aprobar(observacion_id):
    observacion = db.session.get(Observacion, observacion_id)
    if observacion is None:
        abort(404)
    _verificar_empresa(observacion.instalacion.cliente.empresa_id)

    if not current_user.puede_aprobar:
        abort(403)

    observacion.aprobar(current_user)
    db.session.commit()
    flash("Deficiencia aprobada — ya la ve el cliente.", "ok")
    return redirect(request.referrer or url_for("principal.deficiencias"))


# ---------------------------------------------------------------------------
# Helpers de plantilla
# ---------------------------------------------------------------------------


def categorias_de(instalacion):
    """Categorías con al menos un equipo cargado en esa instalación."""
    ids = {
        e.tipo_equipo.categoria_id
        for e in instalacion.equipos
        if e.activo and e.tipo_equipo and e.tipo_equipo.categoria_id
    }
    if not ids:
        return []
    return (
        CategoriaEquipo.query.filter(CategoriaEquipo.id.in_(ids))
        .order_by(CategoriaEquipo.orden)
        .all()
    )


principal.add_app_template_global(categorias_de, "categorias_de")
