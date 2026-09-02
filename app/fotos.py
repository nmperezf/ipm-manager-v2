"""Guardado de fotos del banco.

Una foto de tablet moderna pesa entre 3 y 6 MB. Guardarlas tal cual haría
que el banco crezca varios GB por año y que el listado tarde una eternidad
sobre la conexión de una sala de bombas. Por eso todas se reescalan a
`LADO_MAX` y se reescriben como JPEG antes de tocar el disco.

Se reescribe la imagen en vez de confiar en la extensión: abrirla con
Pillow y volver a guardarla descarta cualquier cosa que no sea realmente
una imagen, y de paso saca los metadatos EXIF (que incluyen ubicación GPS).
"""

import os
import uuid

from PIL import Image, ImageOps

# Lo que ofrece un <input accept="image/*"> en una tablet.
EXTENSIONES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
MAX_BYTES = 12 * 1024 * 1024      # antes de reescalar
LADO_MAX = 1600                   # suficiente para leer un manómetro
CALIDAD = 82


class FotoInvalida(Exception):
    """El archivo no sirve. El mensaje va tal cual al usuario."""


def _carpeta(app, empresa_id):
    ruta = os.path.join(app.static_folder, "uploads", str(empresa_id))
    os.makedirs(ruta, exist_ok=True)
    return ruta


def guardar_archivo(app, archivo, empresa_id):
    """Valida, reescala y escribe. Devuelve (nombre, ancho, alto, bytes)."""
    if not archivo or not archivo.filename:
        raise FotoInvalida("No llegó ningún archivo.")

    extension = os.path.splitext(archivo.filename)[1].lower()
    if extension not in EXTENSIONES:
        raise FotoInvalida(
            f"«{extension or 'sin extensión'}» no es un formato de imagen aceptado."
        )

    archivo.stream.seek(0, os.SEEK_END)
    tamano = archivo.stream.tell()
    archivo.stream.seek(0)
    if tamano > MAX_BYTES:
        raise FotoInvalida(
            f"La imagen pesa {tamano // (1024 * 1024)} MB y el máximo es "
            f"{MAX_BYTES // (1024 * 1024)} MB."
        )

    try:
        imagen = Image.open(archivo.stream)
        # exif_transpose respeta la orientación con la que se sacó: sin
        # esto las fotos de tablet en vertical se guardan acostadas.
        imagen = ImageOps.exif_transpose(imagen)
        imagen = imagen.convert("RGB")
    except Exception as error:  # noqa: BLE001 — cualquier fallo es "no es imagen"
        raise FotoInvalida("El archivo no se pudo abrir como imagen.") from error

    imagen.thumbnail((LADO_MAX, LADO_MAX), Image.LANCZOS)

    nombre = f"{uuid.uuid4().hex}.jpg"
    destino = os.path.join(_carpeta(app, empresa_id), nombre)
    imagen.save(destino, "JPEG", quality=CALIDAD, optimize=True)

    return nombre, imagen.width, imagen.height, os.path.getsize(destino)


def ruta_relativa(empresa_id, archivo):
    """Ruta para url_for('static', filename=...)."""
    return f"uploads/{empresa_id}/{archivo}"


def borrar_archivo(app, empresa_id, archivo):
    """Borra el archivo del disco. Que no esté no es un error: la fila de
    la base es la fuente de verdad, el archivo es una consecuencia."""
    ruta = os.path.join(_carpeta(app, empresa_id), archivo)
    try:
        os.remove(ruta)
    except OSError:
        pass
