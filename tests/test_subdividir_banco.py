"""
Tests de la subdivisión de la contrapartida en el módulo Bancos.

El movimiento bancario permanece SIEMPRE por un solo valor (una línea), pero la
contrapartida puede repartirse en varias cuentas con importes (y terceros)
distintos que sumen el valor del movimiento. El banco no se subdivide.
"""

from datetime import date
from decimal import Decimal

from app.banco.importador_banco import MovimientoBanco
from app.banco.mapeador_banco import mapear_banco_a_siigo


def _mov(idx, valor, **kw):
    base = dict(
        idx=idx,
        cuenta_banco_num="551-000068-95",
        codigo_banco="551",
        fecha=date(2026, 3, 15),
        valor=Decimal(str(valor)),
        codigo_detalle="2999",
        descripcion="PAGO PROVEEDORES",
    )
    base.update(kw)
    return MovimientoBanco(**base)


def test_sin_subdivision_una_contrapartida():
    """Comportamiento histórico: 1 línea banco + 1 línea contrapartida."""
    movs = [_mov(0, -1000000)]
    asigs = [{"idx": 0, "cuenta_contrapartida": "51050501", "nit_tercero": "800123456"}]
    filas = mapear_banco_a_siigo(movs, "11100501", asigs)

    assert len(filas) == 2
    banco, contra = filas
    assert banco.codigo_cuenta == "11100501"
    assert banco.credito == 1000000.0 and banco.debito == 0.0
    assert contra.codigo_cuenta == "51050501"
    assert contra.debito == 1000000.0 and contra.credito == 0.0
    # Mismo asiento (consecutivo) y cuadrado
    assert banco.consecutivo_comprobante == contra.consecutivo_comprobante
    assert sum(f.debito for f in filas) == sum(f.credito for f in filas)


def test_egreso_subdividido_en_varias_cuentas():
    """Egreso: banco crédito por el total; contrapartida débito repartida."""
    movs = [_mov(0, -1000000)]
    asigs = [{
        "idx": 0,
        "nit_tercero": "800123456",
        "contrapartidas": [
            {"cuenta": "51050501", "monto": 600000, "concepto": "Arriendo"},
            {"cuenta": "53050501", "monto": 400000, "nit_tercero": "900999"},
        ],
    }]
    filas = mapear_banco_a_siigo(movs, "11100501", asigs)

    assert len(filas) == 3
    banco, *contras = filas
    # Banco: una sola línea por el total, al crédito (egreso)
    assert banco.codigo_cuenta == "11100501"
    assert banco.credito == 1000000.0 and banco.debito == 0.0
    # Contrapartidas: al débito, suman el total
    assert all(c.credito == 0.0 for c in contras)
    assert sum(c.debito for c in contras) == 1000000.0
    assert {c.codigo_cuenta for c in contras} == {"51050501", "53050501"}
    # Mismo asiento y cuadre exacto
    assert len({f.consecutivo_comprobante for f in filas}) == 1
    assert sum(f.debito for f in filas) == sum(f.credito for f in filas)
    # NIT por parte: una hereda el del movimiento, otra fija el suyo
    nit_por_cuenta = {c.codigo_cuenta: c.nit_tercero for c in contras}
    assert nit_por_cuenta["51050501"] == "800123456"
    assert nit_por_cuenta["53050501"] == "900999"
    # Concepto propio en la primera parte; la segunda hereda la descripción
    desc_por_cuenta = {c.codigo_cuenta: c.descripcion for c in contras}
    assert desc_por_cuenta["51050501"] == "Arriendo"
    assert desc_por_cuenta["53050501"] == "PAGO PROVEEDORES"


def test_ingreso_subdividido_en_varias_cuentas():
    """Ingreso: banco débito por el total; contrapartida crédito repartida."""
    movs = [_mov(0, 500000, descripcion="ABONO CLIENTES")]
    asigs = [{
        "idx": 0,
        "nit_tercero": "800123456",
        "contrapartidas": [
            {"cuenta": "41350501", "monto": 300000},
            {"cuenta": "41350502", "monto": 200000},
        ],
    }]
    filas = mapear_banco_a_siigo(movs, "11100501", asigs)

    assert len(filas) == 3
    banco, *contras = filas
    assert banco.debito == 500000.0 and banco.credito == 0.0
    assert all(c.debito == 0.0 for c in contras)
    assert sum(c.credito for c in contras) == 500000.0
    assert sum(f.debito for f in filas) == sum(f.credito for f in filas)


def test_subdivision_convive_con_4x1000():
    """La subdivisión de la contrapartida no afecta a las líneas 4x1000."""
    padre = _mov(0, -1000000)
    imp = _mov(1, -4000, codigo_detalle="3339", es_4x1000=True, idx_padre=0,
               descripcion="IMPTO GOBIERNO 4X1000")
    asigs = [{
        "idx": 0,
        "nit_tercero": "800123456",
        "contrapartidas": [
            {"cuenta": "51050501", "monto": 600000},
            {"cuenta": "53050501", "monto": 400000},
        ],
    }]
    filas = mapear_banco_a_siigo([padre, imp], "11100501", asigs, nit_banco="860000")

    # 1 banco + 2 contrapartidas + 2 del 4x1000 = 5 filas, todas en el mismo asiento
    assert len(filas) == 5
    assert len({f.consecutivo_comprobante for f in filas}) == 1
    assert round(sum(f.debito for f in filas), 2) == round(sum(f.credito for f in filas), 2)


def test_partes_vacias_se_ignoran_y_no_descuadran():
    """Una parte sin cuenta ni monto se descarta; el asiento sigue cuadrando."""
    movs = [_mov(0, -1000000)]
    asigs = [{
        "idx": 0,
        "nit_tercero": "800123456",
        "contrapartidas": [
            {"cuenta": "51050501", "monto": 600000},
            {"cuenta": "", "monto": 0},          # fila vacía → ignorada
            {"cuenta": "53050501", "monto": 400000},
        ],
    }]
    filas = mapear_banco_a_siigo(movs, "11100501", asigs)

    assert len(filas) == 3   # banco + 2 contrapartidas reales
    assert sum(f.debito for f in filas) == sum(f.credito for f in filas)
