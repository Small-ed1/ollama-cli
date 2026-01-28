#!/bin/bash

# Kiwix Research Usage Examples
# Make sure KIWIX_URL is set and kiwix-serve is running

export KIWIX_URL="http://127.0.0.1:8080"

echo "=== Kiwix Research Examples ==="
echo

echo "1. Interactive mode:"
echo "python ollama_cli.py chat --tools --system \"\$(cat ~/kiwix_system.txt)\" llama3.2:3b"
echo

echo "2. One-shot question:"
echo "printf '%s\n' \"How do I install packages in Arch Linux?\" | python ollama_cli.py chat --tools --system \"\$(cat ~/kiwix_system.txt)\" llama3.2:3b"
echo

echo "3. System check:"
echo "curl -s \"\$KIWIX_URL\" | head -1"
echo

echo "4. ZIM discovery test:"
echo "python -c \"import ollama_cli, json; print(json.loads(ollama_cli.tool_kiwix_list_zims())['count'])\" ZIMs available"
echo

echo "=== Usage Tips ==="
echo "- Use archwiki for Arch Linux specific questions"
echo "- Use git/python/linux for documentation"  
echo "- Use askubuntu for practical Ubuntu solutions (often transferable)"
echo "- Use serverfault for sysadmin depth"
echo "- Use wikipedia_* for general background"
echo "- Research loop will automatically find English content first"