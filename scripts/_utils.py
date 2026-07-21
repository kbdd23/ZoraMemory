"""Utilidades compartidas entre hooks de ZoraMemory."""

import time


def marcar_inyectado(collection, session_id: str) -> None:
    """Marca en ChromaDB que esta sesion ya recibio inyeccion de memoria."""
    if not session_id:
        return
    flag_id = f"inj_{session_id[:16]}"
    try:
        collection.upsert(
            ids=[flag_id],
            metadatas=[{
                "tipo": "inyeccion",
                "memoria_inyectada": 1,
                "sesion": session_id,
                "ts": time.time(),
            }],
            documents=[f"Flag de inyeccion para sesion {session_id}"],
        )
    except Exception:
        pass


def marcar_inyectado_reset(collection, session_id: str) -> None:
    """Resetea el flag para que la proxima sesion pueda inyectar."""
    if not session_id:
        return
    flag_id = f"inj_{session_id[:16]}"
    try:
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
    except Exception:
        pass
