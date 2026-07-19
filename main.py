# TLS: route all Python SSL through the OS certificate store BEFORE any
# HTTPS-capable library (httpx, supabase, fastapi-mail, google-genai, …)
# creates its default SSL context. This makes corporate MITM proxies with a
# private CA "just work" — the CA is already trusted at the OS level via
# GPO/keychain and truststore surfaces it to Python. Safe on Vercel too:
# there it's a no-op because no proxy CA is installed.
import truststore
truststore.inject_into_ssl()

from app.factory import create_app

app = create_app()
