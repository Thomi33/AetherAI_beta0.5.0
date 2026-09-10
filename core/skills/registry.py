"""
Registro de Skills para Aether.

Una "skill" es una carpeta bajo RUTA_SKILLS con un SKILL.md: instrucciones
reutilizables (buenas prácticas, checklists, errores comunes) para un tipo
de tarea recurrente. NO son tools -- no ejecutan nada por sí solas -- son
CONOCIMIENTO que el modelo puede cargar bajo demanda (vía la tool `fs_read`,
ya existente) antes de encarar una tarea, en vez de reinventar el enfoque
de memoria cada vez. Ver la sección de skills del README raíz para el formato
completo y cómo agregar una nueva (a mano, o pidiéndoselo al propio Aether -- `fs_write` ya
puede escribir ahí, no hace falta ninguna tool nueva para eso).

Formato de SKILL.md (frontmatter mínimo, NO YAML real -- solo
'clave: valor' línea por línea, alcanza para name/description y evita
sumar una dependencia de parsing YAML por esto):

    ---
    name: nombre-skill
    description: Cuándo usar esta skill, en una línea.
    ---
    <contenido libre>

DISEÑO:
- listar_skills() escanea RUTA_SKILLS en cada llamada (son pocos archivos
  chicos -- no vale la pena cachear con invalidación por ahora).
- Carpetas sin SKILL.md, o con frontmatter sin 'name'/'description', se
  ignoran silenciosamente: una skill mal formada no debe romper el
  arranque ni ensuciar el catálogo con entradas inútiles.
- catalogo_skills_condensado() arma el string compacto que se inyecta en
  el system prompt (ver construir_backstory en core/agent/prompts.py).
  Devuelve "" si no hay skills, para no meter una sección vacía en el
  prompt cuando la carpeta todavía no tiene nada.
"""
from __future__ import annotations

import os
import re

from core.config.settings import RUTA_SKILLS

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parsear_frontmatter(texto: str) -> dict:
    """Parser mínimo del bloque '--- ... ---' inicial. Retorna {} si no hay."""
    m = _FRONTMATTER_RE.match(texto)
    if not m:
        return {}
    meta: dict = {}
    for linea in m.group(1).splitlines():
        if ":" not in linea:
            continue
        clave, _, valor = linea.partition(":")
        meta[clave.strip().lower()] = valor.strip()
    return meta


def listar_skills() -> list[dict]:
    """
    Escanea RUTA_SKILLS y devuelve una lista de
    {"name": str, "description": str, "path": str (absoluta)} por cada
    <RUTA_SKILLS>/*/SKILL.md válido (con name y description en el
    frontmatter). Lista vacía si la carpeta no existe todavía.
    """
    skills: list[dict] = []
    base = str(RUTA_SKILLS)
    if not os.path.isdir(base):
        return skills

    for nombre_dir in sorted(os.listdir(base)):
        ruta_skill = os.path.join(base, nombre_dir, "SKILL.md")
        if not os.path.isfile(ruta_skill):
            continue
        try:
            with open(ruta_skill, "r", encoding="utf-8") as f:
                contenido = f.read()
        except Exception:
            continue

        meta = _parsear_frontmatter(contenido)
        nombre = meta.get("name") or nombre_dir
        descripcion = meta.get("description")
        if not descripcion:
            continue

        skills.append({
            "name": nombre,
            "description": descripcion,
            "path": os.path.abspath(ruta_skill),
        })
    return skills


def catalogo_skills_condensado() -> str:
    """
    String compacto para el system prompt: una línea por skill con
    nombre, descripción y la ruta absoluta para leerla con fs_read.
    "" si no hay ninguna (evita una sección vacía en el prompt).
    """
    skills = listar_skills()
    if not skills:
        return ""
    return "\n".join(
        f"- '{s['name']}': {s['description']} (leer completa con fs_read en: {s['path']})"
        for s in skills
    )
