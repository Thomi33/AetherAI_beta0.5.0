# Skills de Aether

Una skill es una carpeta con instrucciones reutilizables para un tipo de
tarea recurrente (buenas prácticas, checklists, formato de salida). No
ejecutan nada por sí solas — son conocimiento que Aether carga bajo demanda
(vía `fs_read`) antes de encarar una tarea, no en cada turno.

## Formato

```
skills/
  <nombre-skill>/
    SKILL.md
```

`SKILL.md` lleva un frontmatter mínimo al principio:

```
---
name: nombre-skill
description: Cuándo usar esta skill, en una línea, para que el catálogo
  del system prompt alcance para decidir si conviene leerla completa.
---

<contenido libre: instrucciones, ejemplos, checklists, errores comunes>
```

- `name` y `description` son obligatorios — sin ellos, `core/skills/registry.py`
  ignora la carpeta silenciosamente (para no romper el arranque por una
  skill mal formada).
- El resto del archivo es libre: markdown normal.

## Cómo se agrega una skill nueva

No hace falta tocar código ni reiniciar Aether:

- A mano: creá la carpeta y el `SKILL.md` con cualquier editor.
- Pidiéndoselo a Aether: "creá una skill en skills/<nombre>/SKILL.md que
  diga...". `fs_write` ya puede escribir ahí directo.

`registry.py` escanea la carpeta en cada orden (son pocos archivos chicos,
no vale la pena cachear), así que la próxima orden ya la va a ver.

## Cómo se usa

El catálogo (nombre + descripción + ruta) se inyecta automáticamente en el
system prompt (`construir_backstory`, en `core/agent/prompts.py`). El
modelo decide solo, según el pedido del Creador, si conviene leer alguna
completa con `fs_read` antes de actuar.
