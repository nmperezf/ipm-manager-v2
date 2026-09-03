"""Catálogo de sala de bombas y datos de demostración.

El paquete se carga UNA VEZ por empresa. Es la diferencia de fondo con el
modelo viejo, donde había que recrearlo o importarlo para cada cliente: la
prueba sin flujo de una bomba diesel es idéntica en todas las salas, así
que definirla por cliente solo garantizaba que se desincronizaran.

Las frecuencias siguen NFPA 25 y hay que confirmarlas contra la edición a
la que se certifica — en particular la de bombas eléctricas, que pasó de
semanal a mensual en ediciones recientes.
"""

from datetime import date

from app.models import (
    CAMPO_ESTADO,
    CAMPO_NUMERO,
    CAMPO_SELECCION,
    CAMPO_TEXTO,
    FRECUENCIA_ANUAL,
    FRECUENCIA_MENSUAL,
    FRECUENCIA_SEMESTRAL,
    GRAVEDAD_CRITICA,
    GRAVEDAD_NO_CRITICA,
    CampoFormulario,
    CategoriaEquipo,
    Cliente,
    Contrato,
    Empresa,
    Equipo,
    Instalacion,
    ServicioContrato,
    TipoEquipo,
    TipoFormulario,
    Usuario,
    db,
)

CATEGORIA = "Sala de bombas"

T_BOMBA_ELEC = "Bomba principal eléctrica"
T_BOMBA_DIESEL = "Bomba principal diesel"
T_CONTROLADOR = "Controlador"
T_TANQUE = "Tanque de combustible"
T_JOCKEY = "Bomba jockey"
T_RESERVA = "Reserva de agua"


def _campo(clave, label, tipo, orden, unidad=None, minimo=None, maximo=None,
           opciones=None, con_estado=True, gravedad=GRAVEDAD_NO_CRITICA, ayuda=None,
           frecuencia=None, atributo_equipo=None, tolerancia=10.0):
    return CampoFormulario(
        clave=clave,
        label=label,
        tipo=tipo,
        unidad=unidad,
        minimo=minimo,
        maximo=maximo,
        opciones_raw="|".join(opciones) if opciones else None,
        con_estado=con_estado,
        gravedad_fuera_rango=gravedad,
        ayuda=ayuda,
        frecuencia=frecuencia,
        atributo_equipo=atributo_equipo,
        tolerancia_pct=tolerancia,
        orden=orden,
    )


def _modo_encendido(orden):
    """Primer campo de toda bomba principal: cómo se la arrancó para esta
    prueba. Es opción única — con multi_seleccion se podría marcar Test y
    Manual a la vez, que no significa nada."""
    return _campo(
        "modo_encendido", "Modo de encendido", CAMPO_SELECCION, orden,
        opciones=["Test", "Automático", "Manual"],
        ayuda="Cómo se arrancó la bomba para esta prueba",
    )


def _formulario(empresa, categoria, nombre, orden, campos, tipo_equipo=None,
                por_equipo=True, frecuencia=None, referencia=None, en_paquete=True,
                descripcion=None):
    tipo = TipoFormulario(
        empresa_id=empresa.id,
        categoria_id=categoria.id,
        nombre=nombre,
        descripcion=descripcion,
        por_equipo=por_equipo,
        tipo_equipo_id=tipo_equipo.id if tipo_equipo else None,
        frecuencia=frecuencia,
        referencia_normativa=referencia,
        orden=orden,
        incluir_en_paquete=en_paquete,
    )
    tipo.campos = campos
    db.session.add(tipo)
    return tipo


def sembrar_catalogo(empresa):
    """Carga la categoría Sala de bombas con sus tipos de equipo y sus
    formularios tipo. Idempotente: si ya está, no hace nada."""
    ya = CategoriaEquipo.query.filter_by(empresa_id=empresa.id, nombre=CATEGORIA).first()
    if ya:
        return ya

    categoria = CategoriaEquipo(empresa_id=empresa.id, nombre=CATEGORIA, orden=10)
    db.session.add(categoria)
    db.session.flush()

    def tipo_equipo(nombre, orden, encabeza=False, padre=None):
        te = TipoEquipo(
            empresa_id=empresa.id,
            categoria_id=categoria.id,
            nombre=nombre,
            orden=orden,
            encabeza_conjunto=encabeza,
            tipo_padre_id=padre.id if padre else None,
        )
        db.session.add(te)
        db.session.flush()
        return te

    # Las bombas principales encabezan conjunto: el controlador y el tanque
    # cuelgan de ellas, y el técnico las recorre juntas.
    te_elec = tipo_equipo(T_BOMBA_ELEC, 10, encabeza=True)
    te_diesel = tipo_equipo(T_BOMBA_DIESEL, 20, encabeza=True)
    te_ctrl = tipo_equipo(T_CONTROLADOR, 30)
    te_tanque = tipo_equipo(T_TANQUE, 40, padre=te_diesel)
    te_jockey = tipo_equipo(T_JOCKEY, 50)
    te_reserva = tipo_equipo(T_RESERVA, 60)

    # ---- 1 · Condiciones del recinto (sala completa) ----------------------
    _formulario(
        empresa, categoria, "Condiciones del recinto", 10,
        por_equipo=False, frecuencia="semanal", referencia="NFPA 25 · cap. 8",
        descripcion="Se carga una vez para toda la sala.",
        campos=[
            _campo("temperatura", "Temperatura del local", CAMPO_NUMERO, 10,
                   unidad="°C", minimo=4, gravedad=GRAVEDAD_CRITICA,
                   ayuda="Mín. 4 °C · 21 °C si hay diesel sin calefactor"),
            _campo("ventilacion", "Ventilación y rejillas", CAMPO_ESTADO, 20,
                   ayuda="Libres, sin obstrucción"),
            _campo("iluminacion", "Iluminación de emergencia", CAMPO_ESTADO, 30),
            _campo("drenaje", "Drenaje del piso", CAMPO_ESTADO, 40,
                   ayuda="Sin agua acumulada"),
            _campo("acceso", "Acceso libre y despejado", CAMPO_ESTADO, 50),
            _campo("senalizacion", "Señalización del recinto", CAMPO_ESTADO, 60),
            _campo("sin_almacenaje", "Sin almacenamiento ajeno", CAMPO_ESTADO, 70,
                   ayuda="Sala de uso exclusivo"),
            _campo("observaciones", "Observaciones generales", CAMPO_TEXTO, 80,
                   con_estado=False),
        ],
    )

    # ---- 2 · Reserva de agua ---------------------------------------------
    _formulario(
        empresa, categoria, "Reserva de agua", 20, tipo_equipo=te_reserva,
        frecuencia="semanal", referencia="NFPA 25 · cap. 8",
        campos=[
            _campo("nivel", "Nivel de agua", CAMPO_NUMERO, 10, unidad="%",
                   ayuda="Lleno según diseño"),
            _campo("estanqueidad", "Estanqueidad del tanque", CAMPO_ESTADO, 20,
                   ayuda="Sin fugas visibles"),
            _campo("llenado", "Válvula de llenado automático", CAMPO_ESTADO, 30),
            _campo("filtro", "Filtro / colador de succión", CAMPO_ESTADO, 40,
                   ayuda="Sin obstrucción"),
            _campo("valvula_succion", "Válvula de succión abierta y trabada",
                   CAMPO_ESTADO, 50, gravedad=GRAVEDAD_CRITICA,
                   ayuda="Punto crítico: cerrada deja el sistema inoperante"),
        ],
    )

    # ---- 3 · Bomba jockey -------------------------------------------------
    _formulario(
        empresa, categoria, "Bomba jockey", 30, tipo_equipo=te_jockey,
        frecuencia="semanal", referencia="NFPA 25 · cap. 8",
        campos=[
            _campo("p_arranque", "Presión de arranque", CAMPO_NUMERO, 10, unidad="psi",
                   ayuda="Por debajo de la presión de arranque de la principal"),
            _campo("p_parada", "Presión de parada", CAMPO_NUMERO, 20, unidad="psi"),
            _campo("encendidos", "Cantidad de encendidos", CAMPO_NUMERO, 30,
                   ayuda="Encendidos excesivos indican fuga en la red"),
            _campo("ruido", "Ruido o vibración anormal", CAMPO_ESTADO, 40),
            _campo("fuga_sello", "Fuga por sello", CAMPO_ESTADO, 50),
        ],
    )

    # ---- 4.1 · Controlador — va antes de su bomba -------------------------
    _formulario(
        empresa, categoria, "Controlador", 41, tipo_equipo=te_ctrl,
        frecuencia="mensual", referencia="NFPA 25 · cap. 8",
        descripcion="Se carga antes que su bomba: si el selector no está en "
                    "AUTOMÁTICO, la prueba de arranque automático no tiene sentido.",
        campos=[
            _campo("selector_auto", "Selector en AUTOMÁTICO", CAMPO_ESTADO, 10,
                   gravedad=GRAVEDAD_CRITICA,
                   ayuda="El punto más crítico de la sala"),
            _campo("lamparas", "Lámparas de señalización", CAMPO_ESTADO, 20,
                   ayuda="Prueba de lámparas"),
            _campo("alarmas", "Alarmas audibles", CAMPO_ESTADO, 30),
            _campo("registrador", "Registrador de presión", CAMPO_ESTADO, 40,
                   ayuda="Con carta o memoria vigente"),
            _campo("transferencia", "Transferencia a fuente de emergencia",
                   CAMPO_ESTADO, 50, ayuda="Si aplica"),
            _campo("contador", "Contador de arranques", CAMPO_NUMERO, 60,
                   con_estado=False, ayuda="Solo registro"),
        ],
    )

    # ---- 4.2a · Bomba eléctrica ------------------------------------------
    _formulario(
        empresa, categoria, "Bomba eléctrica — prueba sin flujo", 42,
        tipo_equipo=te_elec, frecuencia="mensual", referencia="NFPA 25 · cap. 8",
        descripcion="Frecuencia a confirmar contra la edición vigente de NFPA 25.",
        campos=[
            _modo_encendido(10),
            _campo("p_succion", "Presión de succión", CAMPO_NUMERO, 20, unidad="psi",
                   ayuda="Rango esperado según placa del equipo"),
            _campo("p_churn", "Presión de descarga en churn", CAMPO_NUMERO, 30,
                   unidad="psi", ayuda="Comparar con la presión máxima de placa",
                   atributo_equipo="presion_maxima"),
            _campo("p_arranque", "Presión de arranque automático", CAMPO_NUMERO, 40,
                   unidad="psi"),
            _campo("t_arranque", "Tiempo hasta arranque", CAMPO_NUMERO, 50, unidad="s"),
            _campo("t_marcha", "Tiempo de marcha", CAMPO_NUMERO, 60, unidad="min",
                   minimo=10, ayuda="Mín. 10 min"),
            _campo("tension", "Tensión de línea", CAMPO_NUMERO, 70, unidad="V"),
            _campo("corriente", "Corriente", CAMPO_NUMERO, 80, unidad="A"),
            _campo("t_rodamientos", "Temperatura de rodamientos", CAMPO_NUMERO, 90,
                   unidad="°C", maximo=80, gravedad=GRAVEDAD_CRITICA,
                   ayuda="Por encima de 80 °C abre deficiencia crítica"),
            _campo("prensaestopa", "Goteo por prensaestopa", CAMPO_ESTADO, 100,
                   ayuda="Debe gotear levemente, no estar seco"),
            _campo("ruido", "Ruido o vibración anormal", CAMPO_ESTADO, 110),
            _campo("acople", "Alineación del acople", CAMPO_ESTADO, 120),
        ],
    )

    # ---- 4.2b · Bomba diesel ---------------------------------------------
    _formulario(
        empresa, categoria, "Bomba diesel — prueba sin flujo", 42,
        tipo_equipo=te_diesel, frecuencia="semanal", referencia="NFPA 25 · cap. 8",
        campos=[
            _modo_encendido(10),
            _campo("p_succion", "Presión de succión", CAMPO_NUMERO, 20, unidad="psi"),
            _campo("p_churn", "Presión de descarga en churn", CAMPO_NUMERO, 30, unidad="psi",
                   atributo_equipo="presion_maxima"),
            _campo("p_arranque", "Presión de arranque automático", CAMPO_NUMERO, 40, unidad="psi"),
            _campo("t_arranque", "Tiempo hasta arranque", CAMPO_NUMERO, 50, unidad="s",
                   ayuda="Debe arrancar en el primer intento"),
            _campo("t_marcha", "Tiempo de marcha", CAMPO_NUMERO, 60, unidad="min",
                   minimo=30, ayuda="Mín. 30 min"),
            _campo("rpm", "Velocidad", CAMPO_NUMERO, 70, unidad="RPM",
                   ayuda="Contra las RPM nominales de placa", atributo_equipo="rpm_nominal"),
            _campo("p_aceite", "Presión de aceite", CAMPO_NUMERO, 80, unidad="psi"),
            _campo("t_refrigerante", "Temperatura de refrigerante", CAMPO_NUMERO, 90, unidad="°C"),
            _campo("t_rodamientos", "Temperatura de rodamientos", CAMPO_NUMERO, 100,
                   unidad="°C", maximo=80, gravedad=GRAVEDAD_CRITICA,
                   ayuda="Por encima de 80 °C abre deficiencia crítica"),
            _campo("nivel_aceite", "Nivel de aceite", CAMPO_ESTADO, 110),
            _campo("nivel_refrigerante", "Nivel de refrigerante", CAMPO_ESTADO, 120),
            _campo("bateria_a", "Tensión batería A", CAMPO_NUMERO, 130, unidad="V"),
            _campo("bateria_b", "Tensión batería B", CAMPO_NUMERO, 140, unidad="V",
                   ayuda="Sistema redundante"),
            _campo("electrolito", "Nivel de electrolito", CAMPO_ESTADO, 150),
            _campo("escape", "Sistema de escape — fugas", CAMPO_ESTADO, 160),
            _campo("prensaestopa", "Goteo por prensaestopa", CAMPO_ESTADO, 170),
            _campo("ruido", "Ruido o vibración anormal", CAMPO_ESTADO, 180),
        ],
    )

    # ---- 4.3 · Tanque de combustible -------------------------------------
    _formulario(
        empresa, categoria, "Tanque de combustible", 43, tipo_equipo=te_tanque,
        frecuencia="semanal", referencia="NFPA 25 · cap. 8",
        campos=[
            _campo("nivel", "Nivel de combustible", CAMPO_NUMERO, 10, unidad="%",
                   minimo=66, gravedad=GRAVEDAD_CRITICA,
                   ayuda="Mín. 2/3 de capacidad"),
            _campo("agua_sedimentos", "Agua o sedimentos en el fondo", CAMPO_ESTADO, 20,
                   ayuda="Drenar si hay"),
            _campo("fugas", "Fugas en líneas y conexiones", CAMPO_ESTADO, 30),
            _campo("venteo", "Venteo del tanque", CAMPO_ESTADO, 40, ayuda="Libre"),
            _campo("valvula_corte", "Válvula de corte abierta", CAMPO_ESTADO, 50,
                   gravedad=GRAVEDAD_CRITICA,
                   ayuda="Punto crítico: cerrada deja la bomba sin combustible"),
        ],
    )

    # ---- 5 · Prueba anual de caudal — fuera del paquete de rutina --------
    for te_bomba, sufijo in ((te_elec, "eléctrica"), (te_diesel, "diesel")):
        _formulario(
            empresa, categoria, f"Prueba anual de caudal — bomba {sufijo}", 50,
            tipo_equipo=te_bomba, frecuencia="anual", referencia="NFPA 25 · cap. 8",
            en_paquete=False,
            descripcion="Se carga aparte de la inspección de rutina.",
            campos=[
                _campo("metodo", "Método de medición", CAMPO_SELECCION, 10,
                       opciones=["Manifold", "Caudalímetro", "Recirculación"],
                       con_estado=False),
                _campo("churn_succion", "Churn — presión de succión", CAMPO_NUMERO, 20, unidad="psi"),
                _campo("churn_descarga", "Churn — presión de descarga", CAMPO_NUMERO, 30, unidad="psi",
                       atributo_equipo="presion_maxima"),
                _campo("c100_caudal", "100 % — caudal", CAMPO_NUMERO, 40, unidad="GPM",
                       atributo_equipo="caudal_nominal"),
                _campo("c100_succion", "100 % — presión de succión", CAMPO_NUMERO, 50, unidad="psi"),
                _campo("c100_descarga", "100 % — presión de descarga", CAMPO_NUMERO, 60, unidad="psi",
                       atributo_equipo="presion_diseno"),
                _campo("c150_caudal", "150 % — caudal", CAMPO_NUMERO, 70, unidad="GPM"),
                _campo("c150_succion", "150 % — presión de succión", CAMPO_NUMERO, 80, unidad="psi"),
                _campo("c150_descarga", "150 % — presión de descarga", CAMPO_NUMERO, 90,
                       unidad="psi", ayuda="Mín. 65 % de la presión nominal",
                       atributo_equipo="presion_sobrecarga"),
                _campo("rpm", "Velocidad", CAMPO_NUMERO, 100, unidad="RPM",
                       atributo_equipo="rpm_nominal"),
                _campo("curva", "Comparación con curva de fábrica", CAMPO_ESTADO, 110,
                       ayuda="Degradación respecto del ensayo de aceptación"),
            ],
        )

    db.session.commit()
    return categoria


# ---------------------------------------------------------------------------
# Estaciones de control y alarma
# ---------------------------------------------------------------------------

CATEGORIA_ECA = "Estaciones de control y alarma"

T_ECA_HUMEDA = "Estación de control húmeda"
T_ECA_PREACCION = "Estación de control de preacción"
T_ECA_DILUVIO = "Estación de control de diluvio"
T_COMPRESOR = "Compresor de aire"
T_PANEL = "Panel de detección"

# Las tres rutinas de la categoría. Acumulativas: la semestral hace todo lo
# de la mensual, la anual todo lo de las dos.
MES = FRECUENCIA_MENSUAL
SEM = FRECUENCIA_SEMESTRAL
ANU = FRECUENCIA_ANUAL


def sembrar_catalogo_eca(empresa):
    """Categoría de estaciones de control y alarma.

    Tres arquitecturas distintas de estación, cada una con su formulario:

    - **Húmeda**: tubería siempre con agua. Lo que se prueba es el paso de
      agua y que la alarma suene.
    - **Preacción**: el agua no entra hasta que la detección lo ordena. Se
      agrega todo lo que hace de gatillo: aire de supervisión, solenoide,
      enclavamiento y el panel que lo comanda.
    - **Seca**: el aire mantiene cerrada la válvula por diferencial. Se
      agrega el agua de cebado, el dispositivo de apertura rápida y el
      tiempo que tarda el agua en llegar a la conexión de prueba.

    El panel de detección y el compresor se cargan como equipos hijos de su
    estación, así el técnico los recorre en el mismo conjunto. El panel va
    antes de la estación de preacción por el mismo criterio que el
    controlador antes de su bomba: es lo que la comanda.
    """
    ya = CategoriaEquipo.query.filter_by(empresa_id=empresa.id, nombre=CATEGORIA_ECA).first()
    if ya:
        return ya

    categoria = CategoriaEquipo(empresa_id=empresa.id, nombre=CATEGORIA_ECA, orden=20)
    db.session.add(categoria)
    db.session.flush()

    def tipo_equipo(nombre, orden, encabeza=False):
        te = TipoEquipo(
            empresa_id=empresa.id, categoria_id=categoria.id, nombre=nombre,
            orden=orden, encabeza_conjunto=encabeza,
        )
        db.session.add(te)
        db.session.flush()
        return te

    te_humeda = tipo_equipo(T_ECA_HUMEDA, 10, encabeza=True)
    te_preaccion = tipo_equipo(T_ECA_PREACCION, 20, encabeza=True)
    te_diluvio = tipo_equipo(T_ECA_DILUVIO, 30, encabeza=True)
    te_compresor = tipo_equipo(T_COMPRESOR, 40)
    te_panel = tipo_equipo(T_PANEL, 50)

    # Puntos comunes a toda estación, sea del tipo que sea. Se repiten
    # porque cada formulario es independiente, pero salen de un solo lugar
    # para que no se desincronicen entre tipos.
    def comunes_mensuales(desde=10):
        return [
            _campo("valvula_principal", "Válvula principal abierta y trabada",
                   CAMPO_ESTADO, desde, gravedad=GRAVEDAD_CRITICA, frecuencia=MES,
                   ayuda="Punto crítico: cerrada deja el sector sin protección"),
            _campo("p_suministro", "Presión de suministro", CAMPO_NUMERO, desde + 10,
                   unidad="psi", frecuencia=MES, ayuda="Manómetro por debajo de la válvula"),
            _campo("p_sistema", "Presión del sistema", CAMPO_NUMERO, desde + 20,
                   unidad="psi", frecuencia=MES, ayuda="Manómetro por encima de la válvula"),
        ]

    def señales_semestrales(desde=200):
        """Prueba semestral de señales de supervisión y alarma."""
        return [
            _campo("supervision_valvula", "Señal de supervisión de válvula",
                   CAMPO_ESTADO, desde, gravedad=GRAVEDAD_CRITICA, frecuencia=SEM,
                   ayuda="Cerrar parcialmente la válvula debe generar la señal"),
            _campo("alarma_flujo", "Señal de alarma de flujo", CAMPO_ESTADO, desde + 10,
                   frecuencia=SEM),
            _campo("gong", "Alarma hidráulica audible", CAMPO_ESTADO, desde + 20,
                   frecuencia=SEM),
            _campo("monitoreo", "Señales llegan a la central de monitoreo",
                   CAMPO_ESTADO, desde + 30, frecuencia=SEM),
            _campo("manometros", "Manómetros dentro de fecha de calibración",
                   CAMPO_ESTADO, desde + 40, frecuencia=SEM),
            _campo("fugas_trim", "Trim sin fugas", CAMPO_ESTADO, desde + 50, frecuencia=SEM),
        ]

    def purga_anual(desde=300):
        """Prueba anual: se abre la purga del inspector y se cronometra toda
        la cadena — arranque de bomba, respuesta de alarma y restablecimiento."""
        return [
            _campo("purga_abierta", "Purga del inspector abierta", CAMPO_ESTADO, desde,
                   frecuencia=ANU, ayuda="Descarga equivalente al rociador más chico"),
            _campo("t_bomba", "Tiempo de arranque de la bomba principal",
                   CAMPO_NUMERO, desde + 10, unidad="s", frecuencia=ANU),
            _campo("t_alarma", "Tiempo de respuesta de la alarma", CAMPO_NUMERO, desde + 20,
                   unidad="s", maximo=90, frecuencia=ANU,
                   ayuda="Umbral a confirmar contra la edición vigente"),
            _campo("restablecimiento_alarma", "Restablecimiento de la alarma",
                   CAMPO_ESTADO, desde + 30, frecuencia=ANU),
            _campo("restablecimiento_bomba", "Restablecimiento de la bomba",
                   CAMPO_ESTADO, desde + 40, frecuencia=ANU),
            _campo("drenaje_estatica", "Drenaje principal — presión estática",
                   CAMPO_NUMERO, desde + 50, unidad="psi", frecuencia=ANU),
            _campo("drenaje_residual", "Drenaje principal — presión residual",
                   CAMPO_NUMERO, desde + 60, unidad="psi", frecuencia=ANU,
                   ayuda="Una caída mayor a la histórica sugiere obstrucción"),
            _campo("purga_cerrada", "Purga y drenajes cerrados al terminar",
                   CAMPO_ESTADO, desde + 70, gravedad=GRAVEDAD_CRITICA, frecuencia=ANU),
            _campo("reposicion", "Sistema repuesto y en servicio",
                   CAMPO_ESTADO, desde + 80, gravedad=GRAVEDAD_CRITICA, frecuencia=ANU,
                   ayuda="Punto crítico: sin reponer, el sector queda sin protección"),
        ]

    # ---- 1 · Cuarto de válvulas (sala completa) --------------------------
    _formulario(
        empresa, categoria, "Condiciones del cuarto de válvulas", 10,
        por_equipo=False, frecuencia=MES, referencia="NFPA 25 · cap. 5 y 13",
        descripcion="Se carga una vez para todo el cuarto de válvulas.",
        campos=[
            _campo("temperatura", "Temperatura del local", CAMPO_NUMERO, 10,
                   unidad="°C", minimo=4, gravedad=GRAVEDAD_CRITICA, frecuencia=MES,
                   ayuda="Mín. 4 °C — por debajo congela el agua de cebado"),
            _campo("acceso", "Acceso libre y despejado", CAMPO_ESTADO, 20, frecuencia=MES),
            _campo("identificacion", "Identificación de zonas y carteles", CAMPO_ESTADO, 30,
                   frecuencia=MES, ayuda="Cada estación rotulada con el área que cubre"),
            _campo("observaciones", "Observaciones generales", CAMPO_TEXTO, 40,
                   con_estado=False, frecuencia=MES),
            _campo("iluminacion", "Iluminación", CAMPO_ESTADO, 210, frecuencia=SEM),
            _campo("planos", "Planos hidráulicos disponibles", CAMPO_ESTADO, 220,
                   frecuencia=SEM),
            _campo("drenaje", "Drenaje del piso", CAMPO_ESTADO, 230, frecuencia=SEM,
                   ayuda="Con capacidad para el caudal del drenaje principal"),
        ],
    )

    # ---- 2 · Estación húmeda ---------------------------------------------
    _formulario(
        empresa, categoria, "Estación de control húmeda", 20,
        tipo_equipo=te_humeda, frecuencia=MES, referencia="NFPA 25 · cap. 5 y 13",
        descripcion="Tubería permanentemente con agua. Mensual: posición de "
                    "válvulas y presiones. Semestral: señales. Anual: purga del "
                    "inspector con tiempos.",
        campos=comunes_mensuales()
        + [
            _campo("camara_retardo", "Cámara de retardo drena libremente",
                   CAMPO_ESTADO, 210, frecuencia=SEM),
        ]
        + señales_semestrales()
        + purga_anual(),
    )

    # ---- 3 · Panel de detección — antes de la estación de preacción ------
    _formulario(
        empresa, categoria, "Panel de detección", 25,
        tipo_equipo=te_panel, frecuencia="trimestral", referencia="NFPA 25 · cap. 13",
        descripcion="Se carga antes de su estación: es lo que ordena el disparo, "
                    "igual que el controlador respecto de su bomba.",
        campos=[
            _campo("sin_averias", "Panel sin averías ni señales de falla",
                   CAMPO_ESTADO, 10, gravedad=GRAVEDAD_CRITICA, frecuencia=MES),
            _campo("bateria", "Tensión de batería de respaldo", CAMPO_NUMERO, 20,
                   unidad="V", frecuencia=SEM),
            _campo("detectores", "Prueba de detectores de la zona", CAMPO_ESTADO, 30,
                   frecuencia=SEM),
            _campo("monitoreo", "Señal a central de monitoreo", CAMPO_ESTADO, 40,
                   frecuencia=SEM),
            _campo("enclavamiento", "Enclavamiento con la estación verificado",
                   CAMPO_ESTADO, 50, gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
        ],
    )

    # ---- 4 · Estación de preacción ---------------------------------------
    _formulario(
        empresa, categoria, "Estación de control de preacción", 30,
        tipo_equipo=te_preaccion, frecuencia="trimestral", referencia="NFPA 25 · cap. 13",
        descripcion="El agua no entra a la red hasta que la detección lo ordena. "
                    "Se verifica toda la cadena de disparo.",
        campos=[
            _campo("enclavamiento", "Tipo de enclavamiento", CAMPO_SELECCION, 5,
                   opciones=["Simple", "Doble", "No enclavado"], frecuencia=MES,
                   ayuda="Determina qué debe ocurrir para que entre el agua"),
        ]
        + comunes_mensuales(10)
        + [
            # Lo propio del tipo, que también se registra todos los meses.
            _campo("p_enclavamiento", "Presión de la cámara de enclavamiento",
                   CAMPO_NUMERO, 40, unidad="psi", frecuencia=MES,
                   gravedad=GRAVEDAD_CRITICA,
                   ayuda="Es lo que mantiene la válvula cerrada"),
            _campo("p_supervision", "Presión de aire de supervisión", CAMPO_NUMERO, 50,
                   unidad="psi", frecuencia=MES,
                   ayuda="Contra el valor de diseño de la estación"),
            _campo("agua_cebado", "Nivel de agua de cebado", CAMPO_ESTADO, 210,
                   gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
            _campo("trim", "Válvulas de trim en posición correcta", CAMPO_ESTADO, 215,
                   frecuencia=SEM),
            _campo("solenoide", "Solenoide acciona al recibir la señal",
                   CAMPO_ESTADO, 220, gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
            _campo("estacion_manual", "Estación manual de disparo", CAMPO_ESTADO, 225,
                   frecuencia=SEM),
            _campo("baja_presion", "Alarma de baja presión de aire — presión de disparo",
                   CAMPO_NUMERO, 230, unidad="psi", frecuencia=SEM),
        ]
        + señales_semestrales(240)
        + [
            _campo("tiempo_disparo", "Tiempo hasta la apertura de la válvula",
                   CAMPO_NUMERO, 305, unidad="s", frecuencia=ANU),
        ]
        + purga_anual(),
    )

    # ---- 5 · Estación de diluvio ------------------------------------------
    _formulario(
        empresa, categoria, "Estación de control de diluvio", 40,
        tipo_equipo=te_diluvio, frecuencia=MES, referencia="NFPA 25 · cap. 13",
        descripcion="Válvula de diafragma con rociadores abiertos: al disparar "
                    "moja todo el sector. La presión de la cámara del diafragma "
                    "es lo que la mantiene cerrada.",
        campos=comunes_mensuales()
        + [
            # Lo propio del tipo, que también se registra todos los meses.
            _campo("p_diafragma", "Presión de la cámara del diafragma",
                   CAMPO_NUMERO, 40, unidad="psi", frecuencia=MES,
                   gravedad=GRAVEDAD_CRITICA,
                   ayuda="Es lo que mantiene la válvula cerrada: si cae, dispara"),
            _campo("agua_cebado", "Nivel de agua de cebado", CAMPO_ESTADO, 210,
                   gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
            _campo("trim", "Válvulas de trim en posición correcta", CAMPO_ESTADO, 215,
                   frecuencia=SEM),
            _campo("linea_piloto", "Línea piloto de actuación", CAMPO_ESTADO, 220,
                   gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
            _campo("solenoide", "Solenoide acciona al recibir la señal",
                   CAMPO_ESTADO, 225, gravedad=GRAVEDAD_CRITICA, frecuencia=SEM),
            _campo("estacion_manual", "Estación manual de disparo", CAMPO_ESTADO, 230,
                   frecuencia=SEM),
            _campo("rociadores_abiertos", "Rociadores abiertos libres de obstrucción",
                   CAMPO_ESTADO, 235, frecuencia=SEM,
                   ayuda="Sin pintura ni tapones: la descarga cubre todo el sector"),
        ]
        + señales_semestrales(240)
        + [
            _campo("tiempo_disparo", "Tiempo hasta la apertura de la válvula",
                   CAMPO_NUMERO, 305, unidad="s", frecuencia=ANU),
            _campo("tiempo_agua", "Tiempo de llegada de agua al punto más lejano",
                   CAMPO_NUMERO, 306, unidad="s", maximo=60, gravedad=GRAVEDAD_CRITICA,
                   frecuencia=ANU, ayuda="Umbral a confirmar según el volumen del sistema"),
        ]
        + purga_anual(),
    )

    # ---- 6 · Compresor de aire — cuelga de la estación de preacción ------
    _formulario(
        empresa, categoria, "Compresor de aire", 45,
        tipo_equipo=te_compresor, frecuencia=MES, referencia="NFPA 25 · cap. 13",
        campos=[
            _campo("p_arranque", "Presión de arranque", CAMPO_NUMERO, 10, unidad="psi",
                   frecuencia=MES),
            _campo("p_parada", "Presión de parada", CAMPO_NUMERO, 20, unidad="psi",
                   frecuencia=MES),
            _campo("presostato", "Presostato de control opera", CAMPO_ESTADO, 210,
                   frecuencia=SEM),
            _campo("filtro", "Filtro de aire", CAMPO_ESTADO, 220, frecuencia=SEM),
            _campo("fugas", "Línea de aire sin fugas", CAMPO_ESTADO, 230, frecuencia=SEM,
                   ayuda="Un compresor que arranca seguido delata una fuga"),
            _campo("restablecimiento", "Tiempo de restablecimiento de presión normal",
                   CAMPO_NUMERO, 310, unidad="min", maximo=30, frecuencia=ANU,
                   ayuda="Máx. 30 min — umbral a confirmar contra la edición vigente"),
        ],
    )

    db.session.commit()
    return categoria


# ---------------------------------------------------------------------------
# Datos de demostración
# ---------------------------------------------------------------------------


def sembrar_demo():
    """Empresa, usuarios y tres salas de bombas distintas.

    Las tres salas existen para mostrar que el mismo paquete produce
    checklists distintos según el inventario, sin ninguna condicional en
    el código.
    """
    existente = Empresa.query.first()
    if existente:
        return existente

    empresa = Empresa(nombre="IPM Uruguay", rut="21 000 000 0011")
    db.session.add(empresa)
    db.session.flush()

    jefe = Usuario(empresa_id=empresa.id, username="jefe",
                   nombre_completo="M. Rodríguez", rol="Jefe técnico")
    jefe.set_password("jefe")
    tecnico = Usuario(empresa_id=empresa.id, username="tecnico",
                      nombre_completo="J. Silva", rol="Técnico")
    tecnico.set_password("tecnico")
    db.session.add_all([jefe, tecnico])
    db.session.flush()

    sembrar_catalogo(empresa)
    sembrar_catalogo_eca(empresa)

    def te(nombre):
        return TipoEquipo.query.filter_by(empresa_id=empresa.id, nombre=nombre).one()

    def instalacion(nombre_cliente, nombre_inst):
        cliente = Cliente(empresa_id=empresa.id, nombre=nombre_cliente)
        db.session.add(cliente)
        db.session.flush()
        inst = Instalacion(cliente_id=cliente.id, nombre=nombre_inst)
        db.session.add(inst)
        db.session.flush()
        return inst

    def equipo(inst, tipo, codigo, nombre, ubicacion=None, padre=None, **placa):
        eq = Equipo(
            instalacion_id=inst.id, tipo_equipo_id=tipo.id, codigo=codigo,
            nombre=nombre, ubicacion=ubicacion,
            padre_id=padre.id if padre else None, **placa
        )
        db.session.add(eq)
        db.session.flush()
        return eq

    # --- Sala A: 1 eléctrica + su controlador + jockey, red pública -------
    a = instalacion("Torre Ejecutiva", "Sala de bombas")
    ba = equipo(a, te(T_BOMBA_ELEC), "BP-01", "Bomba principal eléctrica",
                "Subsuelo 1", caudal_nominal=500, presion_diseno=100,
                presion_maxima=120, presion_sobrecarga=65, rpm_nominal=3500)
    equipo(a, te(T_CONTROLADOR), "CTRL-01", "Controlador eléctrico", padre=ba)
    equipo(a, te(T_JOCKEY), "JK-01", "Bomba jockey", "Subsuelo 1")

    # --- Sala B: 1 diesel + controlador + tanque + jockey + reserva -------
    b = instalacion("Planta Industrial Norte", "Sala de bombas")
    bb = equipo(b, te(T_BOMBA_DIESEL), "BP-01", "Bomba principal diesel",
                "Sala técnica", caudal_nominal=750, presion_diseno=110,
                presion_maxima=135, presion_sobrecarga=72, rpm_nominal=2100)
    equipo(b, te(T_CONTROLADOR), "CTRL-01", "Controlador diesel", padre=bb)
    equipo(b, te(T_TANQUE), "TQ-01", "Tanque de combustible", padre=bb)
    equipo(b, te(T_JOCKEY), "JK-01", "Bomba jockey", "Sala técnica")
    equipo(b, te(T_RESERVA), "RES-01", "Reserva de agua", "Exterior")

    # --- Sala C: 2 eléctricas + 1 diesel, cada una con su controlador ----
    c = instalacion("Hospital Central", "Sala de bombas")
    c1 = equipo(c, te(T_BOMBA_ELEC), "BP-01", "Bomba principal eléctrica 1",
                "Nivel -2", caudal_nominal=1000, presion_diseno=115,
                presion_maxima=140, presion_sobrecarga=75, rpm_nominal=3500)
    equipo(c, te(T_CONTROLADOR), "CTRL-01", "Controlador eléctrico 1", padre=c1)
    c2 = equipo(c, te(T_BOMBA_ELEC), "BP-02", "Bomba principal eléctrica 2",
                "Nivel -2", caudal_nominal=1000, presion_diseno=115,
                presion_maxima=140, presion_sobrecarga=75, rpm_nominal=3500)
    equipo(c, te(T_CONTROLADOR), "CTRL-02", "Controlador eléctrico 2", padre=c2)
    c3 = equipo(c, te(T_BOMBA_DIESEL), "BP-03", "Bomba principal diesel",
                "Nivel -2", caudal_nominal=1000, presion_diseno=115,
                presion_maxima=140, presion_sobrecarga=75, rpm_nominal=2100)
    equipo(c, te(T_CONTROLADOR), "CTRL-03", "Controlador diesel", padre=c3)
    equipo(c, te(T_TANQUE), "TQ-01", "Tanque de combustible", padre=c3)
    equipo(c, te(T_JOCKEY), "JK-01", "Bomba jockey", "Nivel -2")
    equipo(c, te(T_RESERVA), "RES-01", "Reserva de agua", "Azotea")

    # --- Sala de válvulas con las tres arquitecturas de estación ---------
    # Muestra que el mismo paquete se adapta: la húmeda va sola, la de
    # preacción arrastra su panel de detección, y la seca su compresor.
    d = instalacion("Centro Comercial Sur", "Cuarto de válvulas")
    equipo(d, te(T_ECA_HUMEDA), "ECA-01", "Estación húmeda — locales planta baja",
           "Cuarto de válvulas")
    pre = equipo(d, te(T_ECA_PREACCION), "ECA-02", "Estación de preacción — sala de servidores",
                 "Cuarto de válvulas")
    equipo(d, te(T_PANEL), "PD-01", "Panel de detección — sala de servidores", padre=pre)
    equipo(d, te(T_COMPRESOR), "CP-01", "Compresor de aire de supervisión", padre=pre)
    dil = equipo(d, te(T_ECA_DILUVIO), "ECA-03", "Estación de diluvio — playa de carga",
                 "Cuarto de válvulas")
    equipo(d, te(T_PANEL), "PD-02", "Panel de detección — playa de carga", padre=dil)

    # --- contratos: es lo que hace predecible el calendario ---
    # Sin contrato, cada inspección hay que arrancarla a mano.
    def contratar(inst, *categorias_y_anclas):
        ct = Contrato(instalacion_id=inst.id, desde=date(date.today().year, 1, 1))
        db.session.add(ct)
        db.session.flush()
        for nombre_cat, ancla in categorias_y_anclas:
            cat = CategoriaEquipo.query.filter_by(
                empresa_id=empresa.id, nombre=nombre_cat).one()
            db.session.add(ServicioContrato(
                contrato_id=ct.id, categoria_id=cat.id, mes_ancla=ancla))
        return ct

    contratar(a, (CATEGORIA, 1))
    contratar(b, (CATEGORIA, 2))
    contratar(c, (CATEGORIA, 1))
    contratar(d, (CATEGORIA_ECA, 3))

    db.session.commit()
    return empresa
