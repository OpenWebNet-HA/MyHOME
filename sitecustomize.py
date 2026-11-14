"""Early runtime hooks for Home Assistant and pytest compatibility.

Home Assistant dev (>= 2026.11) aliases httpx/httpcore to httpx2/httpcore2.
When pytest loads setuptools entrypoint plugins, third-party test plugins
(e.g., respx and pytest-homeassistant-custom-component) import httpx before
homeassistant is imported, triggering:
  RuntimeError: httpx was already imported; call `alias_httpx()` before any `import httpx`.
Aliasing httpx process-wide at Python startup ensures full forward compatibility.
"""

try:
    import httpx2  # noqa: F401

    httpx2.alias_httpx()
except (ImportError, AttributeError):
    pass
