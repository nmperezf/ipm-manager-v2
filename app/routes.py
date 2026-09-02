"""Rutas de IPM Manager v2.

Estructura del CMMS: panorama, clientes e instalaciones, visitas con su
checklist, banco de deficiencias y catálogo de la empresa.
"""

from datetime import date, timedelta

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from app.checklist import armar_bloques, guardar_checklist, nombre_campo
from app.models import (
    CAMPO_ESTADO,
    CAMPO_MULTI,
    CAMPO_NUMERO,
    CAMPO_SELECCION,
    CLASIF_CRITICA,
    CLASIF_NO_CRITICA,
    ESTADO_CONFORME,
    ESTADO_NA,
    ESTADO_NO_CONFORME,
    FRECUENCIA_MENSUAL,
    FRECUENCIAS,
    GRAVEDAD_CRITICA,
    GRAVEDAD_NO_CRITICA,
    REVISION_APROBADA,
    REVISION_PENDIENTE,
    CategoriaEquipo,
    Cliente,
    Equipo,
    Instalacion,
    ItemVisita,
    Observacion,
    TipoEquipo,
    TipoFormulario,
    Usuario,
    Visita,
    db,
)

principal = Blueprint("principal", __name__)


# ---------------------------------------------------------------------------
# Contexto compartido
# ---------------------------------------------------------------------------


@principal.app_context_processor
def inyectar_contexto():
    """Las constantes de dominio viajan a las plantillas para que el HTML no
    tenga literales sueltos que se desincronicen del modelo. El contador de
    pendientes alimenta el badge del riel y de la campana."""
    contexto = {
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
        "CLASIF_NO_CRITICA": CLASIF_NO_CRITICA,
        "REVISION_APROBADA": REVISION_APROBADA,
        "FRECUENCIAS": FRECUENCIAS,
        "nombre_campo": nombre_campo,
        "pendientes_aprobacion": 0,
    }
    if current_user.is_authenticated:
        contexto["pendientes_aprobacion"] = _observaciones_empresa().filter(
            Observacion.estado_revision == REVISION_PENDIENTE
        ).count()
    return contexto


def _verificar_empresa(empresa_id):
    if empresa_id != current_user.empresa_id:
        abort(403)


def _instalaciones_empresa():
    return Instalacion.query.join(Cliente).filter(Cliente.empresa_id == current_user.empresa_id)


def _observaciones_empresa():
    return (
        Observacion.query.join(Instalacion, Observacion.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )


def _visitas_empresa():
    return (
        Visita.query.join(Instalacion, Visita.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )


def categorias_de(instalacion):
    """Categorías con al menos un equipo cargado en esa instalación. Es lo
    que decide qué se puede inspeccionar ahí."""
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
# Panorama
# ---------------------------------------------------------------------------


@principal.route("/")
@login_required
def inicio():
    obs = _observaciones_empresa()
    criticas = (
        obs.filter(Observacion.clasificacion == CLASIF_CRITICA, Observacion.resuelto.is_(False))
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    pendientes = obs.filter(Observacion.estado_revision == REVISION_PENDIENTE).count()
    abiertas = obs.filter(Observacion.resuelto.is_(False)).count()

    desde = date.today() - timedelta(days=30)
    visitas_recientes = (
        _visitas_empresa().filter(Visita.fecha >= desde)
        .order_by(Visita.fecha.desc(), Visita.id.desc()).limit(8).all()
    )
    total_visitas = _visitas_empresa().filter(Visita.fecha >= desde).count()
    instalaciones = _instalaciones_empresa().count()

    return render_template(
        "inicio.html",
        criticas=criticas[:6],
        total_criticas=len(criticas),
        pendientes=pendientes,
        abiertas=abiertas,
        visitas=visitas_recientes,
        total_visitas=total_visitas,
        instalaciones=instalaciones,
    )


# ---------------------------------------------------------------------------
# Clientes e instalaciones
# ---------------------------------------------------------------------------


@principal.route("/clientes")
@login_required
def clientes():
    lista = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre).all()
    )
    return render_template("clientes.html", clientes=lista)


@principal.route("/cliente/<int:cliente_id>")
@login_required
def cliente(cliente_id):
    obj = db.session.get(Cliente, cliente_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)

    abiertas = (
        _observaciones_empresa()
        .filter(Cliente.id == obj.id, Observacion.resuelto.is_(False))
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    return render_template("cliente.html", cliente=obj, abiertas=abiertas)


@principal.route("/instalacion/<int:instalacion_id>")
@login_required
def instalacion(instalacion_id):
    obj = db.session.get(Instalacion, instalacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.cliente.empresa_id)

    # Los equipos se listan como el técnico los recorre: los que encabezan
    # conjunto arrastran a sus hijos.
    activos = [e for e in obj.equipos if e.activo]
    raices = [e for e in activos if e.padre_id is None]
    raices.sort(key=lambda e: (e.tipo_equipo.orden, e.codigo or "", e.nombre))

    visitas_inst = (
        Visita.query.filter_by(instalacion_id=obj.id)
        .order_by(Visita.fecha.desc(), Visita.id.desc()).limit(10).all()
    )
    abiertas = (
        Observacion.query.filter_by(instalacion_id=obj.id, resuelto=False)
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    return render_template(
        "instalacion.html", instalacion=obj, raices=raices,
        visitas=visitas_inst, abiertas=abiertas,
    )


@principal.route("/instalacion/<int:instalacion_id>/inspeccionar/<int:categoria_id>", methods=["POST"])
@login_required
def abrir_inspeccion(instalacion_id, categoria_id):
    """Crea la visita y su ítem para arrancar a cargar. En el sistema
    terminado esto lo genera la coordinación mensual desde el contrato."""
    obj = db.session.get(Instalacion, instalacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.cliente.empresa_id)

    rutina = request.form.get("rutina", FRECUENCIA_MENSUAL)
    if rutina not in FRECUENCIAS:
        rutina = FRECUENCIA_MENSUAL

    nueva = Visita(instalacion_id=obj.id, fecha=date.today(), tecnico_id=current_user.id)
    db.session.add(nueva)
    db.session.flush()
    item = ItemVisita(visita_id=nueva.id, categoria_id=categoria_id, rutina=rutina)
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("principal.checklist", item_id=item.id))


# ---------------------------------------------------------------------------
# Visitas
# ---------------------------------------------------------------------------


@principal.route("/visitas")
@login_required
def visitas():
    query = _visitas_empresa()
    # Un técnico ve lo suyo; gestión ve todo.
    if current_user.rol == "Técnico":
        query = query.filter(Visita.tecnico_id == current_user.id)
    lista = query.order_by(Visita.fecha.desc(), Visita.id.desc()).limit(60).all()
    return render_template("visitas.html", visitas=lista)


@principal.route("/visita/<int:visita_id>")
@login_required
def visita(visita_id):
    obj = db.session.get(Visita, visita_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)

    observaciones = (
        Observacion.query.filter_by(visita_id=obj.id).order_by(Observacion.id).all()
    )
    return render_template("visita.html", visita=obj, observaciones=observaciones)


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
            return redirect(url_for("principal.visita", visita_id=item.visita_id))
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
    query = _observaciones_empresa()
    if cliente_id:
        query = query.filter(Cliente.id == cliente_id)

    filtro = request.args.get("estado")
    if filtro == "pendientes":
        query = query.filter(Observacion.estado_revision == REVISION_PENDIENTE)
    elif filtro == "criticas":
        query = query.filter(Observacion.clasificacion == CLASIF_CRITICA)

    observaciones = query.order_by(
        Observacion.estado_revision.desc(),
        Observacion.fecha_carga.desc(),
        Observacion.id.desc(),
    ).all()

    lista_clientes = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre).all()
    )
    return render_template(
        "deficiencias.html", observaciones=observaciones,
        clientes=lista_clientes, cliente_id=cliente_id, filtro=filtro,
    )


@principal.route("/observacion/<int:observacion_id>/aprobar", methods=["POST"])
@login_required
def aprobar(observacion_id):
    obj = db.session.get(Observacion, observacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    if not current_user.puede_aprobar:
        abort(403)

    obj.aprobar(current_user)
    db.session.commit()
    flash("Deficiencia aprobada — ya la ve el cliente.", "ok")
    return redirect(request.referrer or url_for("principal.deficiencias"))


@principal.route("/observacion/<int:observacion_id>/resolver", methods=["POST"])
@login_required
def resolver(observacion_id):
    obj = db.session.get(Observacion, observacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    if not current_user.puede_aprobar:
        abort(403)

    obj.resuelto = True
    obj.fecha_resolucion = date.today()
    db.session.commit()
    flash("Deficiencia marcada como resuelta.", "ok")
    return redirect(request.referrer or url_for("principal.deficiencias"))


# ---------------------------------------------------------------------------
# Catálogo de la empresa
# ---------------------------------------------------------------------------


@principal.route("/catalogo")
@login_required
def catalogo():
    """Lo que define qué se inspecciona. Vive a nivel empresa: se carga una
    vez y sirve para todos los clientes."""
    if not current_user.puede_aprobar:
        abort(403)

    categorias = (
        CategoriaEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(CategoriaEquipo.orden).all()
    )
    tipos_equipo = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
    )
    formularios = (
        TipoFormulario.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoFormulario.categoria_id, TipoFormulario.orden).all()
    )
    return render_template(
        "catalogo.html", categorias=categorias,
        tipos_equipo=tipos_equipo, formularios=formularios,
    )


@principal.route("/cuenta")
@login_required
def cuenta():
    return render_template("cuenta.html")


# ---------------------------------------------------------------------------
# Búsqueda global
# ---------------------------------------------------------------------------


@principal.route("/buscar")
@login_required
def buscar():
    q = (request.args.get("q") or "").strip()
    resultados = {"clientes": [], "instalaciones": [], "equipos": [], "visitas": []}

    if len(q) >= 2:
        patron = f"%{q}%"
        empresa = current_user.empresa_id

        resultados["clientes"] = (
            Cliente.query.filter(Cliente.empresa_id == empresa, Cliente.nombre.ilike(patron))
            .order_by(Cliente.nombre).limit(10).all()
        )
        resultados["instalaciones"] = (
            _instalaciones_empresa().filter(Instalacion.nombre.ilike(patron))
            .order_by(Instalacion.nombre).limit(10).all()
        )
        resultados["equipos"] = (
            Equipo.query.join(Instalacion).join(Cliente)
            .filter(
                Cliente.empresa_id == empresa,
                or_(Equipo.nombre.ilike(patron), Equipo.codigo.ilike(patron)),
            )
            .order_by(Equipo.codigo).limit(15).all()
        )
        # Buscar una visita por su número es lo más común en campo.
        crudo = q.lstrip("#")
        if crudo.isdigit():
            encontrada = _visitas_empresa().filter(Visita.id == int(crudo)).first()
            if encontrada:
                resultados["visitas"] = [encontrada]

    total = sum(len(v) for v in resultados.values())
    return render_template("buscar.html", q=q, resultados=resultados, total=total)
