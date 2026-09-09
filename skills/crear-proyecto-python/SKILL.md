---
name: crear-proyecto-python
description: Usar cuando el Creador pide crear un proyecto o script chico en Python con uno o varios archivos (ej. "creá una calculadora", "armá un script que...").
---

# Crear un proyecto Python chico

## Escritura

- Usá `fs_write` con `files: [{path, content}, ...]` para todos los
  archivos del proyecto en una sola llamada — es atómico (si uno falla,
  se revierten los que ya se habían escrito). No uses `codigo` para esto:
  esa tool solo guarda el PRIMER bloque de código de la respuesta, no
  sirve para proyectos de varios archivos.
- Usá rutas absolutas explícitas. Si el Creador no dio una ruta, preguntá
  antes de asumir dónde — no hay default razonable para "dónde va esto".

## Si el script pide input() en loop (menús interactivos)

Antes de ejecutarlo para verificar que no tiene errores de sintaxis,
fijate CÓMO termina el loop:

- Si la condición de salida se evalúa DESPUÉS de pedir otros inputs
  (ej. primero pide dos números y recién en el tercer input revisa si es
  "salir"), no alcanza con mandar una sola línea "salir" por stdin — hay
  que respetar el orden real de los prompts o el proceso queda colgado
  esperando el input que nunca llega.
- Si tenés dudas sobre el flujo exacto, LEÉ el archivo escrito (`fs_read`)
  antes de intentar simular la entrada, en vez de asumir la primera línea
  que pide el programa.
- Alternativa más simple y confiable: verificar sintaxis con
  `python3 -m py_compile <archivo>` en vez de ejecutar el programa
  interactivo completo — confirma que compila sin tener que simular un
  diálogo de stdin.

## Después de escribir

Reportá la ruta exacta de cada archivo creado. No asumas que el Creador
sabe dónde quedó si no fue él quien dio la ruta completa.
