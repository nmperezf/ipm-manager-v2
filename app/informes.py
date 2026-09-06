"""Informe de visita en PDF.

Un documento por visita: datos de instalación/cliente/técnico, el checklist
cargado (destacando lo fuera de rango), las deficiencias abiertas, y las
firmas de cierre si existen. Usa fpdf2 (puro Python, sin dependencias de
sistema) para no complicar el build en Railway.
"""

import os

from fpdf import FPDF

from app.models import ESTADO_NO_CONFORME


def _ruta_estatica(app, relativa):
    if not relativa:
        return None
    ruta = os.path.join(app.static_folder, relativa)
    return ruta if os.path.isfile(ruta) else None


def _latin1(texto):
    """La fuente core Helvetica de fpdf2 solo soporta latin-1. Un técnico
    puede escribir cualquier cosa en un comentario (emoji, comillas
    tipográficas, etc.) — mejor perder ese carácter puntual que romper la
    descarga del informe entero."""
    if texto is None:
        return texto
    return str(texto).encode("latin-1", "replace").decode("latin-1")


class _InformePDF(FPDF):
    def cell(self, *args, **kwargs):
        if "text" in kwargs:
            kwargs["text"] = _latin1(kwargs["text"])
        elif len(args) >= 3:
            args = list(args)
            args[2] = _latin1(args[2])
        return super().cell(*args, **kwargs)

    def multi_cell(self, *args, **kwargs):
        if "text" in kwargs:
            kwargs["text"] = _latin1(kwargs["text"])
        elif len(args) >= 3:
            args = list(args)
            args[2] = _latin1(args[2])
        return super().multi_cell(*args, **kwargs)


def generar_informe_visita(app, visita):
    """Devuelve los bytes del PDF."""
    inst = visita.instalacion
    cliente = inst.cliente
    empresa = cliente.empresa

    pdf = _InformePDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    logo = _ruta_estatica(app, empresa.logo)
    if logo:
        pdf.image(logo, x=10, y=8, h=16)
        pdf.set_xy(30, 8)
    else:
        pdf.set_xy(10, 8)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, empresa.nombre, ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(10)
    pdf.cell(0, 10, "Informe de visita - mantenimiento de sistemas contra incendio", ln=1)
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 10)
    filas = [
        ("Cliente", cliente.nombre),
        ("Instalación", inst.nombre),
        ("Fecha de visita", visita.fecha.strftime("%d/%m/%Y")),
        ("Técnico", visita.tecnico.nombre_completo if visita.tecnico else "sin asignar"),
        ("Visita N°", str(visita.id)),
    ]
    for etiqueta, valor in filas:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(40, 6, f"{etiqueta}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, valor, ln=1)
    if visita.notas:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(0, 5, f"Notas: {visita.notas}")

    for item in visita.items:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(0, 7, f"{item.categoria.nombre} - rutina {item.rutina}", ln=1, fill=True)

        for formulario in item.formularios:
            equipo_txt = f" · {formulario.equipo.etiqueta}" if formulario.equipo else ""
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 6, f"{formulario.tipo_formulario.nombre}{equipo_txt}", ln=1)

            pdf.set_font("Helvetica", "", 8)
            for respuesta in formulario.respuestas:
                campo = respuesta.campo
                fuera = respuesta.estado == ESTADO_NO_CONFORME
                valor = respuesta.valor
                texto = f"  {campo.label}: {valor if valor is not None else '-'}"
                if campo.unidad and respuesta.valor_numero is not None:
                    texto += f" {campo.unidad}"
                if fuera:
                    pdf.set_text_color(200, 30, 10)
                pdf.multi_cell(0, 5, texto)
                pdf.set_text_color(0, 0, 0)

    deficiencias = [o for o in visita.observaciones if o.clasificacion != "Comentario"]
    if deficiencias:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(0, 7, "Deficiencias", ln=1, fill=True)
        pdf.set_font("Helvetica", "", 9)
        for obs in deficiencias:
            if obs.es_critica:
                pdf.set_text_color(200, 30, 10)
            pdf.multi_cell(0, 5, f"[{obs.clasificacion}] {obs.descripcion}")
            pdf.set_text_color(0, 0, 0)

    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Firmas", ln=1)

    y_firmas = pdf.get_y()
    ancho_firma = 85
    firma_tecnico = (
        _ruta_estatica(app, f"uploads/{empresa.id}/{visita.firma_tecnico_archivo}")
        if visita.firma_tecnico_archivo else None
    )
    firma_cliente = (
        _ruta_estatica(app, f"uploads/{empresa.id}/{visita.firma_cliente_archivo}")
        if visita.firma_cliente_archivo else None
    )

    pdf.set_font("Helvetica", "", 8)
    if firma_tecnico:
        pdf.image(firma_tecnico, x=10, y=y_firmas, w=ancho_firma, h=25)
    else:
        pdf.set_xy(10, y_firmas + 10)
        pdf.cell(ancho_firma, 5, "Sin firmar")
    pdf.set_xy(10, y_firmas + 26)
    pdf.cell(ancho_firma, 5, f"Técnico: {visita.tecnico.nombre_completo if visita.tecnico else '-'}", border="T")

    if firma_cliente:
        pdf.image(firma_cliente, x=105, y=y_firmas, w=ancho_firma, h=25)
    else:
        pdf.set_xy(105, y_firmas + 10)
        pdf.cell(ancho_firma, 5, "Sin firmar")
    pdf.set_xy(105, y_firmas + 26)
    pdf.cell(ancho_firma, 5, f"Cliente: {visita.firma_cliente_nombre or '-'}", border="T")

    return bytes(pdf.output())
