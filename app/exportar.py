"""Exportación de listados a CSV.

Un solo helper genérico: cada ruta arma sus propios encabezados y filas
respetando los filtros que ya tenía la pantalla (mismo query, sin límite),
y esto solo se encarga de la respuesta HTTP.
"""

import csv
import io

from flask import Response


def csv_response(nombre_archivo, encabezados, filas):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(encabezados)
    writer.writerows(filas)

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )
