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

ROLES = ["Administrador", "Jefe técnico", "Técnico", "Cliente"]

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

    empresa = db.relationship("Empresa", backref="usuarios")

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
    # Los que quedan fuera del paquete de rutina (ej. la prueba anual de
    # caudal) para no mezclarlos con la inspección semanal.
    incluir_en_paquete = db.Column(db.Boolean, default=True, nullable=False)

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
# Banco de deficiencias
# ---------------------------------------------------------------------------


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
