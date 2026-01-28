# SearxNG Setup for Web Tools

## Overview

The web tools (`web_search` and `web_open`) require a SearxNG instance to provide search functionality. SearxNG is a privacy-respecting metasearch engine.

## Quick Start (Docker Compose - Recommended)

### 1. Create Docker Compose File

Create `docker-compose.yml`:

```yaml
version: '3.8'
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    ports:
      - "8080:8080"
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml
    restart: unless-stopped
```

### 2. Create SearxNG Configuration

Create `settings.yml` in the same directory:

```yaml
# SearxNG settings for ollama-cli web tools
# See: https://docs.searxng.org/admin/settings.html

search:
  # IMPORTANT: Enable JSON format for API access
  formats:
    - html
    - json
  
  # Optional: Customize search behavior
  safe_search: 2  # Moderate filtering (0=none, 1=moderate, 2=strict)

# Optional: Limit results for better performance
server:
  limit_query: true
  default_query_params:
    format: json
```

### 3. Start SearxNG

```bash
docker-compose up -d
```

### 4. Test Configuration

```bash
curl "http://localhost:8080/search?q=test&format=json"
```

You should see JSON search results. If you get 403, check that `formats: [html, json]` is set correctly.

## Alternative Setup (Single Container - Advanced)

For advanced users who prefer single containers:

```bash
# Create settings.yml (same as above)
mkdir -p searxng-config
cat > searxng-config/settings.yml << 'EOF'
search:
  formats:
    - html
    - json
server:
  limit_query: true
EOF

# Run container
docker run -d \
  --name searxng \
  -p 8080:8080 \
  -v $(pwd)/searxng-config/settings.yml:/etc/searxng/settings.yml \
  searxng/searxng:latest
```

## Environment Configuration

Set the SearxNG URL for ollama-cli:

```bash
# Default (works with above setup)
export SEARXNG_URL="http://localhost:8080/search"

# Custom URL
export SEARXNG_URL="http://your-searxng-instance:8080/search"
```

## Troubleshooting

### 403 Forbidden Errors

**Error**: `403 Forbidden: JSON output likely disabled`

**Solution**: Ensure your `settings.yml` contains:
```yaml
search:
  formats:
    - html
    - json
```

**Why**: SearxNG requires JSON format to be explicitly enabled in settings for API access.

### Connection Refused

**Error**: `Connection refused` or `Max retries exceeded`

**Solutions**:
1. Check if SearxNG is running: `docker ps | grep searxng`
2. Verify port mapping: `curl http://localhost:8080`
3. Check environment variable: `echo $SEARXNG_URL`

### Search Results Empty

**Possible causes**:
1. Network connectivity issues in container
2. Search engines blocked or rate-limited
3. Invalid search query parameters

**Test**: Try a simple search through the web interface at `http://localhost:8080`

## Security Considerations

- **Network access**: SearxNG needs outbound internet to query search engines
- **Privacy**: SearxNG doesn't store search history by default
- **Rate limiting**: Consider enabling rate limits for public deployments
- **CORS**: Configure if accessing from different domains

## Performance Optimization

For better performance:

```yaml
# In settings.yml
server:
  # Enable caching
  public_instance: false
  
# Optional: Limit search engines for faster results
engines:
  - name: google
  - name: duckduckgo
  - name: bing
```

## Alternative Search Backends

The web tools are designed to work with SearxNG, but could be adapted for:
- Brave Search API
- Bing Search API  
- Google Custom Search API

This would require modifying the `WebTools.web_search()` method.