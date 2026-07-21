#!/usr/bin/env python3
"""Memory - BeforeAgent: Recuperacion jerarquica con continuidad narrativa.

Lee:  stdin  -> JSON con la conversacion actual
Escribe: stdout -> JSON con additionalContext estructurado:
  - Capa I: Resumenes comprimidos de sesiones recientes (1 linea c/u)
  - Capa II: Memoria Jerarquica en 4 ventanas temporales:
      * inmediata (<1h): 4 slots
      * reciente (1-24h): 3 slots
      * semanal (1-7d): 2 slots
      * historica (>7d): 1 slot
Stderr:      -> logs de diagnostico

Estrategia:
  CAPA I: Consulta directa por metadata tipo=session_summary,
          trayendo los ultimos N resumenes comprimidos.
  CAPA II: Query semantica (TOP_K=12) con clasificacion por ventana
           temporal, priorizando lo reciente sobre lo antiguo.
  CIRCUIT BREAKER: MAX_CTX_CHARS (50000) como cortafuego contra casos patologicos.
"""
import datetime
import json
import os
import sys
import time
from collections import defaultdict
from _utils import marcar_inyectado, marcar_inyectado_reset

NL = chr(10)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory", "chroma")

UMBRAL_RELEVANCIA = 0.40
TOP_K_HIERARCHY = 12  # Query grande para distribuir entre 4 ventanas
MAX_RESUMENES = 3
MAX_CTX_CHARS = 50000  # Circuit breaker: solo trunca si hay un caso patologico


def extraer_ultimo_mensaje(input_data: dict) -> str | None:
    """Extrae el ultimo mensaje del usuario del input del hook.

    Prioridad:
    1. prompt directo (BeforeAgent del CLI)
    2. messages (formato historico)
    3. llm_request.messages (formato legado)
    """
    prompt = input_data.get("prompt")
    if prompt and isinstance(prompt, str) and prompt.strip():
        return prompt

    messages = input_data.get("messages", [])
    if messages:
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "")

    llm_req = input_data.get("llm_request", {})
    msgs = llm_req.get("messages", [])
    if msgs:
        for m in reversed(msgs):
            if m.get("role") == "user":
                return m.get("content", "")

    return None


def formatear_ts(ts: float | int) -> str:
    """Convierte timestamp Unix a string legible."""
    if not ts:
        return "?"
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


def main() -> None:
    input_data = json.load(sys.stdin)

    session_id = input_data.get("session_id", "")
    query = extraer_ultimo_mensaje(input_data)

    print(f"Memory: before_agent.py session_id={session_id!r} query={query!r}", file=sys.stderr)

    # =====================================================================
    # CONEXION UNICA A CHROMADB
    # =====================================================================
    import chromadb
    from chromadb.errors import NotFoundError
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    try:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = client.get_collection(
            "hermes", embedding_function=DefaultEmbeddingFunction()
        )
    except NotFoundError:
        print("Memory: coleccion 'hermes' no encontrada, saliendo", file=sys.stderr)
        print(json.dumps({}))
        return
    except Exception as exc:
        print(f"Memory: error conectando ChromaDB: {exc}", file=sys.stderr)
        print(json.dumps({}))
        return

    if collection.count() == 0:
        print("Memory: coleccion vacia, saliendo", file=sys.stderr)
        print(json.dumps({}))
        return

    # =====================================================================
    # FLAG EN CHROMADB: inyeccion unica por sesion
    # =====================================================================
    if session_id:
        flag_id = f"inj_{session_id[:16]}"
        try:
            flag_res = collection.get(ids=[flag_id], include=["metadatas"])
            if flag_res.get("ids"):
                meta = flag_res["metadatas"][0]
                valor_flag = meta.get("memoria_inyectada", 0)
                print(f"Memory: flag {flag_id} = {valor_flag}", file=sys.stderr)
                if valor_flag == 1:
                    print("Memory: ya inyectado en esta sesion, omitiendo", file=sys.stderr)
                    print(json.dumps({}))
                    return
            else:
                print(f"Memory: flag {flag_id} no encontrado (primera vez)", file=sys.stderr)
        except Exception as exc:
            print(f"Memory: error leyendo flag: {exc}", file=sys.stderr)
    else:
        print("Memory: session_id vacio, no se puede verificar flag", file=sys.stderr)

    # =====================================================================
    # DETECCION DE REWINDS (misma conexion)
    # =====================================================================
    transcript_path = input_data.get("transcript_path", "")

    if session_id and transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r") as _tf:
                _tlines = _tf.readlines()

            ultimo_rewind_id = None
            for _line in reversed(_tlines):
                try:
                    _d = json.loads(_line.strip())
                    if "$rewindTo" in _d:
                        ultimo_rewind_id = _d["$rewindTo"]
                        break
                except (json.JSONDecodeError, Exception):
                    continue

            turnos_reales = 0
            if ultimo_rewind_id:
                _pasado_rewind = False
                for _line in _tlines:
                    try:
                        _d = json.loads(_line.strip())
                        _id_actual = _d.get("id", "")
                        if not _pasado_rewind and _id_actual == ultimo_rewind_id:
                            _pasado_rewind = True
                            continue
                        if _pasado_rewind and _d.get("type") == "user":
                            turnos_reales += 1
                    except (json.JSONDecodeError, Exception):
                        continue
            else:
                for _line in _tlines:
                    try:
                        _d = json.loads(_line.strip())
                        if _d.get("type") == "user":
                            turnos_reales += 1
                    except (json.JSONDecodeError, Exception):
                        continue

            _res = collection.get(
                where={"$and": [{"tipo": "usuario"}, {"sesion": session_id}]},
                include=["metadatas"],
            )
            _en_chromadb = len(_res.get("ids", []))

            if _en_chromadb > turnos_reales:
                _exceso = _en_chromadb - turnos_reales
                _metas = _res.get("metadatas", [])
                _ids = _res.get("ids", [])
                _pares = sorted(
                    zip(_metas, _ids),
                    key=lambda x: x[0].get("ts", 0),
                )
                _a_purgar = [x[1] for x in _pares[-_exceso:]]
                collection.delete(ids=_a_purgar)
                print(
                    f"Memory: {_exceso} recuerdos purgados "
                    f"por rewind (sesion {session_id[:12]})",
                    file=sys.stderr,
                )

                _ts_min = min(x[0].get("ts", 0) for x in _pares[-_exceso:])
                _res_a = collection.get(
                    where={"$and": [{"tipo": "asistente"}, {"sesion": session_id}]},
                    include=["metadatas"],
                )
                _ids_a = _res_a.get("ids", [])
                _metas_a = _res_a.get("metadatas", [])
                _a_purgar_a = [
                    _ids_a[i]
                    for i, m in enumerate(_metas_a)
                    if m.get("ts", 0) >= _ts_min
                ]
                if _a_purgar_a:
                    collection.delete(ids=_a_purgar_a)
                    print(
                        f"Memory: {len(_a_purgar_a)} respuestas asociadas purgadas",
                        file=sys.stderr,
                    )

                marcar_inyectado_reset(collection, session_id)  # rewind: permitir re-inyeccion

        except Exception as _exc:
            print(f"Memory: error en deteccion de rewind: {_exc}", file=sys.stderr)

    if not query or not query.strip():
        print("Memory: query vacia, saliendo", file=sys.stderr)
        print(json.dumps({}))
        return

    # =====================================================================
    # CAPA I: Resumenes de sesiones recientes
    # =====================================================================
    partes: list[str] = []

    try:
        resumenes_raw = collection.get(
            where={"tipo": "session_summary"},
            include=["documents", "metadatas"],
            limit=MAX_RESUMENES,
        )
        res_docs = resumenes_raw.get("documents", [])
        res_metas = resumenes_raw.get("metadatas", [])

        if res_docs:
            pares = list(zip(res_docs, res_metas))
            pares.sort(key=lambda x: x[1].get("ts_end", 0), reverse=True)

            vistos_sesion: set[str] = set()
            pares_dedup = []
            for doc, meta in pares:
                sid = meta.get("sesion", "")
                if sid in vistos_sesion:
                    continue
                vistos_sesion.add(sid)
                pares_dedup.append((doc, meta))

            lineas_resumenes = ["### Resumen de sesiones anteriores"]
            for doc, meta in pares_dedup:
                ts_end = meta.get("ts_end", 0)
                interacciones = meta.get("interacciones", 0)
                sesion_id = meta.get("sesion", "")[:12]
                primeras = doc.strip().split(NL)
                preview = ""
                for linea in primeras:
                    if linea.startswith("Tema:"):
                        preview = linea[5:].strip()[:80]
                        break
                entrada = f"- {sesion_id}: "
                if preview:
                    entrada += " \"" + preview + "\" "
                entrada += f"[{interacciones}t, {formatear_ts(ts_end)}]"
                lineas_resumenes.append(entrada)

            partes.append(NL.join(lineas_resumenes))
    except Exception as exc:
        print(f"Memory: error en Capa I: {exc}", file=sys.stderr)

    # =====================================================================
    # CAPA II: Memoria Jerarquica (4 niveles)
    # =====================================================================
    AHORA = time.time()
    JERARQUIA = [
        ("inmediata", 0, 3600, 4),
        ("reciente", 3600, 86400, 3),
        ("semanal", 86400, 604800, 2),
        ("historica", 604800, float("inf"), 1),
    ]

    try:
        results = collection.query(
            query_texts=[query], n_results=TOP_K_HIERARCHY,
            include=["documents", "distances", "metadatas"],
        )

        docs_raw = results.get("documents", [[]])[0]
        dists_raw = results.get("distances", [[]])[0]
        metas_raw = results.get("metadatas", [[]])[0]

        if docs_raw:
            clasificados: dict[str, list[dict]] = {
                nivel: [] for nivel, _, _, _ in JERARQUIA
            }
            vistos: set[str] = set()

            for i, (doc, dist) in enumerate(zip(docs_raw, dists_raw)):
                score = 1.0 - dist
                contenido = doc.strip()
                if score < UMBRAL_RELEVANCIA or contenido in vistos:
                    continue
                vistos.add(contenido)

                meta = (
                    metas_raw[i]
                    if i < len(metas_raw) and metas_raw[i]
                    else {}
                )
                tipo = meta.get("tipo", "general")
                if tipo in ("session_start", "session_summary"):
                    continue

                ts_item = meta.get("ts", 0) or 0
                edad = AHORA - ts_item

                for nivel, min_e, max_e, _ in JERARQUIA:
                    if min_e <= edad < max_e:
                        clasificados[nivel].append({
                            "contenido": contenido,
                            "ts": ts_item,
                            "tipo": tipo,
                            "score": score,
                            "cwd": meta.get("cwd", ""),
                            "project": meta.get("project", ""),
                            "sesion": meta.get("sesion", "") or "sin-sesion",
                        })
                        break

            seleccion: list[dict] = []
            for nivel, _, _, cupo in JERARQUIA:
                items = clasificados.get(nivel, [])
                items.sort(key=lambda x: x["score"], reverse=True)
                seleccion.extend(items[:cupo])

            if seleccion:
                sesiones: dict[str, list[dict]] = defaultdict(list)
                for m in seleccion:
                    sesiones[m["sesion"]].append(m)

                sesiones_ordenadas = sorted(
                    sesiones.items(),
                    key=lambda kv: max((m["ts"] for m in kv[1]), default=0),
                    reverse=True,
                )

                bloque = []
                for sesion_id, items in sesiones_ordenadas:
                    items.sort(key=lambda m: m["ts"])
                    ts_min = formatear_ts(items[0]["ts"])
                    ts_max = formatear_ts(items[-1]["ts"])
                    etiqueta = sesion_id[:12]

                    bloque.append(
                        f"--- Sesion {etiqueta} ({ts_min} -> {ts_max}) ---"
                    )
                    for m in items:
                        rol = (
                            "[U]" if m["tipo"] == "usuario"
                            else "[A]" if m["tipo"] == "asistente"
                            else "[S]" if m["tipo"] == "session_start"
                            else "[?]"
                        )
                        t_tag = formatear_ts(m["ts"])
                        cwd_tag = f" [{m['cwd']}]" if m.get("cwd") else ""
                        prj_tag = f" [{m['project']}]" if m.get("project") and m["project"] != "default" else ""
                        bloque.append(
                            f"  {rol} {m['contenido']}{cwd_tag}{prj_tag} "
                            f"~{t_tag} ({m['score']:.2f})"
                        )

                if bloque:
                    partes.append("### Memorias por sesion")
                    partes.append(NL.join(bloque))
    except Exception as exc:
        print(f"Memory: error en Capa II: {exc}", file=sys.stderr)

    # =====================================================================
    # OUTPUT
    # =====================================================================
    if not partes:
        print("Memory: sin contenido para inyectar", file=sys.stderr)
        print(json.dumps({}))
        return

    contexto = NL.join(partes)

    # Circuit breaker: solo trunca si hay un caso patologico (+20k chars)
    if len(contexto) > MAX_CTX_CHARS:
        contexto = contexto[:MAX_CTX_CHARS - 80] + NL + "... (contexto truncado por circuit breaker)"

    # Marcar flag ANTES de imprimir salida
    marcar_inyectado(collection, session_id)
    print(f"Memory: flag marcado como inyectado para sesion {session_id[:16] if session_id else 'anon'}", file=sys.stderr)

    salida = {
        "hookSpecificOutput": {
            "hookEventName": "BeforeAgent",
            "additionalContext": (
                NL + "## Continuidad Memory" + NL + contexto + NL
            ),
        }
    }
    print(json.dumps(salida))


if __name__ == "__main__":
    main()
