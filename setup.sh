#!/usr/bin/env bash
# setup.sh — Instalar dependencias y enlazar extension Memory
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

echo "[Memory] Creando entorno virtual..."
python3 -m venv "$VENV"

echo "[Memory] Instalando chromadb..."
"$VENV/bin/pip" install --quiet chromadb

echo "[Memory] Probando modelo de embeddings..."
"$VENV/bin/python3" -c "
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
ef = DefaultEmbeddingFunction()
v = ef(['test'])
print(f'Embedding OK — dimension: {len(v[0])}')
"

echo "[Memory] Enlazando extension al CLI..."
EXTENSIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/deepseek-cli/extensions"
mkdir -p "$EXTENSIONS_DIR"
ln -sfn "$DIR" "$EXTENSIONS_DIR/zora-memory"

echo "[Memory] Extension instalada. El proximo inicio cargara Memory."

echo ""
echo "═══════════════════════════════════════════"
echo "  Zora Memory — Memoria persistente lista"
echo "═══════════════════════════════════════════"
echo ""
echo "  Extension path: $DIR"
echo "  Para desinstalar:"
echo "    gemini extensions uninstall zora-memory"
echo "═══════════════════════════════════════════"
