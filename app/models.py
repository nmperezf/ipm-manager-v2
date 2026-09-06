"""Dominio de IPM Manager v2.

Los seis cambios de diseño que motivaron el proyecto nuevo están acá,
resueltos de raíz en vez de como parches sobre el modelo viejo:

1. TipoFormulario cuelga de Empresa, no de Cliente. NFPA 25 no cambia por
   cliente: la prueba de una bomba diesel es idéntica en todas las salas.
2. Existe el tipo de campo `seleccion` (opción única), que faltaba para
   cosas como "Modo de encendido: Test / Automático / Manual".
3. Los campos numéricos declaran min/max, y el valor fuera de rango abre
   la no conformidad solo.
4. La gravedad de la deficiencia la elige el técnico (crítica / no
   crítica), no una regla fija en el código.
5. Equipo.padre_id permite armar conjuntos reales (controlador y tanque
   colgando de su bomba principal) para recorrer el checklist por
   conjunto y no por tipo de formulario.
6. TipoFormulario apunta al TipoEquipo por clave foránea, no por nombre
   de texto: renombrar un tipo de equipo ya no rompe el checklist en
   silencio.
"""

from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------

ROL_CLIENTE = "Cliente"
ROLES = ["Administrador", "Jefe técnico", "Técnico", ROL_CLIENTE]

# Roles que pueden aprobar una observación para que la vea el cliente.
ROLES_APRUEBAN = ("Administrador", "Jefe técnico")

# Estado de un punto de checklist. Son tres, no cuatro: "cumple o no
# cumple" es un hecho, "qué tan grave es" es un juicio aparte (ver
# GRAVEDADES). Mezclarlos en un solo control es lo que impedía reportar
# una crítica desde campo.
ESTADO_CONFORME = "conforme"
ESTADO_NO_CONFORME = "no_conforme"
ESTADO_NA = "na"
ESTADOS_PUNTO = [ESTADO_CONFORME, ESTADO_NO_CONFORME, ESTADO_NA]
ESTADOS_PUNTO_LABEL = {
    ESTADO_CONFORME: "Conforme",
    ESTADO_NO_CONFORME: "No conforme",
    ESTADO_NA: "N/A",
}

# Gravedad que el técnico elige cuando marca No conforme. Se traduce
# directo a la clasificación de la Observación que se abre.
GRAVEDAD_CRITICA = "critica"
GRAVEDAD_NO_CRITICA = "no_critica"
GRAVEDADES = [GRAVEDAD_CRITICA, GRAVEDAD_NO_CRITICA]

# Clasificación de la observación en el banco de deficiencias del cliente.
# Sigue la terminología de NFPA 25: deficiencia crítica, deficiencia no
# crítica, y desactivación (impairment).
CLASIF_CRITICA = "Deficiencia crítica"
CLASIF_NO_CRITICA = "Deficiencia no crítica"
CLASIF_DESACTIVACION = "Desactivación"
CLASIF_COMENTARIO = "Comentario"
CLASIFICACIONES = [CLASIF_CRITICA, CLASIF_NO_CRITICA, CLASIF_DESACTIVACION, CLASIF_COMENTARIO]

GRAVEDAD_A_CLASIFICACION = {
    GRAVEDAD_CRITICA: CLASIF_CRITICA,
    GRAVEDAD_NO_CRITICA: CLASIF_NO_CRITICA,
}

# Una observación solo llega al cliente si está Aprobada y no es Interna.
REVISION_PENDIENTE = "Pendiente"
REVISION_APROBADA = "Aprobada"

VISIBILIDAD_CLIENTE = "Cliente"
VISIBILIDAD_INTERNA = "Interna"

# Tipos de campo de un formulario. `seleccion` (opción única) es el que
# faltaba en el modelo viejo: multi_seleccion dejaba marcar varias, que
# para "Modo de encendido" no tiene sentido.
CAMPO_NUMERO = "numero"
CAMPO_TEXTO = "texto"
CAMPO_SELECCION = "seleccion"
CAMPO_MULTI = "multi_seleccion"
CAMPO_ESTADO = "estado"
TIPOS_CAMPO = [CAMPO_NUMERO, CAMPO_TEXTO, CAMPO_SELECCION, CAMPO_MULTI, CAMPO_ESTADO]

# Rutinas de inspección. Son ACUMULATIVAS: la semestral hace todo lo de la
# mensual más lo suyo, y la anual hace las tres cosas. Por eso la
# frecuencia se declara por campo y no por formulario — si no, habría que
# mantener tres copias casi idénticas de cada checklist de estación.
FRECUENCIA_MENSUAL = "mensual"
FRECUENCIA_TRIMESTRAL = "trimestral"
FRECUENCIA_SEMESTRAL = "semestral"
FRECUENCIA_ANUAL = "anual"
FRECUENCIA_SEMANAL = "semanal"

NIVEL_FRECUENCIA = {
    FRECUENCIA_SEMANAL: 1,
    FRECUENCIA_MENSUAL: 2,
    FRECUENCIA_TRIMESTRAL: 3,
    FRECUENCIA_SEMESTRAL: 4,
    FRECUENCIA_ANUAL: 5,
}
FRECUENCIAS = sorted(NIVEL_FRECUENCIA, key=NIVEL_FRECUENCIA.get)

# Datos de placa contra los que se puede validar un campo numérico (ver
# CampoFormulario.atributo_equipo). Es lista blanca a propósito: evita que
# un valor mal tipeado en el catálogo termine leyendo un atributo del
# modelo Equipo que no es un dato numérico de placa.
ATRIBUTOS_EQUIPO = {
    "presion_diseno": "Presión de diseño (100 % del caudal)",
    "presion_maxima": "Presión máxima (churn, 0 % del caudal)",
    "presion_sobrecarga": "Presión de sobrecarga (150 % del caudal)",
    "caudal_nominal": "Caudal nominal",
    "rpm_nominal": "RPM nominal",
}

# Cada cuántos meses cae cada rutina. Es lo que hace predecible el año:
# sabiendo el mes ancla del contrato, se deriva qué toca cada mes sin
# guardar un calendario.
PERIODO_MESES = {
    FRECUENCIA_MENSUAL: 1,
    FRECUENCIA_TRIMESTRAL: 3,
    FRECUENCIA_SEMESTRAL: 6,
    FRECUENCIA_ANUAL: 12,
}


# Estados de una orden de trabajo. El técnico la empuja hasta dejarla en
# revisión; cerrarla es de gestión, igual que aprobar una deficiencia.
OT_PENDIENTE = "Pendiente"
OT_EN_CURSO = "En curso"
OT_EN_REVISION = "En revisión"
OT_CERRADA = "Cerrada"
ESTADOS_OT = [OT_PENDIENTE, OT_EN_CURSO, OT_EN_REVISION, OT_CERRADA]

OT_PREVENTIVO = "Preventivo"
OT_CORRECTIVO = "Correctivo"
TIPOS_OT = [OT_PREVENTIVO, OT_CORRECTIVO]

PRIORIDAD_ALTA = "Alta"
PRIORIDAD_MEDIA = "Media"
PRIORIDAD_BAJA = "Baja"
PRIORIDADES_OT = [PRIORIDAD_ALTA, PRIORIDAD_MEDIA, PRIORIDAD_BAJA]

# Presupuestos: no es un presupuesto económico (sin montos ni ítems), es el
# tracking de aprobación de una deficiencia que requiere cotizar un
# correctivo. Cerrado solo lo pone el sistema, al finalizar la OT que generó.
PRESUP_PENDIENTE = "Pendiente"
PRESUP_COTIZADO = "Cotizado"
PRESUP_APROBADO = "Aprobado"
PRESUP_RECHAZADO = "Rechazado"
PRESUP_CERRADO = "Cerrado"
ESTADOS_PRESUPUESTO = [PRESUP_PENDIENTE, PRESUP_COTIZADO, PRESUP_APROBADO, PRESUP_RECHAZADO, PRESUP_CERRADO]
TRANSICIONES_PRESUPUESTO = {
    PRESUP_PENDIENTE: [PRESUP_COTIZADO],
    PRESUP_COTIZADO: [PRESUP_APROBADO, PRESUP_RECHAZADO],
}

# Notificaciones internas: tipo → texto legible y clase de severidad (mapea
# a los mismos colores que ya usan chip/pill: crit=ember, alerta=warn, ok=ok).
TIPOS_NOTIFICACION = {
    "presupuesto": "Cambio de estado en un presupuesto",
    "stock_critico": "Repuesto en nivel crítico",
    "ot_asignada": "Orden de trabajo asignada",
    "observacion_pendiente": "Deficiencia pendiente de aprobación",
    "observacion_aprobada": "Deficiencia aprobada",
    "coordinacion": "Coordinación de visita",
}
SEVERIDAD_NOTIFICACION = {
    "presupuesto": "info",
    "stock_critico": "alerta",
    "ot_asignada": "info",
    "observacion_pendiente": "alerta",
    "observacion_aprobada": "ok",
    "coordinacion": "info",
}


def nivel_frecuencia(frecuencia):
    """Ordinal de una frecuencia. Una rutina incluye todo lo de nivel menor
    o igual. Lo desconocido queda en el nivel más bajo para que aparezca
    siempre en vez de desaparecer sin aviso."""
    return NIVEL_FRECUENCIA.get(frecuencia, 1)


# ---------------------------------------------------------------------------
# Empresa y usuarios
# ---------------------------------------------------------------------------


class Empresa(db.Model):
    __tablename__ = "empresas"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    rut = db.Column(db.String(40))
    # Va al encabezado de los PDF en vez del logo genérico.
    logo = db.Column(db.String(300))

    def __repr__(self):
        return f"<Empresa {self.nombre}>"


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    nombre_completo = db.Column(db.String(150))
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(30), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    # A qué cliente pertenece, si es un usuario del lado del cliente. Sin
    # esto no hay forma de responder "lo suyo": el contacto de un cliente
    # no puede ver las instalaciones de otro.
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True, index=True)

    empresa = db.relationship("Empresa", backref="usuarios")
    cliente = db.relationship("Cliente", backref="usuarios")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        """Flask-Login lo consulta al iniciar sesión. Se mapea a `activo`
        para que dar de baja a un usuario le impida entrar."""
        return self.activo

    @property
    def es_cliente(self):
        """Usuario del lado del cliente. No ve la operación interna: ni
        otros clientes, ni el catálogo, ni lo que todavía no se aprobó."""
        return self.rol == ROL_CLIENTE

    @property
    def puede_aprobar(self):
        """Un jefe técnico (o administrador) aprueba observaciones para que
        las vea el cliente. De acá sale también la auto-aprobación: si la
        visita la hizo alguien que puede aprobar, no tiene sentido que se
        apruebe a sí mismo."""
        return self.rol in ROLES_APRUEBAN

    def __repr__(self):
        return f"<Usuario {self.username} ({self.rol})>"


# ---------------------------------------------------------------------------
# Clientes e instalaciones
# ---------------------------------------------------------------------------


class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    nombre = db.Column(db.String(150), nullable=False)
    rut = db.Column(db.String(40))
    contacto = db.Column(db.String(150))
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(150))
    direccion = db.Column(db.String(250))
    activo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa", backref="clientes")

    def __repr__(self):
        return f"<Cliente {self.nombre}>"


class Instalacion(db.Model):
    __tablename__ = "instalaciones"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True)
    nombre = db.Column(db.String(150), nullable=False)
    direccion = db.Column(db.String(250))

    cliente = db.relationship("Cliente", backref=db.backref("instalaciones", cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<Instalacion {self.nombre}>"


# ---------------------------------------------------------------------------
# Catálogo de equipos — lo que NFPA 20 describe y varía por instalación
# ---------------------------------------------------------------------------


class CategoriaEquipo(db.Model):
    """Agrupa tipos de equipo en un paquete de inspección (ej. 'Sala de
    bombas'). El técnico carga una categoría entera de una sola pasada."""

    __tablename__ = "categorias_equipo"
    __table_args__ = (db.UniqueConstraint("empresa_id", "nombre", name="uq_categoria_empresa_nombre"),)

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    nombre = db.Column(db.String(80), nullable=False)
    orden = db.Column(db.Integer, default=0, nullable=False)

    empresa = db.relationship("Empresa", backref="categorias_equipo")

    def __repr__(self):
        return f"<CategoriaEquipo {self.nombre}>"


class TipoEquipo(db.Model):
    """Catálogo de la empresa: qué clases de equipo puede tener una
    instalación. Los formularios apuntan acá por FK (cambio 6), así que
    renombrar un tipo ya no deja secciones huérfanas."""

    __tablename__ = "tipos_equipo"
    __table_args__ = (db.UniqueConstraint("empresa_id", "nombre", name="uq_tipo_equipo_empresa_nombre"),)

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias_equipo.id"), nullable=True, index=True)
    nombre = db.Column(db.String(60), nullable=False)
    # Un equipo de este tipo se carga colgando de uno de este otro tipo
    # (el controlador cuelga de su bomba, el tanque de su bomba diesel).
    # Sirve para validar al cargar y para ordenar el recorrido en campo.
    tipo_padre_id = db.Column(db.Integer, db.ForeignKey("tipos_equipo.id"), nullable=True)
    # Un "conjunto" es el equipo que encabeza el recorrido en campo: la
    # bomba principal arrastra a su controlador y su tanque.
    encabeza_conjunto = db.Column(db.Boolean, default=False, nullable=False)
    orden = db.Column(db.Integer, default=0, nullable=False)

    empresa = db.relationship("Empresa", backref="tipos_equipo")
    categoria = db.relationship("CategoriaEquipo", backref="tipos_equipo")
    tipo_padre = db.relationship("TipoEquipo", remote_side=[id], backref="tipos_hijos")

    def __repr__(self):
        return f"<TipoEquipo {self.nombre}>"


class Equipo(db.Model):
    """Equipo físico de una instalación. `padre_id` arma los conjuntos
    reales: el controlador y el tanque de combustible cuelgan de su bomba
    principal, y el checklist se recorre bomba por bomba en vez de
    agrupado por tipo de formulario (cambio 5)."""

    __tablename__ = "equipos"

    id = db.Column(db.Integer, primary_key=True)
    instalacion_id = db.Column(db.Integer, db.ForeignKey("instalaciones.id"), nullable=False, index=True)
    tipo_equipo_id = db.Column(db.Integer, db.ForeignKey("tipos_equipo.id"), nullable=False, index=True)
    padre_id = db.Column(db.Integer, db.ForeignKey("equipos.id"), nullable=True, index=True)
    codigo = db.Column(db.String(40))
    nombre = db.Column(db.String(150), nullable=False)
    ubicacion = db.Column(db.String(250))
    activo = db.Column(db.Boolean, default=True, nullable=False)

    # Datos de placa. Son el rango esperado "por equipo": la presión de
    # descarga correcta depende del modelo instalado, así que no puede ir
    # como rango fijo del formulario (ver CampoFormulario).
    marca = db.Column(db.String(150))
    modelo = db.Column(db.String(150))
    serie = db.Column(db.String(150))
    caudal_nominal = db.Column(db.Float)       # GPM
    presion_diseno = db.Column(db.Float)       # psi a 100 % del caudal
    presion_maxima = db.Column(db.Float)       # psi a caudal 0 % (churn)
    presion_sobrecarga = db.Column(db.Float)   # psi a 150 % del caudal
    rpm_nominal = db.Column(db.Integer)

    instalacion = db.relationship("Instalacion", backref=db.backref("equipos", cascade="all, delete-orphan"))
    tipo_equipo = db.relationship("TipoEquipo")
    padre = db.relationship("Equipo", remote_side=[id], backref="hijos")

    @property
    def etiqueta(self):
        return f"{self.codigo} · {self.nombre}" if self.codigo else self.nombre

    def __repr__(self):
        return f"<Equipo {self.etiqueta}>"


# ---------------------------------------------------------------------------
# Formularios tipo — lo que NFPA 25 define y NO cambia por cliente
# ---------------------------------------------------------------------------


class TipoFormulario(db.Model):
    """Define qué se inspecciona y con qué campos.

    Cuelga de la Empresa (cambio 1). Es la diferencia de fondo con el
    modelo viejo: la prueba sin flujo de una bomba diesel es la misma en
    todas las salas, así que definirla una vez por cliente era duplicar
    trabajo y garantizar que se desincronizaran entre sí.
    """

    __tablename__ = "tipos_formulario"
    __table_args__ = (db.UniqueConstraint("empresa_id", "nombre", name="uq_tipo_formulario_empresa_nombre"),)

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)

    # Si es True se completa una vez por cada equipo de ese tipo en la
    # instalación; si es False, una sola vez para todo el recinto.
    por_equipo = db.Column(db.Boolean, default=True, nullable=False)
    # FK, no nombre de texto (cambio 6).
    tipo_equipo_id = db.Column(db.Integer, db.ForeignKey("tipos_equipo.id"), nullable=True, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias_equipo.id"), nullable=True, index=True)

    frecuencia = db.Column(db.String(30))             # semanal | mensual | anual
    referencia_normativa = db.Column(db.String(200))  # ej. "NFPA 25 · cap. 8"
    orden = db.Column(db.Integer, default=0, nullable=False)
    # Los que quedan fuera del paquete de rutina, para no mezclarlos con
    # la inspección de rutina (hoy no hay ninguno así, pero el mecanismo
    # sigue disponible por si hace falta un formulario que no cuelgue de
    # ninguna frecuencia calendario).
    incluir_en_paquete = db.Column(db.Boolean, default=True, nullable=False)
    # La prueba anual de caudal no se completa como una grilla de campos:
    # es un ensayo con puntos dinámicos (churn/50/100/150 + los que se
    # agreguen), succión, corrección por afinidad y un gráfico en vivo.
    # Este flag le dice al motor del checklist que renderice ese widget
    # en vez de la grilla genérica de CampoFormulario (ver checklist.py).
    es_ensayo_curva = db.Column(db.Boolean, default=False, nullable=False)

    empresa = db.relationship("Empresa", backref="tipos_formulario")
    tipo_equipo = db.relationship("TipoEquipo", backref="tipos_formulario")
    categoria = db.relationship("CategoriaEquipo", backref="tipos_formulario")

    campos = db.relationship(
        "CampoFormulario",
        backref="tipo_formulario",
        cascade="all, delete-orphan",
        order_by="CampoFormulario.orden",
    )

    def __repr__(self):
        return f"<TipoFormulario {self.nombre}>"


class CampoFormulario(db.Model):
    """Un punto de inspección.

    Los rangos `minimo`/`maximo` son los *universales* — los que valen en
    cualquier sala del mundo (combustible por debajo de 66 %, rodamientos
    por encima de 80 °C). Los rangos que dependen del equipo instalado
    (presiones, caudal, RPM) no van acá: salen de la placa del Equipo.
    """

    __tablename__ = "campos_formulario"

    id = db.Column(db.Integer, primary_key=True)
    tipo_formulario_id = db.Column(
        db.Integer, db.ForeignKey("tipos_formulario.id"), nullable=False, index=True
    )
    clave = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # ver TIPOS_CAMPO
    unidad = db.Column(db.String(20))
    # Opciones para seleccion / multi_seleccion, separadas por "|".
    opciones_raw = db.Column(db.Text)
    # Si el campo lleva además el control Conforme / No conforme / N-A.
    # Los de tipo `estado` lo llevan por definición.
    con_estado = db.Column(db.Boolean, default=True, nullable=False)

    # Rango universal (cambio 3). Fuera de rango abre la no conformidad
    # sola, sin depender de que el técnico recuerde el valor de referencia.
    minimo = db.Column(db.Float)
    maximo = db.Column(db.Float)
    # Gravedad que se asigna cuando el disparo es automático por rango.
    gravedad_fuera_rango = db.Column(db.String(20), default=GRAVEDAD_NO_CRITICA)

    # En qué rutina entra este punto. Nulo = la del formulario. Es lo que
    # permite que la estación húmeda sea UN formulario cuyo checklist crece
    # con la rutina: mensual registra posición de válvulas y presiones, la
    # semestral agrega las pruebas de señales, y la anual la purga del
    # inspector con sus tiempos.
    frecuencia = db.Column(db.String(20))

    ayuda = db.Column(db.String(300))
    orden = db.Column(db.Integer, default=0, nullable=False)

    # Rango por equipo (pendiente del README, cambio complementario al 3).
    # A diferencia de minimo/maximo, este no es fijo: sale de la placa del
    # equipo que responde la sección, con una tolerancia porque un valor de
    # campo nunca coincide exactamente con el de fábrica. Sin equipo (una
    # sección de recinto) o sin ese dato cargado en la placa, no aplica.
    atributo_equipo = db.Column(db.String(30))  # ver ATRIBUTOS_EQUIPO
    tolerancia_pct = db.Column(db.Float, default=10.0)

    @property
    def frecuencia_efectiva(self):
        return self.frecuencia or (self.tipo_formulario.frecuencia if self.tipo_formulario else None)

    def entra_en(self, rutina):
        """True si este punto se carga en esa rutina."""
        return nivel_frecuencia(self.frecuencia_efectiva) <= nivel_frecuencia(rutina)

    @property
    def opciones(self):
        if not self.opciones_raw:
            return []
        return [o.strip() for o in self.opciones_raw.split("|") if o.strip()]

    @property
    def lleva_estado(self):
        return self.tipo == CAMPO_ESTADO or self.con_estado

    @property
    def tiene_rango(self):
        return self.minimo is not None or self.maximo is not None

    def fuera_de_rango(self, valor):
        """True si el valor numérico cae fuera del rango universal."""
        if valor is None or not self.tiene_rango:
            return False
        try:
            n = float(valor)
        except (TypeError, ValueError):
            return False
        if self.minimo is not None and n < self.minimo:
            return True
        if self.maximo is not None and n > self.maximo:
            return True
        return False

    def texto_rango(self):
        sufijo = f" {self.unidad}" if self.unidad else ""
        if self.minimo is not None and self.maximo is not None:
            return f"{self.minimo:g} – {self.maximo:g}{sufijo}"
        if self.minimo is not None:
            return f"mín. {self.minimo:g}{sufijo}"
        if self.maximo is not None:
            return f"máx. {self.maximo:g}{sufijo}"
        return ""

    def rango_equipo(self, equipo):
        """Rango esperado según la placa del equipo, o None si el campo no
        depende de un atributo de equipo, no hay equipo (sección de
        recinto), o la placa no tiene ese dato cargado."""
        if not self.atributo_equipo or equipo is None:
            return None
        if self.atributo_equipo not in ATRIBUTOS_EQUIPO:
            return None
        valor_placa = getattr(equipo, self.atributo_equipo, None)
        if valor_placa is None:
            return None
        tolerancia = (self.tolerancia_pct if self.tolerancia_pct is not None else 10.0) / 100.0
        return (valor_placa * (1 - tolerancia), valor_placa * (1 + tolerancia))

    def fuera_de_rango_equipo(self, valor, equipo):
        """True si el valor cae fuera del rango de la placa del equipo."""
        rango = self.rango_equipo(equipo)
        if rango is None or valor is None:
            return False
        try:
            n = float(valor)
        except (TypeError, ValueError):
            return False
        minimo, maximo = rango
        return n < minimo or n > maximo

    def texto_rango_equipo(self, equipo):
        rango = self.rango_equipo(equipo)
        if rango is None:
            return ""
        minimo, maximo = rango
        sufijo = f" {self.unidad}" if self.unidad else ""
        return f"{minimo:g} – {maximo:g}{sufijo} según placa del equipo"

    def __repr__(self):
        return f"<CampoFormulario {self.clave}>"


# ---------------------------------------------------------------------------
# Visitas y carga en campo
# ---------------------------------------------------------------------------


class Visita(db.Model):
    __tablename__ = "visitas"

    id = db.Column(db.Integer, primary_key=True)
    instalacion_id = db.Column(db.Integer, db.ForeignKey("instalaciones.id"), nullable=False, index=True)
    fecha = db.Column(db.Date, default=date.today, nullable=False)
    estado = db.Column(db.String(20), default="Pendiente", nullable=False)
    tecnico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True, index=True)
    # Nota libre de la visita. Se llama `notas` y no `observaciones` para
    # que "observación" signifique siempre un registro del banco de
    # deficiencias — que llega acá como backref desde Observacion.visita.
    notas = db.Column(db.Text)

    instalacion = db.relationship("Instalacion", backref="visitas")
    tecnico = db.relationship("Usuario")

    def __repr__(self):
        return f"<Visita {self.id} {self.fecha}>"


class ItemVisita(db.Model):
    """Una categoría a inspeccionar dentro de una visita (ej. 'Sala de
    bombas'). Es contra este ítem que se cargan los formularios."""

    __tablename__ = "items_visita"

    id = db.Column(db.Integer, primary_key=True)
    visita_id = db.Column(db.Integer, db.ForeignKey("visitas.id"), nullable=False, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias_equipo.id"), nullable=False, index=True)
    estado = db.Column(db.String(20), default="Pendiente", nullable=False)
    # Qué rutina se ejecuta. Define hasta qué nivel de campos se carga:
    # la anual incluye lo semestral, que a su vez incluye lo mensual.
    rutina = db.Column(db.String(20), default=FRECUENCIA_MENSUAL, nullable=False)

    visita = db.relationship("Visita", backref=db.backref("items", cascade="all, delete-orphan"))
    categoria = db.relationship("CategoriaEquipo")

    def __repr__(self):
        return f"<ItemVisita {self.id}>"


class Contrato(db.Model):
    """Lo que la empresa se comprometió a mantener en una instalación.

    Cuelga de la instalación y no del cliente: se contrata el mantenimiento
    de un sitio concreto, con su sala de bombas y su cuarto de válvulas.
    """

    __tablename__ = "contratos"

    id = db.Column(db.Integer, primary_key=True)
    instalacion_id = db.Column(
        db.Integer, db.ForeignKey("instalaciones.id"), nullable=False, index=True
    )
    desde = db.Column(db.Date, default=date.today, nullable=False)
    hasta = db.Column(db.Date)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    notas = db.Column(db.Text)

    instalacion = db.relationship(
        "Instalacion", backref=db.backref("contratos", cascade="all, delete-orphan")
    )
    servicios = db.relationship(
        "ServicioContrato", backref="contrato", cascade="all, delete-orphan"
    )

    def vigente_en(self, cuando=None):
        cuando = cuando or date.today()
        if not self.activo or self.desde > cuando:
            return False
        return self.hasta is None or self.hasta >= cuando

    def __repr__(self):
        return f"<Contrato {self.id} inst={self.instalacion_id}>"


class ServicioContrato(db.Model):
    """Una categoría cubierta por el contrato.

    Deliberadamente NO guarda una frecuencia. Las rutinas son acumulativas
    y una misma categoría tiene varias cadencias (la mensual registra
    presiones, la semestral prueba señales, la anual abre la purga). Poner
    una sola frecuencia acá obligaría a contradecir el catálogo.

    Lo único que hace falta es el **mes ancla**: a partir de él se deriva
    qué rutina cae cada mes, cruzando con las frecuencias que el catálogo
    define para esa categoría. Eso es lo que permite predecir el año entero
    sin cargar nada a mano.
    """

    __tablename__ = "servicios_contrato"
    __table_args__ = (
        db.UniqueConstraint("contrato_id", "categoria_id", name="uq_servicio_contrato_categoria"),
    )

    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey("contratos.id"), nullable=False, index=True)
    categoria_id = db.Column(
        db.Integer, db.ForeignKey("categorias_equipo.id"), nullable=False, index=True
    )
    # Mes en que arranca el ciclo (1-12). La anual cae en este mes, la
    # semestral en este y seis meses después, y así.
    mes_ancla = db.Column(db.Integer, default=1, nullable=False)

    categoria = db.relationship("CategoriaEquipo")

    def __repr__(self):
        return f"<ServicioContrato cat={self.categoria_id} ancla={self.mes_ancla}>"


class OrdenTrabajo(db.Model):
    """El compromiso de trabajo que envuelve a una visita.

    Uno a uno con la Visita. La división es deliberada:

    - La OT responde **a quién, para cuándo y en qué estado** — es lo que
      se asigna, se numera y el cliente puede referenciar.
    - La Visita responde **qué se hizo** — de ella cuelgan los ítems con su
      rutina y los formularios cargados.

    Mezclarlas obligaría a que cada cambio de estado administrativo tocara
    los datos técnicos, y a que reprogramar pareciera una visita nueva.
    """

    __tablename__ = "ordenes_trabajo"

    id = db.Column(db.Integer, primary_key=True)
    # 1:1 — cada OT tiene su visita y viceversa.
    visita_id = db.Column(
        db.Integer, db.ForeignKey("visitas.id"), nullable=False, unique=True, index=True
    )
    numero = db.Column(db.String(30), index=True)
    tipo = db.Column(db.String(30), default=OT_PREVENTIVO, nullable=False)
    prioridad = db.Column(db.String(20), default=PRIORIDAD_MEDIA, nullable=False)
    estado = db.Column(db.String(20), default=OT_PENDIENTE, nullable=False)

    tecnico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True, index=True)
    fecha_apertura = db.Column(db.Date, default=date.today, nullable=False)
    fecha_compromiso = db.Column(db.Date)
    fecha_cierre = db.Column(db.Date)
    descripcion = db.Column(db.Text)

    visita = db.relationship("Visita", backref=db.backref("orden", uselist=False))
    tecnico = db.relationship("Usuario")

    def asignar_numero(self, empresa_id):
        """Numeración correlativa por empresa y año."""
        anio = (self.fecha_apertura or date.today()).year
        cuantas = (
            OrdenTrabajo.query.join(Visita, OrdenTrabajo.visita_id == Visita.id)
            .join(Instalacion, Visita.instalacion_id == Instalacion.id)
            .join(Cliente, Instalacion.cliente_id == Cliente.id)
            .filter(Cliente.empresa_id == empresa_id)
            .filter(db.extract("year", OrdenTrabajo.fecha_apertura) == anio)
            .count()
        )
        self.numero = f"OT-{anio}-{cuantas:04d}"
        return self.numero

    @property
    def vencida(self):
        if not self.fecha_compromiso or self.estado == OT_CERRADA:
            return False
        return self.fecha_compromiso < date.today()

    @property
    def abierta(self):
        return self.estado != OT_CERRADA

    def puede_pasar_a(self, nuevo, usuario):
        """Quién puede mover qué. El técnico la empuja hasta dejarla en
        revisión; cerrarla es de gestión."""
        if nuevo not in ESTADOS_OT:
            return False
        if nuevo == OT_CERRADA:
            return usuario.puede_aprobar and self.estado == OT_EN_REVISION
        if nuevo == OT_EN_REVISION:
            return self.estado in (OT_PENDIENTE, OT_EN_CURSO)
        if nuevo == OT_EN_CURSO:
            return self.estado in (OT_PENDIENTE, OT_EN_REVISION)
        if nuevo == OT_PENDIENTE:
            return usuario.puede_aprobar
        return False

    def __repr__(self):
        return f"<OrdenTrabajo {self.numero} {self.estado}>"


class Formulario(db.Model):
    """Un formulario cargado: un TipoFormulario aplicado a un equipo
    concreto (o al recinto entero, con equipo_id nulo) en una visita."""

    __tablename__ = "formularios"

    id = db.Column(db.Integer, primary_key=True)
    item_visita_id = db.Column(db.Integer, db.ForeignKey("items_visita.id"), nullable=False, index=True)
    tipo_formulario_id = db.Column(db.Integer, db.ForeignKey("tipos_formulario.id"), nullable=False, index=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey("equipos.id"), nullable=True, index=True)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    item_visita = db.relationship("ItemVisita", backref=db.backref("formularios", cascade="all, delete-orphan"))
    tipo_formulario = db.relationship("TipoFormulario")
    equipo = db.relationship("Equipo")
    respuestas = db.relationship("Respuesta", backref="formulario", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Formulario {self.id}>"


class Respuesta(db.Model):
    """El valor cargado para un punto.

    En el modelo viejo esto era un blob JSON. Como fila propia se puede
    consultar, graficar la evolución de un valor y ligar la observación
    que abrió, sin parsear nada.
    """

    __tablename__ = "respuestas"

    id = db.Column(db.Integer, primary_key=True)
    formulario_id = db.Column(db.Integer, db.ForeignKey("formularios.id"), nullable=False, index=True)
    campo_id = db.Column(db.Integer, db.ForeignKey("campos_formulario.id"), nullable=False, index=True)

    valor_numero = db.Column(db.Float)
    valor_texto = db.Column(db.Text)
    estado = db.Column(db.String(20))     # ver ESTADOS_PUNTO
    gravedad = db.Column(db.String(20))   # ver GRAVEDADES, solo si no_conforme
    comentario = db.Column(db.Text)
    # True si la no conformidad la disparó el rango, no el técnico.
    disparo_automatico = db.Column(db.Boolean, default=False, nullable=False)

    campo = db.relationship("CampoFormulario")

    @property
    def valor(self):
        return self.valor_numero if self.valor_numero is not None else self.valor_texto

    def __repr__(self):
        return f"<Respuesta {self.campo_id}={self.valor} {self.estado}>"


# ---------------------------------------------------------------------------
# Prueba anual de caudal — succión, corrección por afinidad y comparación
# directa contra los tres psi de placa (churn / 100 % / 150 %). Ver
# app/ensayo_caudal.py para el cálculo; acá solo vive el dato.
# ---------------------------------------------------------------------------

# Etiquetas de los puntos fijos del ensayo. "50" no tiene referencia de
# placa (ningún fabricante certifica un cuarto punto ahí) pero se pide
# igual porque ayuda a leer la forma de la curva.
PUNTO_CHURN = "churn"
PUNTO_50 = "50"
PUNTO_100 = "100"
PUNTO_150 = "150"
PUNTO_EXTRA = "extra"
PUNTOS_FIJOS = (PUNTO_CHURN, PUNTO_50, PUNTO_100, PUNTO_150)


class EnsayoCaudal(db.Model):
    """Un ensayo de curva de caudal cargado para un equipo, en una visita.

    Uno por (item_visita, equipo): repetir el ensayo en la misma visita
    pisa el anterior, no lo duplica — no tiene sentido guardar dos
    intentos del mismo día como si fueran mediciones distintas.
    """

    __tablename__ = "ensayos_caudal"

    id = db.Column(db.Integer, primary_key=True)
    item_visita_id = db.Column(db.Integer, db.ForeignKey("items_visita.id"), nullable=False, index=True)
    tipo_formulario_id = db.Column(db.Integer, db.ForeignKey("tipos_formulario.id"), nullable=False, index=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey("equipos.id"), nullable=False, index=True)

    metodo = db.Column(db.String(30))  # Manifold | Caudalímetro | Recirculación
    # Comparación con la curva certificada de fábrica: no es un número, es
    # que la forma medida se parezca a la de fábrica — eso lo juzga el
    # técnico mirando el gráfico, no se deriva de los puntos.
    curva_conforme = db.Column(db.Boolean)
    comentario = db.Column(db.Text)

    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    item_visita = db.relationship("ItemVisita", backref=db.backref("ensayos_caudal", cascade="all, delete-orphan"))
    tipo_formulario = db.relationship("TipoFormulario")
    equipo = db.relationship("Equipo", backref="ensayos_caudal")
    creado_por = db.relationship("Usuario")

    __table_args__ = (
        db.UniqueConstraint("item_visita_id", "equipo_id", name="uq_ensayo_item_equipo"),
    )

    def __repr__(self):
        return f"<EnsayoCaudal equipo={self.equipo_id}>"


class PuntoEnsayoCaudal(db.Model):
    """Una medición del ensayo: succión, descarga y RPM en un caudal dado.

    `caudal`/`succion`/`descarga`/`rpm` son el dato crudo tal como lo lee
    el técnico de los manómetros — la resta de succión y la corrección
    por afinidad se calculan al vuelo (ver ensayo_caudal.py), no se
    guardan, para que cambiar la placa del equipo después no deje corregido
    guardado y crudo desincronizados.
    """

    __tablename__ = "puntos_ensayo_caudal"

    id = db.Column(db.Integer, primary_key=True)
    ensayo_id = db.Column(db.Integer, db.ForeignKey("ensayos_caudal.id"), nullable=False, index=True)

    etiqueta = db.Column(db.String(20), nullable=False)  # ver PUNTO_*
    orden = db.Column(db.Integer, default=0, nullable=False)

    caudal = db.Column(db.Float)
    succion = db.Column(db.Float)
    descarga = db.Column(db.Float)
    rpm = db.Column(db.Float)

    ensayo = db.relationship(
        "EnsayoCaudal",
        backref=db.backref("puntos", cascade="all, delete-orphan", order_by="PuntoEnsayoCaudal.orden"),
    )

    @property
    def fijo(self):
        return self.etiqueta in PUNTOS_FIJOS

    def __repr__(self):
        return f"<PuntoEnsayoCaudal {self.etiqueta} q={self.caudal}>"


# ---------------------------------------------------------------------------
# Banco de deficiencias
# ---------------------------------------------------------------------------


class Foto(db.Model):
    """Una foto del banco.

    Se saca al final del checklist, no punto por punto: el técnico termina
    de recorrer la sala y ahí elige a qué equipo corresponde cada foto. Por
    eso lo que importa es `equipo_id`, que es además como se navega el
    banco ("todas las fotos de controladores").

    `item_visita_id` guarda en qué inspección se tomó, pero es opcional:
    una foto puede cargarse fuera de una visita.

    `instalacion_id` se guarda aunque sea derivable, porque es lo que
    permite listar y filtrar el banco sin encadenar joins en cada consulta.
    """

    __tablename__ = "fotos"

    id = db.Column(db.Integer, primary_key=True)
    instalacion_id = db.Column(
        db.Integer, db.ForeignKey("instalaciones.id"), nullable=False, index=True
    )
    equipo_id = db.Column(db.Integer, db.ForeignKey("equipos.id"), nullable=True, index=True)
    item_visita_id = db.Column(
        db.Integer, db.ForeignKey("items_visita.id"), nullable=True, index=True
    )

    archivo = db.Column(db.String(200), nullable=False)
    nombre_original = db.Column(db.String(255))
    descripcion = db.Column(db.String(300))
    ancho = db.Column(db.Integer)
    alto = db.Column(db.Integer)
    bytes = db.Column(db.Integer)

    tomada_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    instalacion = db.relationship("Instalacion", backref="fotos")
    equipo = db.relationship("Equipo", backref="fotos")
    item_visita = db.relationship("ItemVisita", backref="fotos")
    tomada_por = db.relationship("Usuario")

    @property
    def tipo_equipo(self):
        """El tipo al que pertenece, que es como se navega el banco."""
        return self.equipo.tipo_equipo if self.equipo else None

    def __repr__(self):
        return f"<Foto {self.archivo}>"


class Observacion(db.Model):
    """Banco de deficiencias del cliente.

    El cliente solo ve las que están Aprobadas y no son Internas. Aprueba
    un jefe técnico — salvo que la visita la haya hecho un jefe técnico,
    en cuyo caso nacen aprobadas (ver checklist.guardar_checklist).
    """

    __tablename__ = "observaciones"

    id = db.Column(db.Integer, primary_key=True)
    instalacion_id = db.Column(db.Integer, db.ForeignKey("instalaciones.id"), nullable=False, index=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey("equipos.id"), nullable=True, index=True)
    respuesta_id = db.Column(db.Integer, db.ForeignKey("respuestas.id"), nullable=True, index=True)
    # Alternativa a respuesta_id para lo que dispara un punto del ensayo
    # de curva de caudal, que no tiene Respuesta propia (ver EnsayoCaudal).
    punto_ensayo_id = db.Column(db.Integer, db.ForeignKey("puntos_ensayo_caudal.id"), nullable=True, index=True)
    visita_id = db.Column(db.Integer, db.ForeignKey("visitas.id"), nullable=True, index=True)

    clasificacion = db.Column(db.String(40), nullable=False)  # ver CLASIFICACIONES
    descripcion = db.Column(db.Text, nullable=False)
    fecha_carga = db.Column(db.Date, default=date.today, nullable=False)

    estado_revision = db.Column(db.String(20), default=REVISION_PENDIENTE, nullable=False)
    visibilidad = db.Column(db.String(20), default=VISIBILIDAD_CLIENTE, nullable=False)
    aprobada_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    fecha_aprobacion = db.Column(db.DateTime)
    # Deja rastro de que se aprobó sola por ser el técnico un jefe.
    aprobacion_automatica = db.Column(db.Boolean, default=False, nullable=False)

    resuelto = db.Column(db.Boolean, default=False, nullable=False)
    fecha_resolucion = db.Column(db.Date)
    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    instalacion = db.relationship("Instalacion", backref="observaciones")
    equipo = db.relationship("Equipo", backref="observaciones")
    respuesta = db.relationship("Respuesta", backref="observacion", uselist=False)
    # uselist=False en los DOS lados: sin el del backref, PuntoEnsayoCaudal
    # .observacion queda como lista (nada impide en la FK que dos
    # Observacion apunten al mismo punto), y actualizar_observacion() la
    # trata como una fila sola. Un punto nunca tiene más de una a la vez.
    punto_ensayo = db.relationship(
        "PuntoEnsayoCaudal", backref=db.backref("observacion", uselist=False), uselist=False,
    )
    visita = db.relationship("Visita", backref="observaciones")
    aprobada_por = db.relationship("Usuario", foreign_keys=[aprobada_por_id])
    creado_por = db.relationship("Usuario", foreign_keys=[creado_por_id])

    @property
    def es_critica(self):
        return self.clasificacion == CLASIF_CRITICA

    @property
    def visible_para_cliente(self):
        return (
            self.estado_revision == REVISION_APROBADA
            and self.visibilidad != VISIBILIDAD_INTERNA
        )

    def aprobar(self, usuario, automatica=False):
        self.estado_revision = REVISION_APROBADA
        self.aprobada_por_id = usuario.id if usuario else None
        self.fecha_aprobacion = datetime.utcnow()
        self.aprobacion_automatica = automatica

    def __repr__(self):
        return f"<Observacion {self.clasificacion} {self.estado_revision}>"


# ---------------------------------------------------------------------------
# Presupuestos — tracking de aprobación de una deficiencia que requiere
# cotizar un correctivo. Ver app/presupuestos.py para la lógica de creación;
# acá vive el dato y la transición de estado (que sí crea la OT).
# ---------------------------------------------------------------------------


class Presupuesto(db.Model):
    __tablename__ = "presupuestos"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    observacion_id = db.Column(
        db.Integer, db.ForeignKey("observaciones.id"), nullable=False, unique=True, index=True
    )
    # Se completa recién al aprobar: antes de eso no hay correctivo que hacer.
    ot_id = db.Column(db.Integer, db.ForeignKey("ordenes_trabajo.id"), nullable=True, index=True)
    codigo = db.Column(db.String(30), unique=True, nullable=False)
    estado = db.Column(db.String(20), default=PRESUP_PENDIENTE, nullable=False)
    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    empresa = db.relationship("Empresa", backref="presupuestos")
    observacion = db.relationship(
        "Observacion",
        backref=db.backref(
            "presupuesto", uselist=False, cascade="all, delete-orphan", single_parent=True
        ),
    )
    ot = db.relationship("OrdenTrabajo", backref=db.backref("presupuesto_origen", uselist=False))
    creado_por = db.relationship("Usuario")

    @property
    def dias_abierto(self):
        return (datetime.utcnow() - self.fecha_creacion).days

    def cambiar_estado(self, nuevo, usuario, nota=None):
        """Valida la transición y, si aprueba, crea la OT correctiva.

        No hace commit — viaja en la transacción del caller, igual que el
        resto de los cambios de estado del sistema (ver OrdenTrabajo).
        """
        anterior = self.estado
        # Cerrado solo lo pone el sistema, al finalizar la OT correctiva —
        # no es una transición manual, así que no está en TRANSICIONES_PRESUPUESTO.
        es_cierre_automatico = nuevo == PRESUP_CERRADO and anterior == PRESUP_APROBADO
        if not es_cierre_automatico and nuevo not in TRANSICIONES_PRESUPUESTO.get(anterior, []):
            raise ValueError(f"No se puede pasar de «{anterior}» a «{nuevo}».")

        self.estado = nuevo
        if nuevo == PRESUP_APROBADO and self.ot_id is None:
            visita = Visita(
                instalacion_id=self.observacion.instalacion_id,
                fecha=date.today(),
                notas=f"Correctivo por presupuesto {self.codigo}.",
            )
            db.session.add(visita)
            db.session.flush()
            ot = OrdenTrabajo(
                visita_id=visita.id,
                tipo=OT_CORRECTIVO,
                prioridad=PRIORIDAD_MEDIA,
                estado=OT_PENDIENTE,
                descripcion=f"Ejecución presupuesto {self.codigo}: {self.observacion.descripcion}",
            )
            db.session.add(ot)
            db.session.flush()
            ot.asignar_numero(self.empresa_id)
            self.ot_id = ot.id

        db.session.add(PresupuestoAudit(
            presupuesto_id=self.id,
            estado_anterior=anterior,
            estado_nuevo=nuevo,
            usuario_id=usuario.id if usuario else None,
            nota=nota,
        ))

    def __repr__(self):
        return f"<Presupuesto {self.codigo} {self.estado}>"


class PresupuestoAudit(db.Model):
    """Historial inmutable de cambios de estado. Solo inserción: nunca se
    edita ni se borra una fila cargada acá."""

    __tablename__ = "presupuestos_audit"

    id = db.Column(db.Integer, primary_key=True)
    presupuesto_id = db.Column(db.Integer, db.ForeignKey("presupuestos.id"), nullable=False, index=True)
    estado_anterior = db.Column(db.String(20))
    estado_nuevo = db.Column(db.String(20), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    nota = db.Column(db.Text)
    fecha_cambio = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    presupuesto = db.relationship(
        "Presupuesto",
        backref=db.backref("auditoria", cascade="all, delete-orphan", order_by="PresupuestoAudit.fecha_cambio"),
    )
    usuario = db.relationship("Usuario")

    def __repr__(self):
        return f"<PresupuestoAudit {self.estado_anterior}→{self.estado_nuevo}>"


# ---------------------------------------------------------------------------
# Inventario de repuestos — catálogo por empresa y su consumo por OT.
# ---------------------------------------------------------------------------


class Repuesto(db.Model):
    """Catálogo de repuestos de la empresa (como TipoEquipo: compartido
    entre instalaciones, no por cliente)."""

    __tablename__ = "repuestos"
    __table_args__ = (db.UniqueConstraint("empresa_id", "nombre", name="uq_repuesto_empresa_nombre"),)

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    nombre = db.Column(db.String(150), nullable=False)
    codigo = db.Column(db.String(60))
    unidad = db.Column(db.String(30), default="unidad", nullable=False)
    stock_actual = db.Column(db.Integer, default=0, nullable=False)
    stock_minimo = db.Column(db.Integer, default=0, nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa", backref="repuestos")

    @property
    def en_nivel_critico(self):
        return self.stock_actual <= self.stock_minimo

    def __repr__(self):
        return f"<Repuesto {self.nombre}>"


class ConsumoRepuesto(db.Model):
    """Un uso de repuesto registrado desde una OT. Descuenta stock en el
    momento de crearse (ver app/inventario.py)."""

    __tablename__ = "consumos_repuesto"

    id = db.Column(db.Integer, primary_key=True)
    ot_id = db.Column(db.Integer, db.ForeignKey("ordenes_trabajo.id"), nullable=False, index=True)
    repuesto_id = db.Column(db.Integer, db.ForeignKey("repuestos.id"), nullable=False, index=True)
    cantidad = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.Date, default=date.today, nullable=False)

    ot = db.relationship("OrdenTrabajo", backref=db.backref("consumos", cascade="all, delete-orphan"))
    repuesto = db.relationship("Repuesto", backref="consumos")

    def __repr__(self):
        return f"<ConsumoRepuesto repuesto={self.repuesto_id} x{self.cantidad}>"


# ---------------------------------------------------------------------------
# Notificaciones internas — sin push, solo in-app (badge + página propia).
# ---------------------------------------------------------------------------


class Notificacion(db.Model):
    __tablename__ = "notificaciones"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    destinatario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    remitente_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    tipo = db.Column(db.String(30), nullable=False)  # ver TIPOS_NOTIFICACION
    titulo = db.Column(db.String(250), nullable=False)
    enlace = db.Column(db.String(300))
    leido = db.Column(db.Boolean, default=False, nullable=False)
    fecha_carga = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    destinatario = db.relationship("Usuario", foreign_keys=[destinatario_id], backref="notificaciones")
    remitente = db.relationship("Usuario", foreign_keys=[remitente_id])

    @property
    def severidad(self):
        return SEVERIDAD_NOTIFICACION.get(self.tipo, "info")

    @property
    def descripcion_tipo(self):
        return TIPOS_NOTIFICACION.get(self.tipo, self.tipo)

    def __repr__(self):
        return f"<Notificacion {self.tipo} → {self.destinatario_id}>"


# ---------------------------------------------------------------------------
# Coordinación con auditoría — extiende planificacion.py (que calcula el
# calendario sin persistir nada) con un registro que permite recoordinar
# una fecha ya confirmada sin perder el historial.
# ---------------------------------------------------------------------------


class SolicitudCoordinacion(db.Model):
    """Una fila por servicio de contrato y mes. Nace al generar las
    solicitudes del mes; se completa al coordinar por primera vez, y se
    puede recoordinar después sin perder la fecha anterior (ver
    CoordinacionAudit)."""

    __tablename__ = "solicitudes_coordinacion"
    __table_args__ = (
        db.UniqueConstraint("servicio_id", "anio", "mes", name="uq_solicitud_servicio_mes"),
    )

    id = db.Column(db.Integer, primary_key=True)
    servicio_id = db.Column(db.Integer, db.ForeignKey("servicios_contrato.id"), nullable=False, index=True)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)

    coordinada = db.Column(db.Boolean, default=False, nullable=False)
    fecha_coordinada = db.Column(db.Date)
    notas = db.Column(db.Text)
    coordinado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    fecha_coordinacion = db.Column(db.DateTime)
    orden_id = db.Column(db.Integer, db.ForeignKey("ordenes_trabajo.id"), nullable=True, index=True)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    servicio = db.relationship("ServicioContrato", backref="solicitudes")
    coordinado_por = db.relationship("Usuario")
    orden = db.relationship("OrdenTrabajo")

    @property
    def estado_derivado(self):
        if not self.coordinada:
            return "sin_coordinar"
        if not self.orden or not self.orden.tecnico_id:
            return "coordinada"
        if self.orden.estado == OT_CERRADA:
            return "ejecutada"
        if self.orden.estado == OT_EN_CURSO:
            return "en_ejecucion"
        return "asignada"

    def __repr__(self):
        return f"<SolicitudCoordinacion servicio={self.servicio_id} {self.anio}-{self.mes:02d}>"


class CoordinacionAudit(db.Model):
    """Historial inmutable de fechas coordinadas/recoordinadas."""

    __tablename__ = "coordinacion_audit"

    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(
        db.Integer, db.ForeignKey("solicitudes_coordinacion.id"), nullable=False, index=True
    )
    fecha_anterior = db.Column(db.Date)  # nula la primera vez que se coordina
    fecha_nueva = db.Column(db.Date, nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    nota = db.Column(db.Text)
    fecha_cambio = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    solicitud = db.relationship(
        "SolicitudCoordinacion",
        backref=db.backref(
            "auditoria", cascade="all, delete-orphan", order_by="CoordinacionAudit.fecha_cambio.desc()"
        ),
    )
    usuario = db.relationship("Usuario")

    def __repr__(self):
        return f"<CoordinacionAudit {self.fecha_anterior}→{self.fecha_nueva}>"
