#!/usr/bin/env python3
"""
Optimizador de logo para 1CONTIGO.

Toma el logo original (alta resolución, normalmente con fondo blanco) y genera
las versiones que usa la WebApp:

  - logo-1contigo.png  → versión recortada y optimizada para el SIDEBAR oscuro
                         (fondo transparente, márgenes recortados, alto fijo).
  - favicon.png        → ícono cuadrado para la pestaña del navegador.

Uso típico
----------
1. Sube tu logo en alta a:  assets/branding/logo-1contigo-source.png
2. Ejecuta:                 python scripts/optimizar_logo.py
3. Listo: el sidebar usará automáticamente el logo optimizado.

Opciones
--------
    python scripts/optimizar_logo.py [ORIGEN] [--altura 140] [--fondo auto]
                                     [--umbral 30] [--no-favicon]

`ORIGEN` puede ser cualquier ruta (.png/.jpg/.webp). Si se omite, se busca en
assets/branding/ y luego en app/web/static/img/, en este orden:
logo-1contigo-source, logo-1contigo-original, logo-1contigo.

Requisito (solo para esta herramienta, no para la app): pip install pillow
"""

import shutil
import sys
from pathlib import Path

import click

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("✗ Falta Pillow. Instálalo con:  pip install pillow", file=sys.stderr)
    sys.exit(1)

# Carpeta de assets: scripts/optimizar_logo.py → raíz → app/web/static/img
_RAIZ = Path(__file__).resolve().parent.parent
IMG_DIR = _RAIZ / "app" / "web" / "static" / "img"
# El logo original en alta vive FUERA de static/ para que no se sirva al
# navegador ni engorde el paquete de despliegue (pesa varios MB).
SOURCE_DIR = _RAIZ / "assets" / "branding"
SALIDA_DEFAULT = IMG_DIR / "logo-1contigo.png"
FAVICON_DEFAULT = IMG_DIR / "favicon.png"

EXTS = (".png", ".webp", ".jpg", ".jpeg")
CANDIDATOS = ("logo-1contigo-source", "logo-1contigo-original", "logo-1contigo")


def _buscar_origen() -> Path | None:
    """Busca un logo de origen en assets/branding/ y luego en static/img/."""
    for carpeta in (SOURCE_DIR, IMG_DIR):
        for base in CANDIDATOS:
            for ext in EXTS:
                p = carpeta / f"{base}{ext}"
                if p.exists():
                    return p
    return None


def _es_casi_blanco(pixel, umbral: int) -> bool:
    r, g, b = pixel[:3]
    return r >= 255 - umbral and g >= 255 - umbral and b >= 255 - umbral


def quitar_fondo(img: Image.Image, umbral: int) -> Image.Image:
    """Hace transparente el fondo conectado a los bordes (flood fill por esquinas).

    Solo afecta a la región de fondo que toca alguna esquina casi blanca, por lo
    que no perfora zonas blancas internas del logo.
    """
    img = img.convert("RGBA")
    ancho, alto = img.size
    base = img.convert("RGB")
    sentinela = (255, 0, 255)  # color improbable en el logo

    esquinas = [(0, 0), (ancho - 1, 0), (0, alto - 1), (ancho - 1, alto - 1)]
    toco_fondo = False
    for esq in esquinas:
        if _es_casi_blanco(base.getpixel(esq), umbral):
            ImageDraw.floodfill(base, esq, sentinela, thresh=umbral)
            toco_fondo = True

    if not toco_fondo:
        return img  # ninguna esquina es blanca: probablemente ya viene recortado

    px_base = base.load()
    px_img = img.load()
    for y in range(alto):
        for x in range(ancho):
            if px_base[x, y] == sentinela:
                r, g, b, _ = px_img[x, y]
                px_img[x, y] = (r, g, b, 0)
    return img


def recortar_transparencia(img: Image.Image) -> Image.Image:
    """Recorta los márgenes totalmente transparentes."""
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def redimensionar_alto(img: Image.Image, altura: int) -> Image.Image:
    if img.height == altura:
        return img
    ratio = altura / img.height
    nuevo_ancho = max(1, round(img.width * ratio))
    return img.resize((nuevo_ancho, altura), Image.LANCZOS)


def detectar_corte_isotipo(img: Image.Image, recorte_vertical: float) -> int:
    """Calcula la fila Y donde termina el isotipo (la marca) y empieza el wordmark.

    Busca la banda de filas "vacías" (casi sin píxeles opacos) más ancha en la
    mitad inferior del logo —el espacio entre la marca y el texto "1CONTIGO"— y
    corta justo ahí. El análisis se hace sobre una copia en baja resolución por
    velocidad. Si no encuentra una separación clara, usa `recorte_vertical` como
    fracción del alto (default 0.65 = se queda con el 65% superior).

    Devuelve la coordenada Y en las dimensiones ORIGINALES de `img`.
    """
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    alto0 = alpha.height

    # Trabajar en baja resolución (alto <= 400) para que el análisis sea rápido.
    escala = min(1.0, 400 / alto0) if alto0 > 400 else 1.0
    a = alpha if escala == 1.0 else alpha.resize(
        (max(1, round(alpha.width * escala)), max(1, round(alpha.height * escala)))
    )
    ancho, alto = a.size
    px = a.load()

    umbral_fila = max(1, int(ancho * 0.008))  # < 0.8% de píxeles opacos ⇒ fila "vacía"
    conteo = []
    for y in range(alto):
        c = 0
        for x in range(ancho):
            if px[x, y] > 16:
                c += 1
        conteo.append(c)

    # Buscar la banda vacía más ancha entre el 45% y el 88% del alto.
    inicio, fin = int(alto * 0.45), int(alto * 0.88)
    mejor_inicio, mejor_len = None, 0
    y = inicio
    while y < fin:
        if conteo[y] <= umbral_fila:
            j = y
            while j < fin and conteo[j] <= umbral_fila:
                j += 1
            if (j - y) > mejor_len:
                mejor_inicio, mejor_len = y, j - y
            y = j
        else:
            y += 1

    if mejor_inicio is not None and mejor_len >= max(3, int(alto * 0.02)):
        corte = mejor_inicio
    else:
        corte = int(alto * recorte_vertical)

    return max(1, round(corte / escala))  # volver a coordenadas originales


def generar_favicon(img: Image.Image, tam: int = 64) -> Image.Image:
    """Crea un ícono cuadrado con el logo centrado y fondo transparente."""
    lado = max(img.size)
    lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    lienzo.paste(img, ((lado - img.width) // 2, (lado - img.height) // 2), img)
    return lienzo.resize((tam, tam), Image.LANCZOS)


@click.command()
@click.argument("origen", required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--altura", default=140, show_default=True,
              help="Alto en px del logo de sidebar (se muestra a 46px, ~3x para nitidez).")
@click.option("--fondo", type=click.Choice(["auto", "blanco", "transparente"]),
              default="auto", show_default=True,
              help="auto: detecta; blanco: quita el fondo blanco; transparente: ya viene sin fondo.")
@click.option("--umbral", default=30, show_default=True,
              help="Tolerancia 0-255 para considerar un píxel 'casi blanco'.")
@click.option("--salida", type=click.Path(path_type=Path), default=None,
              help=f"Ruta del PNG optimizado (default: {SALIDA_DEFAULT}).")
@click.option("--solo-isotipo", is_flag=True, default=False,
              help="Recorta SOLO la marca (el '1' con la flecha) para el sidebar "
                   "y guarda el lockup completo como logo-1contigo-full.png.")
@click.option("--recorte-vertical", default=0.65, show_default=True, type=float,
              help="Fracción superior a conservar como isotipo si no se detecta "
                   "una separación clara con el wordmark (0-1).")
@click.option("--altura-full", default=220, show_default=True,
              help="Alto en px del lockup completo (login/carga) en modo isotipo.")
@click.option("--favicon/--no-favicon", default=True, show_default=True,
              help="Generar también favicon.png.")
def main(origen, altura, fondo, umbral, salida, solo_isotipo,
         recorte_vertical, altura_full, favicon):
    """Genera el logo optimizado del sidebar (y favicon) a partir del logo original."""
    salida = salida or SALIDA_DEFAULT

    if origen is None:
        origen = _buscar_origen()
        if origen is None:
            click.echo(
                "✗ No encontré ningún logo de origen en "
                f"{IMG_DIR}\n"
                "  Sube tu logo como 'logo-1contigo-source.png' (recomendado) "
                "y vuelve a ejecutar, o pasa la ruta como argumento.",
                err=True,
            )
            sys.exit(1)

    origen = origen.resolve()
    salida = salida.resolve()

    # Si el único origen es el propio archivo de salida, conservamos el original
    # como '-source' para no perder la alta resolución en futuras ejecuciones.
    if origen == salida:
        backup = IMG_DIR / "logo-1contigo-source.png"
        if not backup.exists():
            shutil.copy2(origen, backup)
            click.echo(f"• Copia del original guardada en: {backup.name}")
        origen = backup.resolve()

    click.echo(f"• Origen:  {origen}")
    img = Image.open(origen).convert("RGBA")
    tam_original = img.size

    tiene_alpha = img.getchannel("A").getextrema()[0] < 255
    if fondo == "blanco" or (fondo == "auto" and not tiene_alpha):
        img = quitar_fondo(img, umbral)
        click.echo("• Fondo blanco → transparente (recorte por esquinas).")
    else:
        click.echo("• Se conserva la transparencia existente.")

    img = recortar_transparencia(img)  # contenido completo, ya recortado
    salida.parent.mkdir(parents=True, exist_ok=True)

    if solo_isotipo:
        # 1) Lockup completo (para login / pantalla de carga).
        full = redimensionar_alto(img, altura_full)
        ruta_full = salida.parent / "logo-1contigo-full.png"
        full.save(ruta_full, "PNG", optimize=True)
        kb_full = ruta_full.stat().st_size / 1024
        click.echo(f"✓ Lockup completo: {ruta_full.name}  "
                   f"({full.width}×{full.height}px, {kb_full:.1f} KB)")

        # 2) Isotipo (la marca) para el sidebar.
        corte = detectar_corte_isotipo(img, recorte_vertical)
        iso = recortar_transparencia(img.crop((0, 0, img.width, corte)))
        pct = round(100 * corte / img.height)
        click.echo(f"• Isotipo: se conserva el {pct}% superior (filas 0–{corte} de {img.height}).")
        sidebar_img = iso
    else:
        sidebar_img = img

    sidebar_img = redimensionar_alto(sidebar_img, altura)
    sidebar_img.save(salida, "PNG", optimize=True)
    kb = salida.stat().st_size / 1024
    click.echo(f"✓ Sidebar: {salida.name}  ({sidebar_img.width}×{sidebar_img.height}px, {kb:.1f} KB)")

    if favicon:
        ico = generar_favicon(sidebar_img)  # en modo isotipo usa la marca (cuadrada)
        ruta_ico = salida.parent / "favicon.png"
        ico.save(ruta_ico, "PNG", optimize=True)
        click.echo(f"✓ Favicon: {ruta_ico.name}  ({ico.width}×{ico.height}px)")

    click.echo(f"\nListo. Logo original: {tam_original[0]}×{tam_original[1]}px → optimizado.")


if __name__ == "__main__":
    main()
