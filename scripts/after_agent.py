#!/usr/bin/env python3
"""Memory - AfterAgent: Indexar la interaccion completa en BD vectorial.

Este hook se ejecuta UNA vez por turno completo (no por chunk),
ideal para indexar pares (mensaje usuario -> respuesta completa).

Lee:  stdin  -> JSON con prompt y prompt_response (AfterAgent event)
Escribe: stdout -> JSON con systemMessage informando indexacion
Stderr:      -> logs de diagnostico
"""
import hashlib
import json
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory", "chroma")


def extraer_proyecto(cwd: str) -> str:
    if not cwd:
        return "default"
    idx = cwd.find("/Proyectos/")
    if idx != -1:
        resto = cwd[idx + len("/Proyectos/"):]
        partes = [p for p in resto.split("/") if p]
        if len(partes) >= 3:
            return partes[2]
        if partes:
            return partes[-1]
    return cwd.rstrip("/").rsplit("/", 1)[-1] or "default"


def extraer_interaccion(input_data: dict) -> tuple[str, str]:
    """Extrae mensaje de usuario y respuesta del asistente.

    AfterAgent recibe:
      { "prompt": "<mensaje usuario>", "prompt_response": "<respuesta agente>", ... }
    Fallback a llm_request/llm_response por compatibilidad.
    """
    usuario = input_data.get("prompt", "")
    respuesta = input_data.get("prompt_response", "")

    # Fallback: formato AfterModel / legado
    if not usuario:
        llm_req = input_data.get("llm_request", {})
        messages = llm_req.get("messages", [])
        for m in reversed(messages):
            if m.get("role") == "user":
                usuario = m.get("content", "")
                break

    if not respuesta:
        llm_res = input_data.get("llm_response", {})
        respuesta = llm_res.get("content", "")

    return usuario, respuesta


def main() -> None:
    input_data = json.load(sys.stdin)

    usuario, respuesta = extraer_interaccion(input_data)

    if not usuario:
        print(json.dumps({"systemMessage": ""}))
        return

    import chromadb
    from chromadb.errors import NotFoundError
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    client = chromadb.PersistentClient(path=CHROMA_DIR)

    ef = DefaultEmbeddingFunction()

    try:
        collection = client.get_collection("hermes", embedding_function=ef)
    except NotFoundError:
        collection = client.create_collection(
            name="hermes",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

    timestamp = time.time()
    sesion = input_data.get("session_id", "")
    cwd = os.getcwd()

    def _id(tipo: str, contenido: str) -> str:
        h = hashlib.sha256(contenido.encode()).hexdigest()[:16]
        return f"{tipo}_{h}"

    docs: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    project = extraer_proyecto(cwd)

    usuario = usuario.strip()
    if usuario:
        docs.append(usuario)
        metadatas.append({"tipo": "usuario", "ts": timestamp, "sesion": sesion, "cwd": cwd, "project": project})
        ids.append(_id("u", usuario))

    respuesta = respuesta.strip()
    if respuesta and len(respuesta) > 20:
        docs.append(respuesta)
        metadatas.append({"tipo": "asistente", "ts": timestamp, "sesion": sesion, "cwd": cwd, "project": project})
        ids.append(_id("a", respuesta))

    if docs:
        collection.upsert(documents=docs, metadatas=metadatas, ids=ids)
        print(f"Memory: {len(docs)} recuerdos indexados", file=sys.stderr)

    count = collection.count()
    print(json.dumps({"systemMessage": f"Memory: {count} recuerdos totales"}))


if __name__ == "__main__":
    main()
