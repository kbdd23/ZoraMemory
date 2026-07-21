#!/usr/bin/env python3
"""Memory - SessionStart: Inicializar ChromaDB y gestionar rewinds.

Detecta rewinds (retrocesos temporales dentro de una sesion) y purga
las memorias huérfanas para evitar ruido contextual.

Logica de deteccion:
  - Si existe un session_start previo de esta sesion (<5min)
  - Y NO existe un session_summary (session_end no se ejecuto)
  - Entonces: la sesion fue rebobinada o matada antes de cerrar
  - Accion: purgar TODAS las memorias de esta session_id

Esto garantiza que un rewind sea invisible: como si nunca hubiera
ocurrido la rama descartada.

Lee:  stdin  -> JSON con datos de sesion (opcional)
Escribe: stdout -> JSON con systemMessage
Stderr:      -> logs de diagnostico
"""
import json
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSION_DIR = os.path.dirname(SCRIPTS_DIR)
CHROMA_DIR = os.path.join(EXTENSION_DIR, "memory", "chroma")

REWIND_VENTANA_SEG = 300  # 5 minutos


def _contar_sesion(collection, session_id: str) -> int:
    """Cuenta cuantos documentos existen para esta session_id."""
    try:
        res = collection.get(
            where={"sesion": session_id},
            include=["metadatas"],
        )
        return len(res.get("ids", []))
    except Exception:
        return 0


def _hay_resumen_sesion(collection, session_id: str) -> bool:
    """Verifica si existe un session_summary para esta sesion
    (indicador de que session_end se ejecuto correctamente)."""
    try:
        res = collection.get(
            where={"$and": [{"tipo": "session_summary"}, {"sesion": session_id}]},
            include=["ids"],
        )
        return len(res.get("ids", [])) > 0
    except Exception:
        return False


def _purgar_sesion(collection, session_id: str) -> int:
    """Elimina todas las memorias de esta session_id.
    Retorna cantidad de registros eliminados."""
    try:
        res = collection.get(
            where={"sesion": session_id},
            include=["metadatas"],
        )
        ids = res.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception as exc:
        print(f"Memory: error en purge: {exc}", file=sys.stderr)
        return 0


def _resetear_flag_inyeccion(collection, session_id: str) -> None:
    """Marca que la sesion aun no ha recibido inyeccion de memoria."""
    if not session_id:
        return
    flag_id = f"inj_{session_id[:16]}"
    collection.upsert(
        ids=[flag_id],
        metadatas=[{
            "tipo": "inyeccion",
            "memoria_inyectada": 0,
            "sesion": session_id,
            "ts": time.time(),
        }],
        documents=[f"Flag de inyeccion para sesion {session_id}"],
    )


def main() -> None:
    os.makedirs(CHROMA_DIR, exist_ok=True)

    import chromadb
    from chromadb.errors import NotFoundError
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    client = chromadb.PersistentClient(path=CHROMA_DIR)

    try:
        collection = client.get_collection("hermes")
    except NotFoundError:
        ef = DefaultEmbeddingFunction()
        collection = client.create_collection(
            name="hermes",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        _ = ef(["init"])
        print("Memory: coleccion creada, modelo descargado", file=sys.stderr)

    # --- Leer datos de sesion ---
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        input_data = {}

    timestamp = time.time()
    session_id = input_data.get("session_id", "")

    # --- Resetear flag de inyeccion para esta sesion ---
    _resetear_flag_inyeccion(collection, session_id)

    # --- Detectar rewind ---
    if session_id:
        try:
            existentes = collection.get(
                where={"$and": [{"tipo": "session_start"}, {"sesion": session_id}]},
                include=["metadatas"],
            )
            metas_existentes = existentes.get("metadatas", [])
            inicio_reciente = False
            for meta in metas_existentes:
                ts_existente = meta.get("ts", 0)
                if isinstance(ts_existente, (int, float)) and (
                   (timestamp - ts_existente) < REWIND_VENTANA_SEG):
                    inicio_reciente = True
                    break

            if inicio_reciente:
                completo = _hay_resumen_sesion(collection, session_id)
                if not completo:
                    # Rewind o crash: no hubo session_end
                    n_purgados = _purgar_sesion(collection, session_id)
                    # Resetear flag de inyeccion para que re-inyecte
                    _resetear_flag_inyeccion(collection, session_id)
                    print(
                        f"Memory: rewind detectado, {n_purgados} recuerdos "
                        f"purgados de sesion {session_id[:12]}",
                        file=sys.stderr,
                    )
        except Exception as exc:
            print(f"Memory: error detectando rewind: {exc}", file=sys.stderr)

    # --- Insertar marcador de inicio de sesion ---
    sid = f"ss_{session_id[:16] if session_id else 'anon'}_{int(timestamp)}"

    collection.upsert(
        documents=[f"Inicio de sesion {session_id or 'sin-id'} [{int(timestamp)}]"],
        metadatas=[{
            "tipo": "session_start",
            "ts": timestamp,
            "sesion": session_id,
            "cwd": os.getcwd(),
        }],
        ids=[sid],
    )

    count = collection.count()
    print(
        json.dumps({
            "systemMessage": f"Memory memoria lista ({count} recuerdos)"
        })
    )


if __name__ == "__main__":
    main()
