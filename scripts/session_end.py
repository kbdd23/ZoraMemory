#!/usr/bin/env python3
"""Memory - SessionEnd: Podar recuerdos viejos y generar resumen de sesion.

Lee:  stdin  -> JSON con datos de sesion (opcional, puede estar vacio)
Escribe: stdout -> JSON con systemMessage informando poda y resumen
Stderr:      -> logs de diagnostico
"""
import json
import os
import sys
import time

from _utils import marcar_inyectado_reset

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory", "chroma")

RETENCION_DIAS = 30
LIMITE_RECUERDOS = 2000

NL = chr(10)


def _generar_resumen(collection, session_id: str) -> int:
    """Compila la sesion en notas comprimidas: topic, decisiones, metadatos.

    En vez de volcar el transcript completo, extrae:
    - Primer mensaje del usuario (tema de la sesion)
    - Ultimo mensaje del usuario (cierre/conclusion)
    - Proyectos/directorios involucrados
    - Conteo de interacciones y duracion

    Almacena como documento tipo 'session_summary' en Chroma.
    Retorna cantidad de interacciones compiladas, 0 si no se pudo.
    """
    if not session_id:
        return 0

    resultados = collection.get(
        where={"sesion": session_id},
        include=["documents", "metadatas"],
    )

    docs = resultados.get("documents", [])
    metas = resultados.get("metadatas", [])

    if not docs:
        return 0

    # Ordenar por timestamp ascendente
    pares = sorted(zip(docs, metas), key=lambda x: x[1].get("ts", 0))

    ts_min = pares[0][1].get("ts", time.time())
    ts_max = pares[-1][1].get("ts", time.time())

    # Extraer primer y ultimo mensaje de usuario
    usuarios = [(d, m) for d, m in pares if m.get("tipo") == "usuario"]
    primer_user = usuarios[0][0].strip()[:200] if usuarios else "(sin mensaje)"
    ultimo_user = usuarios[-1][0].strip()[:200] if usuarios else ""

    # Proyectos unicos involucrados
    proyectos = sorted(set(
        m.get("project", "") or "default"
        for _, m in pares
        if m.get("project")
    ))
    etiqueta_proyectos = " Proyectos: " + ", ".join(proyectos) if proyectos else ""

    # Construir notas comprimidas
    lineas = []
    lineas.append("Tema: " + primer_user)
    if ultimo_user and ultimo_user != primer_user:
        lineas.append("Cierre: " + ultimo_user)
    lineas.append("Duracion: " + str(int(ts_min)) + "-" + str(int(ts_max)) + " (" + str(len(pares)) + " turnos)")
    if etiqueta_proyectos:
        lineas.append(etiqueta_proyectos)
    # Incluir menciones a decisiones (heuristica: busco '?' o '!' al final)
    decisiones = []
    for d, m in pares:
        txt = d.strip()
        if len(txt) > 30 and len(txt) <= 150 and txt[-1] in ("?", "!"):
            pref = "[U]" if m.get("tipo") == "usuario" else "[A]" if m.get("tipo") == "asistente" else "[?]"
            decisiones.append(pref + " " + txt)
    if decisiones:
        # Max 3 decisiones
        for dec in decisiones[:3]:
            lineas.append(dec)

    resumen = (
        "--- Resumen de sesion " + session_id[:12] + " ---" + NL
        + NL.join(lineas)
    )

    now = time.time()
    # ID deterministico: mismo session_id siempre produce mismo summary_id
    # Asi rewinds sobrescriben en vez de duplicar
    summary_id = "sum_" + (session_id[:16] if session_id else "anon_" + str(int(now)))

    # Eliminar summary previo de esta sesion si existe (rewind guard)
    try:
        prev = collection.get(
            where={"$and": [{"tipo": "session_summary"}, {"sesion": session_id}]},
            include=["ids"],
        )
        prev_ids = prev.get("ids", [])
        if prev_ids:
            collection.delete(ids=prev_ids)
            print(
                "Memory: resumen previo de sesion " + session_id[:12] + " reemplazado",
                file=sys.stderr,
            )
    except Exception:
        pass

    collection.upsert(
        documents=[resumen],
        metadatas=[{
            "tipo": "session_summary",
            "ts": now,
            "ts_start": int(ts_min),
            "ts_end": int(ts_max),
            "sesion": session_id,
            "interacciones": len(pares),
            "cwd": os.getcwd(),
        }],
        ids=[summary_id],
    )
    print(
        "Memory: resumen de sesion " + session_id[:12] + " (" + str(len(pares)) + " turnos)",
        file=sys.stderr,
    )
    return len(pares)


def main() -> None:
    # Leer stdin para obtener session_id si esta disponible
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        input_data = {}
    session_id = input_data.get("session_id", "")

    import chromadb
    from chromadb.errors import NotFoundError
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    client = chromadb.PersistentClient(path=CHROMA_DIR)

    try:
        collection = client.get_collection(
            "hermes", embedding_function=DefaultEmbeddingFunction()
        )
    except NotFoundError:
        print(json.dumps({"systemMessage": "Memory: sin recuerdos que podar"}))
        return

    total = collection.count()
    if total == 0:
        print(json.dumps({"systemMessage": "Memory: sin recuerdos que podar"}))
        return

    # --- Fase 1: Generar resumen de la sesion que finaliza ---
    turnos = _generar_resumen(collection, session_id)

    # --- Fase 2: Podar recuerdos viejos (>30 dias) ---
    ahora = time.time()
    tope = ahora - (RETENCION_DIAS * 24 * 3600)

    todos = collection.get(include=["metadatas"])
    a_eliminar: list[str] = []

    for i, meta in enumerate(todos["metadatas"]):
        ts = meta.get("ts", 0)
        if isinstance(ts, (int, float)) and ts < tope:
            a_eliminar.append(todos["ids"][i])

    if a_eliminar:
        collection.delete(ids=a_eliminar)

    # --- Fase 3: Si excede el limite, eliminar las mas viejas ---
    restantes = collection.count()
    if restantes > LIMITE_RECUERDOS:
        exceso = restantes - LIMITE_RECUERDOS
        sobrantes = collection.get(include=["metadatas"])
        indexados = list(enumerate(sobrantes["metadatas"]))
        indexados.sort(key=lambda x: x[1].get("ts", 0))
        sobrantes_ids = [sobrantes["ids"][i] for i, _ in indexados[:exceso]]
        collection.delete(ids=sobrantes_ids)
        print(
            "Memory: " + str(exceso) + " recuerdos eliminados por limite de capacidad",
            file=sys.stderr,
        )

    final = collection.count()
    msgs = []
    if turnos:
        msgs.append("resumen de sesion guardado (" + str(turnos) + " turnos)")
    if a_eliminar:
        msgs.append(str(len(a_eliminar)) + " recuerdos viejos podados")
    if msgs:
        print("Memory: " + ", ".join(msgs) + ", " + str(final) + " activos", file=sys.stderr)

    # Limpiar flag de inyeccion: proxima sesion podra re-inyectar memoria
    if session_id:
        try:
            marcar_inyectado_reset(collection, session_id)
        except Exception:
            pass

    system_msg = "Memory: "
    if msgs:
        system_msg += ", ".join(msgs) + ", "
    system_msg += str(final) + " activos"

    print(json.dumps({"systemMessage": system_msg}))


if __name__ == "__main__":
    main()
