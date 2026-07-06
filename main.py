"""
Punto de entrada CLI del sistema 1ContaBot.

Uso básico:
    python main.py procesar --radian input/RADIAN.xlsx

Opciones adicionales:
    --terceros     data/Listado_de_Terceros.xlsx
    --cuentas      data/Listado_de_Cuentas_Contables.xlsx
    --comprobantes data/Tipos_de_comprobante_contable.xlsx
    --output       output/
    --db           db/contable.db
"""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from app.config import (
    DATA_DIR, INPUT_DIR, OUTPUT_DIR, DB_PATH, LOG_LEVEL, NOMBRE_EMPRESA, NIT_EMPRESA
)
from app.database import inicializar_db
from app import bitacora as bita

console = Console()


def _configurar_logging(nivel: str) -> None:
    """Configura el logging estándar de Python."""
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.version_option("1.0.0", prog_name="1ContaBot")
def cli():
    """
    Sistema de automatización contable para 1 INVEST SAS.

    Procesa reportes RADIAN de la DIAN y genera preasientos contables
    listos para importar al sistema de contabilidad.
    """


@cli.command("procesar")
@click.option(
    "--radian", "-r",
    required=True,
    help="Ruta al archivo RADIAN.xlsx descargado del portal DIAN.",
    type=click.Path(exists=False),
)
@click.option(
    "--terceros", "-t",
    default=None,
    help="Ruta al maestro de terceros (Listado_de_Terceros.xlsx).",
    type=click.Path(exists=False),
)
@click.option(
    "--cuentas", "-c",
    default=None,
    help="Ruta al plan de cuentas (Listado_de_Cuentas_Contables.xlsx).",
    type=click.Path(exists=False),
)
@click.option(
    "--comprobantes", "-k",
    default=None,
    help="Ruta al catálogo de comprobantes (Tipos_de_comprobante_contable.xlsx).",
    type=click.Path(exists=False),
)
@click.option(
    "--output", "-o",
    default=OUTPUT_DIR,
    help=f"Directorio o ruta del archivo Excel de salida. [default: {OUTPUT_DIR}]",
    show_default=True,
)
@click.option(
    "--db",
    default=DB_PATH,
    help=f"Ruta a la base de datos SQLite. [default: {DB_PATH}]",
    show_default=True,
)
@click.option(
    "--incluir-duplicados",
    is_flag=True,
    default=False,
    help="Procesar también documentos ya registrados en la BD.",
)
@click.option(
    "--log-nivel",
    default=LOG_LEVEL,
    help="Nivel de logging (DEBUG, INFO, WARNING, ERROR).",
    show_default=True,
)
def procesar(radian, terceros, cuentas, comprobantes, output, db,
             incluir_duplicados, log_nivel):
    """
    Procesa un archivo RADIAN y genera el Excel de preasientos contables.

    Flujo:
    1. Importar RADIAN
    2. Clasificar documentos
    3. Cruzar terceros
    4. Asignar comprobantes
    5. Separar impuestos
    6. Generar preasientos
    7. Validar
    8. Exportar Excel
    """
    _configurar_logging(log_nivel)
    bita.limpiar_sesion()

    console.print(Panel(
        f"[bold blue]{NOMBRE_EMPRESA}[/bold blue]\n"
        f"NIT: [cyan]{NIT_EMPRESA}[/cyan]\n"
        f"Sistema de automatización contable v1.0",
        title="1ContaBot",
        expand=False,
    ))

    # ------------------------------------------------------------------
    # Resolución de rutas de archivos maestros con fallback automático
    # ------------------------------------------------------------------
    data_dir = Path(DATA_DIR)

    if terceros is None:
        terceros = str(data_dir / "Listado_de_Terceros.xlsx")
    if cuentas is None:
        cuentas = str(data_dir / "Listado_de_Cuentas_Contables.xlsx")
    if comprobantes is None:
        comprobantes = str(data_dir / "Tipos_de_comprobante_contable.xlsx")

    # ------------------------------------------------------------------
    # Inicializar BD
    # ------------------------------------------------------------------
    console.print(f"[dim]Inicializando base de datos:[/dim] {db}")
    try:
        inicializar_db(db)
        bita.registrar("INFO", "main", "INIT_DB", f"BD inicializada: {db}", db_path=db)
    except Exception as exc:
        console.print(f"[bold red]Error inicializando BD:[/bold red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 1. Importar RADIAN
    # ------------------------------------------------------------------
    from app.importador import (
        importar_radian, cargar_maestro_terceros,
        cargar_maestro_cuentas, cargar_maestro_comprobantes,
    )

    console.print(f"\n[bold]1/8[/bold] Importando RADIAN: [cyan]{radian}[/cyan]")
    try:
        df = importar_radian(radian, db_path=db)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    if not incluir_duplicados:
        n_antes = len(df)
        df = df[~df["_duplicado"]].copy()
        n_dup = n_antes - len(df)
        if n_dup:
            console.print(f"  [yellow]Omitidos {n_dup} duplicados.[/yellow]")

    if df.empty:
        console.print("[yellow]No hay documentos nuevos para procesar.[/yellow]")
        sys.exit(0)

    console.print(f"  [green]✓[/green] {len(df)} documentos a procesar.")
    bita.registrar("INFO", "importador", "IMPORTAR_RADIAN",
                   f"{len(df)} documentos importados desde {radian}", db_path=db)

    # ------------------------------------------------------------------
    # Cargar archivos maestros (opcionales — solo avisa si no existen)
    # ------------------------------------------------------------------
    df_terceros = _cargar_opcional(cargar_maestro_terceros, terceros, "terceros", db)
    df_cuentas  = _cargar_opcional(cargar_maestro_cuentas,  cuentas,   "cuentas",  db)
    df_comp     = _cargar_opcional(cargar_maestro_comprobantes, comprobantes, "comprobantes", db)

    # ------------------------------------------------------------------
    # 2. Clasificar
    # ------------------------------------------------------------------
    from app.clasificador import clasificar_lote
    console.print("\n[bold]2/8[/bold] Clasificando documentos…")
    df = clasificar_lote(df)
    _mostrar_resumen_clasificacion(df)
    bita.registrar("INFO", "clasificador", "CLASIFICAR",
                   f"Clasificación completada para {len(df)} documentos", db_path=db)

    # ------------------------------------------------------------------
    # 3. Cruzar terceros
    # ------------------------------------------------------------------
    from app.terceros import procesar_terceros_lote
    console.print("\n[bold]3/8[/bold] Cruzando terceros…")
    import pandas as pd
    df_t = df_terceros if df_terceros is not None else pd.DataFrame()
    df = procesar_terceros_lote(df, df_t)
    n_sin_tercero = (~df["tercero_encontrado"]).sum()
    console.print(f"  [green]✓[/green] Terceros encontrados: {len(df) - n_sin_tercero}/{len(df)}")
    if n_sin_tercero:
        console.print(f"  [yellow]⚠ {n_sin_tercero} tercero(s) no encontrado(s) en el maestro.[/yellow]")

    # ------------------------------------------------------------------
    # 4. Asignar comprobantes
    # ------------------------------------------------------------------
    from app.comprobantes import asignar_comprobantes_lote
    console.print("\n[bold]4/8[/bold] Asignando comprobantes…")
    df = asignar_comprobantes_lote(df, df_comp)
    console.print("  [green]✓[/green] Comprobantes asignados.")

    # ------------------------------------------------------------------
    # 5. Separar impuestos
    # ------------------------------------------------------------------
    from app.impuestos import procesar_impuestos_lote
    console.print("\n[bold]5/8[/bold] Separando impuestos…")
    df = procesar_impuestos_lote(df)
    console.print("  [green]✓[/green] Impuestos procesados.")

    # ------------------------------------------------------------------
    # 6. Generar preasientos
    # ------------------------------------------------------------------
    from app.preasiento import generar_lote
    console.print("\n[bold]6/8[/bold] Generando preasientos\u2026")
    preasientos = generar_lote(df, df_comp, db_path=db)
    console.print(f"  [green]\u2713[/green] {len(preasientos)} preasientos generados.")

    # ------------------------------------------------------------------
    # 7. Validar y recopilar excepciones
    # ------------------------------------------------------------------
    from app.validaciones import validar_preasiento_completo
    console.print("\n[bold]7/8[/bold] Validando…")
    excepciones = []
    for p in preasientos:
        errores = validar_preasiento_completo(p, df_cuentas, db)
        if errores:
            excepciones.append({
                "cufe": p.cufe,
                "tipo_documento": p.tipo_documento,
                "clasificacion": p.clasificacion,
                "tercero_nit": p.tercero_nit,
                "total": p.total,
                "errores": errores,
            })
            bita.registrar("WARNING", "validaciones", "EXCEPCION",
                           f"CUFE {p.cufe[:20]}…: {'; '.join(errores)}", cufe=p.cufe, db_path=db)

    n_ok = len(preasientos) - len(excepciones)
    console.print(f"  [green]✓[/green] {n_ok} OK — [yellow]{len(excepciones)} con excepciones[/yellow]")

    # ------------------------------------------------------------------
    # Registrar documentos procesados en la BD
    # ------------------------------------------------------------------
    from app.database import registrar_documento
    for _, row in df.iterrows():
        try:
            registrar_documento(
                cufe=str(row.get("CUFE/CUDE", "")),
                tipo_documento=str(row.get("Tipo de documento", "")),
                clasificacion=str(row.get("clasificacion", "")),
                folio=str(row.get("Folio", "")),
                prefijo=str(row.get("Prefijo", "")),
                nit_emisor=str(row.get("NIT Emisor", "")),
                nombre_emisor=str(row.get("Nombre Emisor", "")),
                nit_receptor=str(row.get("NIT Receptor", "")),
                nombre_receptor=str(row.get("Nombre Receptor", "")),
                total=float(row.get("Total", 0.0) or 0.0),
                fecha_emision=row.get("Fecha Emisión"),
                archivo_origen=radian,
                db_path=db,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Error registrando doc en BD: %s", exc)

    # ------------------------------------------------------------------
    # 8. Exportar Excel
    # ------------------------------------------------------------------
    from app.exportador import exportar_excel
    console.print(f"\n[bold]8/8[/bold] Exportando Excel a: [cyan]{output}[/cyan]")
    registros_bitacora = bita.obtener_registros_sesion()
    try:
        ruta_salida = exportar_excel(
            preasientos=preasientos,
            excepciones=excepciones,
            bitacora=registros_bitacora,
            output_path=output,
            archivo_origen=radian,
        )
    except Exception as exc:
        console.print(f"[bold red]Error exportando:[/bold red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Alimentar historial para mejorar sugerencias futuras
    # ------------------------------------------------------------------
    from app.sugerencias import registrar_lote_confirmaciones
    n_confirmaciones = registrar_lote_confirmaciones(preasientos, db_path=db)
    if n_confirmaciones:
        console.print(f"  [dim]Motor de sugerencias: {n_confirmaciones} cuenta(s) registradas en historial.[/dim]")

    # ------------------------------------------------------------------
    # Resumen final
    # ------------------------------------------------------------------
    console.print()
    _imprimir_resumen_final(preasientos, excepciones, ruta_salida)


@cli.command("historial")
@click.option(
    "--db",
    default=DB_PATH,
    help=f"Ruta a la base de datos SQLite. [default: {DB_PATH}]",
    show_default=True,
)
@click.option(
    "--top", "-n",
    default=20,
    help="Número de entradas a mostrar.",
    show_default=True,
)
def historial(db, top):
    """
    Muestra las cuentas contables aprendidas por el motor de sugerencias.

    Lista las combinaciones (clasificación, tercero, tipo de línea) con
    más confirmaciones, en orden descendente de uso.
    """
    from app.database import get_connection
    conn = get_connection(db)
    try:
        rows = conn.execute(
            """
            SELECT clasificacion, nit_tercero, tipo_linea, cuenta, usos, ultima_vez
            FROM historial_cuentas
            ORDER BY usos DESC
            LIMIT ?
            """,
            (top,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]El historial está vacío. Procesa al menos un RADIAN primero.[/yellow]")
        return

    tabla = Table(title="Motor de Sugerencias — Historial de Cuentas", box=box.SIMPLE)
    tabla.add_column("Clasificación",  style="cyan",   no_wrap=True)
    tabla.add_column("NIT Tercero",    style="magenta", no_wrap=True)
    tabla.add_column("Tipo Línea",    style="yellow",  no_wrap=True)
    tabla.add_column("Cuenta",         style="green",   no_wrap=True)
    tabla.add_column("Usos",           justify="right")
    tabla.add_column("Última vez",     style="dim")

    for row in rows:
        tabla.add_row(
            row["clasificacion"],
            row["nit_tercero"],
            row["tipo_linea"],
            row["cuenta"],
            str(row["usos"]),
            (row["ultima_vez"] or "")[:19],
        )

    console.print(tabla)




def _cargar_opcional(funcion, filepath: str, nombre: str, db_path: str):
    """Intenta cargar un archivo maestro; retorna None si no existe."""
    import pandas as pd
    try:
        return funcion(filepath)
    except FileNotFoundError:
        console.print(f"  [dim]Maestro de {nombre} no encontrado ({filepath}). Se omite.[/dim]")
        return None
    except Exception as exc:
        console.print(f"  [yellow]Advertencia al cargar {nombre}: {exc}[/yellow]")
        return None


def _mostrar_resumen_clasificacion(df) -> None:
    """Imprime tabla de clasificación en consola."""
    tabla = Table(box=box.SIMPLE, show_header=True)
    tabla.add_column("Clasificación", style="cyan")
    tabla.add_column("Cantidad", justify="right", style="green")

    conteo = df["clasificacion"].value_counts().to_dict()
    for clase, cant in sorted(conteo.items()):
        color = "red" if clase == "SIN_CLASIFICAR" else "green"
        tabla.add_row(clase, f"[{color}]{cant}[/{color}]")

    console.print(tabla)


def _imprimir_resumen_final(preasientos, excepciones, ruta_salida: str) -> None:
    """Imprime panel de resumen al finalizar."""
    n_ok = len(preasientos) - len(excepciones)
    console.print(Panel(
        f"[bold green]Proceso completado[/bold green]\n\n"
        f"Total documentos procesados: [bold]{len(preasientos)}[/bold]\n"
        f"  [green]✓ Sin excepciones:[/green] {n_ok}\n"
        f"  [yellow]⚠ Con excepciones:[/yellow] {len(excepciones)}\n\n"
        f"Archivo generado:\n[cyan]{ruta_salida}[/cyan]",
        title="Resumen",
        expand=False,
    ))


@cli.command("radian-auto")
@click.option(
    "--empresa", "-e", default=None,
    help="ID de la empresa a importar. Por defecto: todas las habilitadas.",
)
@click.option(
    "--desde", default=None,
    help="Fecha inicial YYYY-MM-DD (por defecto según la config de la empresa).",
)
@click.option(
    "--hasta", default=None,
    help="Fecha final YYYY-MM-DD (por defecto hoy).",
)
@click.option(
    "--incluir-duplicados", is_flag=True, default=False,
    help="Reprocesar documentos (CUFE) ya registrados.",
)
def radian_auto(empresa, desde, hasta, incluir_duplicados):
    """
    Descarga e importa el reporte RADIAN desde la DIAN de forma automática.

    Pensado para ejecutarse desde un programador (cron / Tarea programada /
    Azure WebJob) una vez al día. Solicita el token a la DIAN, lo lee del correo
    configurado, descarga el reporte y lo procesa con el pipeline estándar.
    """
    _configurar_logging(LOG_LEVEL)
    from app.empresas import obtener_empresa
    from app.radian_auto.auto_importador import importar_empresa, importar_todas

    if empresa:
        emp = obtener_empresa(empresa)
        console.print(f"[cyan]Importando RADIAN automático de {emp.nombre}…[/cyan]")
        resultados = [
            importar_empresa(
                emp, fecha_desde=desde, fecha_hasta=hasta,
                incluir_duplicados=incluir_duplicados,
            )
        ]
    else:
        console.print("[cyan]Importando RADIAN automático de las empresas habilitadas…[/cyan]")
        resultados = importar_todas(incluir_duplicados=incluir_duplicados)

    if not resultados:
        console.print("[yellow]No hay empresas con importación automática "
                      "habilitada y configurada.[/yellow]")
        return

    hubo_error = False
    for r in resultados:
        if r.ok:
            console.print(f"  [green]✓[/green] {r.empresa_id}: {r.mensaje}")
        else:
            hubo_error = True
            console.print(f"  [red]✗[/red] {r.empresa_id}: {r.mensaje}")

    if hubo_error:
        sys.exit(1)


if __name__ == "__main__":
    cli()
