# Runtime hook: pre-load stdlib http before webview imports anything
# Fixes: circular import crash in frozen EXE
import sys, importlib
for _m in [
    'http','http.server','http.client','http.cookies','http.cookiejar',
    'wsgiref','wsgiref.simple_server','wsgiref.util',
    'wsgiref.handlers','wsgiref.headers','wsgiref.validate',
    'urllib','urllib.parse','urllib.request','urllib.error',
    'email','email.parser','email.message','email.feedparser',
    'email.policy','email.header','email.charset','email.encoders',
    'email.utils','html','html.parser','socket','ssl','socketserver',
]:
    if _m not in sys.modules:
        try: importlib.import_module(_m)
        except: pass
