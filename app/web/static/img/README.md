# Assets de imagen — 1ContaBot

## Logo

La WebApp usa estos archivos (servidos desde `static/img/`):

| Archivo | Uso | Cómo se genera |
|---|---|---|
| `logo-1contabot.png` | Logo del **sidebar** (alto 46 px) | Manual u optimizado por script |
| `logo-1contabot-full.png` | **Lockup completo** (login / pantalla de carga) | Script, modo `--solo-isotipo` |
| `favicon.png` | Ícono de la pestaña del navegador | Optimizado por script |

> ⚠️ **Mantén estos PNG pequeños** (el del sidebar ~35 KB, alto ≤140 px): se
> descargan en **cada página**. Un logo de 1–2 MB aquí hace que toda la app se
> sienta lenta en el navegador.

El logo **original en alta** vive en `assets/branding/logo-1contabot-source.png`
(fuera de `static/` para que no se sirva al navegador ni engorde el despliegue).

### Opción A — Rápida (sin script)
Sube tu logo con **exactamente** este nombre:

```
app/web/static/img/logo-1contabot.png
```

- PNG con **fondo transparente** (recomendado para el sidebar oscuro).
- Alto recomendado: ~96–140 px.

### Opción B — Optimizada (recomendada) ⭐
Deja que el script recorte márgenes, vuelva el fondo transparente y genere
una versión nítida + el favicon:

```bash
# 1) Sube tu logo en alta resolución como:
#    assets/branding/logo-1contabot-source.png   (puede tener fondo blanco)

# 2) Instala Pillow (solo para esta herramienta, no para la app):
pip install pillow

# 3) Ejecuta el optimizador:
python scripts/optimizar_logo.py
```

Esto genera automáticamente:
- `logo-1contabot.png` → recortado, fondo transparente, alto 140 px, optimizado.
- `favicon.png` → ícono cuadrado 64×64.

Opciones útiles:

```bash
python scripts/optimizar_logo.py ruta/al/logo.png   # origen explícito
python scripts/optimizar_logo.py --altura 120        # otro alto de sidebar
python scripts/optimizar_logo.py --fondo transparente  # si ya viene sin fondo
python scripts/optimizar_logo.py --no-favicon          # no generar favicon
```

### Solo el isotipo en el sidebar (recomendado para este logo) ⭐

El logo de 1ContaBot trae el isotipo (el "1" con la flecha) arriba y el
wordmark "1ContaBot" + eslogan debajo. A 46 px de alto el texto quedaría
diminuto, así que conviene usar **solo la marca** en el sidebar:

```bash
python scripts/optimizar_logo.py --solo-isotipo
```

Esto:
- Detecta automáticamente el espacio entre la marca y el wordmark y recorta
  **solo el isotipo** → `logo-1contabot.png` (sidebar).
- Guarda el **lockup completo** → `logo-1contabot-full.png` (para login/carga).
- Genera el `favicon.png` a partir de la marca (cuadrada, ideal para el ícono).

Si la detección no acierta, ajusta cuánto conservar desde arriba:

```bash
python scripts/optimizar_logo.py --solo-isotipo --recorte-vertical 0.70
python scripts/optimizar_logo.py --solo-isotipo --altura-full 260   # alto del lockup
```

## Referencia en plantillas

```jinja
<img src="{{ url_for('static', filename='img/logo-1contabot.png') }}" alt="1ContaBot">
```

Mientras el archivo no exista, el sidebar muestra un **fallback textual**
"1ContaBot" (la app nunca se ve rota). En cuanto exista el PNG, aparece solo.
