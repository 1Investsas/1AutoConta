# Assets de imagen — 1CONTIGO

## Logo

Coloca aquí el logo oficial con **exactamente** este nombre:

```
app/web/static/img/logo-1contigo.png
```

- Formato **PNG** con fondo transparente (o blanco).
- Alto recomendado: ~96–140 px (se escala automáticamente a 46 px en el sidebar).
- Para el sidebar oscuro conviene una versión con el isotipo + wordmark claros/dorados.

Las plantillas lo referencian con:

```jinja
<img src="{{ url_for('static', filename='img/logo-1contigo.png') }}" alt="1CONTIGO">
```

Mientras el archivo no exista, el sidebar muestra automáticamente un
**fallback textual** "1CONTIGO" (no se ve roto). En cuanto subas el PNG con
ese nombre, el logo aparecerá sin más cambios.
