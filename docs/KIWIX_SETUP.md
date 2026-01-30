# Kiwix Setup

This project uses `kiwix-serve` to access offline ZIM content via HTTP.

## Quick setup (ZIMs in /mnt/HDD/zims)

1. Ensure your ZIMs are in `/mnt/HDD/zims` and you have a `library.xml` there.

2. Start the server:

```bash
kiwix-serve --port 8081 --library /mnt/HDD/zims/library.xml
```

3. Point ollama-cli at it:

```bash
export KIWIX_URL="http://127.0.0.1:8081"
export KIWIX_ZIM_DIR="/mnt/HDD/zims"
```

## How to pick `zim` ids

For `kiwix_*` tools, the `zim` parameter is the Kiwix content id.

In most setups it is the ZIM filename without the `.zim` suffix:

- `python.zim` -> `python`
- `wikipedia_en_all_nopic_2025-12.zim` -> `wikipedia_en_all_nopic_2025-12`

You can list what ollama-cli sees with:

```bash
ollama-cli chat <model> --tool kiwix_list_zims
```

Or call the tool directly via the tool runtime in your own code.
