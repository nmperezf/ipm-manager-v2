"""Inventario de repuestos: catálogo por empresa y su consumo por OT.

Igual que en el original: el stock solo sube por "Reponer" (compra manual) y
solo baja por consumo registrado desde una OT. No hay integración con
compras/proveedores — es inventario de cantidades, no de costos.
"""

from app.models import ConsumoRepuesto, Repuesto, db


class StockInsuficiente(Exception):
    pass


def registrar_consumo(ot, repuesto, cantidad):
    """Descuenta stock y deja la fila de consumo. No hace commit."""
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor a cero.")
    if cantidad > repuesto.stock_actual:
        raise StockInsuficiente(
            f"Quedan {repuesto.stock_actual} {repuesto.unidad}(s) de «{repuesto.nombre}»."
        )
    repuesto.stock_actual -= cantidad
    consumo = ConsumoRepuesto(ot_id=ot.id, repuesto_id=repuesto.id, cantidad=cantidad)
    db.session.add(consumo)
    return consumo


def reponer_stock(repuesto, cantidad):
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor a cero.")
    repuesto.stock_actual += cantidad


def repuestos_criticos(empresa_id, limite=None):
    query = (
        Repuesto.query.filter(
            Repuesto.empresa_id == empresa_id,
            Repuesto.activo.is_(True),
            Repuesto.stock_actual <= Repuesto.stock_minimo,
        ).order_by(Repuesto.nombre)
    )
    return query.limit(limite).all() if limite else query.all()
