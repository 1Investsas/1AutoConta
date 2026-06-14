# Assets de imagen — 1CONTIGO

## Logo

La WebApp usa estos archivos (servidos desde `static/img/`):

| Archivo | Uso | Cómo se genera |
|---|---|---|
| `logo-1contigo.png` | Logo del **sidebar** (alto 46 px) | Manual u optimizado por script |
| `favicon.png` | Ícono de la pestaña del navegador | Optimizado por script |
| `logo-1contigo-source.png` | Logo **original en alta** (no se usa en la web) | Lo subes tú |

### Opción A — Rápida (sin script)
Sube tu logo con **exactamente** este nombre:

```
app/web/static/img/logo-1contigo.png
```

- PNG con **fondo transparente** (recomendado para el sidebar oscuro).
- Alto recomendado: ~96–140 px.

### Opción B — Optimizada (recomendada) ⭐
Deja que el script recorte márgenes, vuelva el fondo transparente y genere
una versión nítida + el favicon:

```bash
# 1) Sube tu logo en alta resolución como:
#    app/web/static/img/logo-1contigo-source.png   (puede tener fondo blanco)

# 2) Instala Pillow (solo para esta herramienta, no para la app):
pip install pillow

# 3) Ejecuta el optimizador:
python scripts/optimizar_logo.py
```

Esto genera automáticamente:
- `logo-1contigo.png` → recortado, fondo transparente, alto 140 px, optimizado.
- `favicon.png` → ícono cuadrado 64×64.

Opciones útiles:

```bash
python scripts/optimizar_logo.py ruta/al/logo.png   # origen explícito
python scripts/optimizar_logo.py --altura 120        # otro alto de sidebar
python scripts/optimizar_logo.py --fondo transparente  # si ya viene sin fondo
python scripts/optimizar_logo.py --no-favicon          # no generar favicon
```

## Referencia en plantillas

```jinja
<img src="{{ url_for('static', filename='img/logo-1contigo.png') }}" alt="1CONTIGO">
```

Mientras el archivo no exista, el sidebar muestra un **fallback textual**
"1CONTIGO" (la app nunca se ve rota). En cuanto exista el PNG, aparece solo.
