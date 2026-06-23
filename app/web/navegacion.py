"""
Catálogo de navegación por categorías.

El menú lateral llega solo hasta la **categoría** (el submenú: «Flujos directos
de efectivo», «Empresas», …). Cada categoría abre una página propia que agrupa,
como botones, los **módulos** que la componen (Bancos, Caja general, …).

Aquí se define ese catálogo. Cada módulo tiene:
- ``nombre`` / ``descripcion``: texto del botón.
- ``endpoint``: endpoint Flask del módulo, o ``None`` si todavía no está
  disponible (se muestra como «Próximamente», deshabilitado).
- ``icono``: interior del ``<svg>`` (paths); la plantilla lo envuelve con un
  ``<svg>`` de estilo uniforme.

El orden de las claves coincide con el orden de los submenús en el sidebar.
"""

from __future__ import annotations

CATEGORIAS: dict[str, dict] = {
    # ===== Automatizaciones =====
    "flujos-indirectos": {
        "titulo": "Flujos indirectos de efectivo",
        "subtitulo": "Automatizaciones de documentos que no mueven efectivo de forma directa.",
        "modulos": [
            {
                "nombre": "Nómina",
                "descripcion": "Causación y dispersión de nómina",
                "endpoint": None,
                "icono": '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3 3-5 6-5s6 2 6 5"/>'
                         '<circle cx="17" cy="8" r="2.5"/><path d="M16 15c3 0 5 2 5 5"/>',
            },
            {
                "nombre": "RADIAN",
                "descripcion": "Procesa el reporte RADIAN de la DIAN",
                "endpoint": "web.radian",
                "icono": '<circle cx="12" cy="12" r="9"/>'
                         '<path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/>',
            },
        ],
    },
    "flujos-directos": {
        "titulo": "Flujos directos de efectivo",
        "subtitulo": "Automatizaciones de movimientos que afectan el efectivo directamente.",
        "modulos": [
            {
                "nombre": "Bancos",
                "descripcion": "Concilia y exporta extractos bancarios",
                "endpoint": "web.banco",
                "icono": '<path d="M3 21h18"/><path d="M5 21V9l7-5 7 5v12"/><path d="M9 21v-6h6v6"/>',
            },
            {
                "nombre": "Caja general",
                "descripcion": "Movimientos de caja menor y general",
                "endpoint": None,
                "icono": '<rect x="2" y="6" width="20" height="13" rx="2"/>'
                         '<path d="M2 10h20"/><circle cx="16" cy="14" r="1.4"/>',
            },
            {
                "nombre": "Cruces de saldos",
                "descripcion": "Conciliación y cruce de saldos entre cuentas",
                "endpoint": None,
                "icono": '<path d="M4 7h13l-3-3"/><path d="M20 17H7l3 3"/>',
            },
        ],
    },
    "tributario-fiscal": {
        "titulo": "Tributario y fiscal",
        "subtitulo": "Obligaciones tributarias, cierres y ajustes del periodo.",
        "modulos": [
            {
                "nombre": "Impuestos",
                "descripcion": "Liquidación y control de impuestos",
                "endpoint": None,
                "icono": '<path d="M6 2h9l3 3v17l-3-2-3 2-3-2-3 2V5Z"/><path d="M9 8h6M9 12h6M9 16h4"/>',
            },
            {
                "nombre": "Cierres",
                "descripcion": "Cierres contables del periodo",
                "endpoint": None,
                "icono": '<rect x="4" y="11" width="16" height="9" rx="2"/>'
                         '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
            },
            {
                "nombre": "Ajustes",
                "descripcion": "Ajustes contables del periodo",
                "endpoint": None,
                "icono": '<line x1="4" y1="6" x2="20" y2="6"/><circle cx="9" cy="6" r="2.2"/>'
                         '<line x1="4" y1="12" x2="20" y2="12"/><circle cx="15" cy="12" r="2.2"/>'
                         '<line x1="4" y1="18" x2="20" y2="18"/><circle cx="8" cy="18" r="2.2"/>',
            },
            {
                "nombre": "Reclasificaciones",
                "descripcion": "Reclasificación de cuentas contables",
                "endpoint": None,
                "icono": '<path d="M4 7h13l-3-3"/><path d="M20 17H7l3 3"/>',
            },
        ],
    },
    "finanzas": {
        "titulo": "Finanzas",
        "subtitulo": "Indicadores y proyecciones financieras.",
        "modulos": [
            {
                "nombre": "Flujo de caja proyectado vs ejecutado",
                "descripcion": "Compara la proyección frente a la ejecución real",
                "endpoint": None,
                "icono": '<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7h-5M21 7v5"/>',
            },
        ],
    },
    # ===== Configuraciones =====
    "empresas": {
        "titulo": "Empresas",
        "subtitulo": "Datos, maestros y configuración de cada empresa.",
        "modulos": [
            {
                "nombre": "Creación / Edición",
                "descripcion": "Crea y edita las empresas y su configuración",
                "endpoint": "web.empresas",
                "icono": '<path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/>'
                         '<path d="M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"/>',
            },
            {
                "nombre": "Maestros",
                "descripcion": "Terceros, cuentas y comprobantes",
                "endpoint": "web.empresas",
                "icono": '<path d="M14 3v5h5"/>'
                         '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
                         '<path d="M8 13h8M8 17h6"/>',
            },
            {
                "nombre": "Consultar cuentas",
                "descripcion": "Busca cualquier cuenta del plan contable",
                "endpoint": "web.cuentas",
                "icono": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
            },
            {
                "nombre": "Estructura archivo bancos",
                "descripcion": "Formato del extracto bancario de la empresa",
                "endpoint": "web.empresas",
                "icono": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
                         '<path d="M3 9h18M9 4v16"/>',
            },
            {
                "nombre": "Machine learning",
                "descripcion": "Reglas aprendidas del motor de sugerencias",
                "endpoint": "web.historial",
                "icono": '<path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V16a3 3 0 0 0 6 0M12 5a3 3 0 0 1 3 3 '
                         '3 3 0 0 1 1 5.8V16a3 3 0 0 1-6 0M12 5V3"/>',
            },
        ],
    },
    "usuarios": {
        "titulo": "Usuarios",
        "subtitulo": "Acceso, roles y trazabilidad del sistema.",
        "modulos": [
            {
                "nombre": "Usuarios y roles",
                "descripcion": "Administra usuarios y sus permisos",
                "endpoint": "web.usuarios",
                "icono": '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3 3-5 6-5s6 2 6 5"/>'
                         '<circle cx="17" cy="8" r="2.5"/><path d="M16 15c3 0 5 2 5 5"/>',
            },
            {
                "nombre": "Auditoría",
                "descripcion": "Bitácora de acciones del sistema",
                "endpoint": "web.auditoria",
                "icono": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6Z"/><path d="m9 12 2 2 4-4"/>',
            },
        ],
    },
}
