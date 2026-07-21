#!/usr/bin/env python3
"""Memory - AfterModel: Indexar la interaccion en la BD vectorial.

Lee:  stdin  → JSON con llm_request y llm_response
Escribe: stdout → JSON vacio {}
Stderr:      → logs de diagnostico
"""
import json
import os
import sys
import time
import uuid

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory", "chroma")


def extraer_interaccion(input_data: dict) -> tuple[str, str]:
    """Extrae mensaje de usuario y respuesta del asistente."""
    llm_req = input_data.get("llm_request", {})
    llm_res = input_data.get("llm_response", {})

    messages = llm_req.get("messages", [])
    respuesta = llm_res.get("content", "")

    usuario = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            usuario = m.get("content", "")
            break

    return usuario, respuesta


def main() -> None:
    input_data = json.load(sys.stdin)
    usuario, respuesta = extraer_interaccion(input_data)

    if not usuario:
        print(json.dumps({}))
        return

    import chromadb
    from chromadb.errors import NotFoundError
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    client = chromadb.PersistentClient(path=CHROMA_DIR)

    ef = DefaultEmbeddingFunction()

    try:
        collection = client.get_collection(
            "hermes", embedding_function=ef
        )
    except NotFoundError:
        collection = client.create_collection(
            name="hermes",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

    timestamp = time.time()
    doc_id = str(uuid.uuid4())
    cwd = os.getcwd()

    docs: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    # Indexar mensaje del usuario
    if usuario.strip():
        docs.append(usuario)
        metadatas.append(
            {
                "tipo": "usuario",
                "ts": timestamp,
                "sesion": input_data.get("session_id", ""),
                "cwd": cwd,
            }
        )
        ids.append(f"u_{doc_id}")

    # Indexar respuesta si es sustancial
    if respuesta.strip() and len(respuesta) > 20:
        docs.append(respuesta)
        metadatas.append(
            {
                "tipo": "asistente",
                "ts": timestamp,
                "sesion": input_data.get("session_id", ""),
                "cwd": cwd,
            }
        )
        ids.append(f"a_{doc_id}")

    if docs:
        collection.add(documents=docs, metadatas=metadatas, ids=ids)
        print(f"Memory: {len(docs)} recuerdos indexados", file=sys.stderr)

    print(json.dumps({}))


if __name__ == "__main__":
    main()
