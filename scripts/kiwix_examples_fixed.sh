#!/bin/bash

# Kiwix Research Examples - Final Fixed Version
# Make sure KIWIX_URL is set and kiwix-serve is running

export KIWIX_URL="http://127.0.0.1:8080"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"

echo "=== Kiwix Research Examples ==="
echo
echo "1. Interactive mode (corrected host order):"
echo "python ollama_cli.py chat --host http://127.0.0.1:11434 --tools --system \"\$(cat ~/kiwix_system.txt)\" qwen3.2:3b"
echo

echo "2. One-shot question (corrected host order):"
echo "printf '%s\n' \"How do I install packages in Arch Linux?\" | python ollama_cli.py chat --host http://127.0.0.1:11434 --tools --system \"\$(cat ~/kiwix_system.txt)\" qwen3.2:3b"
echo

echo "3. Model management (pull missing models):"
echo "ollama pull llama3.2:3b  # Add this model if you want better reasoning"
echo

echo "4. System check:"
echo "curl -s \"\$OLLAMA_BASE_URL\" | head -1"
echo

echo "5. ZIM discovery test:"
echo "python -c \"import ollama_cli, json; print(json.loads(ollama_cli.tool_kiwix_list_zims())['count'])\" ZIMs available"
echo

echo "=== Usage Tips ==="
echo "- export KIWIX_URL=\"http://127.0.0.1:8080\""
echo "- export OLLAMA_BASE_URL=\"http://127.0.0.1:11434\""  
echo "- Use --host BEFORE chat command (important fix)"
echo "- Research loop will automatically find English content first"
echo "- Available models: qwen3.14b (fast), llama3.2:3b (better reasoning), qwen2.5:14b (coding)"
echo "- Pull additional models with: ollama pull <model-name>"