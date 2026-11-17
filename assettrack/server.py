"""Small JSON HTTP application (Python standard library only)."""
from __future__ import annotations

import json
import mimetypes
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import Problem, Store

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 64 * 1024

class Handler(BaseHTTPRequestHandler):
    server_version = 'AssetTrack/0.1'

    def _send(self, status, payload, cookie=None):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        if cookie:
            self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        jar=cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie',''))
        except cookies.CookieError:
            return None
        return jar['assettrack_session'].value if 'assettrack_session' in jar else None

    def _actor(self):
        return self.server.store.actor(self._token())

    def _body(self):
        if self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json':
            raise Problem('Content-Type must be application/json',415)
        try:
            length=int(self.headers.get('Content-Length','0'))
        except ValueError:
            raise Problem('Invalid Content-Length') from None
        if length<1 or length>MAX_BODY:
            raise Problem('Request body is empty or too large',413)
        try:
            data=json.loads(self.rfile.read(length))
        except (UnicodeDecodeError,json.JSONDecodeError):
            raise Problem('Invalid JSON') from None
        if not isinstance(data,dict):
            raise Problem('Expected JSON object')
        return data

    def _origin_check(self):
        # Reject cross-site writes when Origin is supplied; SameSite cookies also help.
        origin=self.headers.get('Origin')
        host=self.headers.get('Host','')
        if origin and urlsplit(origin).netloc != host:
            raise Problem('Cross-origin request denied',403)

    def _serve_static(self,path):
        names={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path not in names:
            raise Problem('Not found',404)
        file=STATIC/names[path]
        data=file.read_bytes()
        ctype=mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type',ctype+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-cache')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path=urlsplit(self.path).path
        try:
            if not path.startswith('/api/'):
                return self._serve_static(path)
            user=self._actor()
            store=self.server.store
            if path=='/api/me': result=user
            elif path=='/api/dashboard': result=store.dashboard(user)
            elif path=='/api/users': result=store.users(user)
            elif path=='/api/assets': result=store.assets(user)
            elif path=='/api/assignments': result=store.assignments(user)
            elif path=='/api/events': result=store.events(user)
            elif path=='/api/transfers': result=store.transfers(user)
            elif path.startswith('/api/assets/'):
                parts=path.strip('/').split('/')
                if len(parts)!=3 or not parts[2].isdigit():
                    raise Problem('Not found',404)
                result=store.asset_detail(user,int(parts[2]))
            else: raise Problem('Not found',404)
            self._send(200,result)
        except Problem as ex:
            self._send(ex.status,{'error':ex.message})
        except Exception:
            self.log_error('Unexpected server error')
            self._send(500,{'error':'Internal server error'})

    def do_POST(self):
        path=urlsplit(self.path).path
        try:
            self._origin_check()
            data=self._body()
            store=self.server.store
            if path=='/api/login':
                token,user=store.authenticate(data.get('username',''),data.get('password',''))
                return self._send(200,user,
                    f'assettrack_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200')
            if path=='/api/logout':
                store.logout(self._token())
                return self._send(200,{'ok':True},'assettrack_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            actor=self._actor()
            if path=='/api/users': result=store.create_user(actor,data)
            elif path.startswith('/api/users/'):
                parts=path.strip('/').split('/')
                if len(parts)!=4 or not parts[2].isdigit() or parts[3]!='status':
                    raise Problem('Not found',404)
                result=store.set_user_active(actor,int(parts[2]),data.get('active'))
            elif path=='/api/assets': result=store.create_asset(actor,data)
            elif path.startswith('/api/assets/'):
                parts=path.strip('/').split('/')
                if len(parts)!=4 or not parts[2].isdigit():
                    raise Problem('Not found',404)
                asset_id=int(parts[2])
                if parts[3]=='assign': result=store.assign(actor,asset_id,data.get('employee_id'))
                elif parts[3]=='return': result=store.return_asset(actor,asset_id,data.get('condition'))
                elif parts[3]=='update': result=store.update_asset(actor,asset_id,data)
                elif parts[3]=='transfer':
                    result=store.request_transfer(actor,asset_id,data.get('employee_id'),data.get('reason'))
                else: raise Problem('Not found',404)
            elif path.startswith('/api/transfers/'):
                parts=path.strip('/').split('/')
                if len(parts)!=4 or not parts[2].isdigit():
                    raise Problem('Not found',404)
                transfer_id=int(parts[2])
                if parts[3]=='accept': result=store.accept_transfer(actor,transfer_id)
                elif parts[3]=='reject': result=store.reject_transfer(actor,transfer_id)
                elif parts[3]=='cancel': result=store.cancel_transfer(actor,transfer_id,data.get('reason'))
                else: raise Problem('Not found',404)
            else: raise Problem('Not found',404)
            self._send(200,result)
        except Problem as ex:
            self._send(ex.status,{'error':ex.message})
        except Exception:
            self.log_error('Unexpected server error')
            self._send(500,{'error':'Internal server error'})

    def log_message(self, format, *args):
        if getattr(self.server, "testing", False):
            return
        super().log_message(format, *args)

class AppServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):
        super().__init__(address,Handler)
        self.store=store
