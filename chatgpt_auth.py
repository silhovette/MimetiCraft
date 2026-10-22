"""OpenAI Sign in with ChatGPT for a local OSS app.

Uses the documented dynamic-agent OAuth 2.0 + OIDC + PKCE flow.
Tokens are kept in the OS credential vault with keyring; metadata in user home.
No ChatGPT web cookies, copied Codex tokens, or private API routes.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
import uuid
import webbrowser

AUTH_URL = 'https://auth.openai.com/api/accounts/authorize'
TOKEN_URL = 'https://auth.openai.com/api/accounts/oauth/token'
JWKS_URL = 'https://auth.openai.com/.well-known/jwks.json'
ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
SERVICE = 'video2datapack_siwc_v03'
APP_NAME = 'MimetiCraft'


def _deps():
    try:
        import httpx
        import jwt
        import keyring
        return httpx, jwt, keyring
    except ImportError as exc:
        raise RuntimeError('Missing login dependency. Double-click START_WINDOWS.bat to install into .venv') from exc


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


def authorization_url(*, client_id: str, redirect_uri: str, state: str, nonce: str,
                      verifier: str, host_id: str, id_token_hint: str | None = None) -> str:
    params = {
        'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'scope': SCOPES, 'resource': RESOURCE, 'state': state, 'nonce': nonce,
        'code_challenge_method': 'S256', 'code_challenge': pkce_challenge(verifier),
        'ext_agent_host_id': host_id,
    }
    if client_id == 'dynamic_agent_client':
        params['agent_name_hint'] = APP_NAME
    elif id_token_hint:
        params['id_token_hint'] = id_token_hint
    return AUTH_URL + '?' + urlencode(params)


def _safe_request_error(exc: Exception) -> str:
    """Never reveal returned tokens, OAuth codes or sensitive request URLs."""
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    if status:
        return f'OpenAI authorization request failed (HTTP {status}); check login/permissions and try again.'
    return f'OpenAI authorization/network error ({type(exc).__name__}); check your network and retry.'


def _write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    if os.name != 'nt':
        os.chmod(temp, 0o600)
    os.replace(temp, path)


@dataclass(frozen=True)
class Account:
    client_id: str
    subject: str
    email: str


class ChatGPTAuth:
    def __init__(self, data_dir: Path | None = None):
        self.path = (data_dir or Path.home() / '.video2datapack') / 'registrations.json'
        if self.path.exists():
            self.config = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.config = {'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'accounts': [], 'active': None}
            self._save()
        self._token_lock = threading.RLock()

    def _save(self):
        _write_json(self.path, self.config)

    def accounts(self) -> list[Account]:
        return [Account(x['client_id'], x['subject'], x.get('email') or 'ChatGPT account') for x in self.config['accounts']]

    def selected(self) -> Account | None:
        return next((a for a in self.accounts() if a.client_id == self.config.get('active')), None)

    def select(self, client_id: str):
        if client_id not in {x.client_id for x in self.accounts()}:
            raise ValueError('Unknown ChatGPT registration')
        self.config['active'] = client_id
        self._save()

    @staticmethod
    def _vault():
        return _deps()[2]

    def _read_tokens(self, client_id: str) -> dict | None:
        vault = self._vault()
        value = vault.get_password(SERVICE, client_id)
        if not value:
            return None
        record = json.loads(value)
        if '_vault_chunks' not in record:
            return record  # Backward compatible with small, single-entry records.
        parts = []
        for service in self._chunk_services(client_id, record):
            part = vault.get_password(service, client_id)
            if part is None:
                raise RuntimeError('Saved login is incomplete. Sign in again.')
            parts.append(part)
        return json.loads(''.join(parts))

    @staticmethod
    def _chunk_services(client_id: str, record: dict) -> list[str]:
        if '_vault_chunks' not in record:
            return []
        count, generation = record['_vault_chunks'], record.get('_vault_generation')
        if (type(count) is not int or not 1 <= count <= 1024 or
                not isinstance(generation, str) or len(generation) != 32 or
                any(c not in '0123456789abcdef' for c in generation)):
            raise RuntimeError('Saved login metadata is invalid. Sign in again.')
        return [f'{SERVICE}.chunk.{client_id}.{generation}.{i}' for i in range(count)]

    @staticmethod
    def _remove_chunks(vault, client_id: str, services: list[str]):
        for service in services:
            try:
                vault.delete_password(service, client_id)
            except Exception:
                # A cleanup failure must not discard a committed rotated token.
                pass

    def _save_tokens(self, client_id: str, record: dict):
        vault = self._vault()
        previous = vault.get_password(SERVICE, client_id)
        old_services = self._chunk_services(client_id, json.loads(previous)) if previous else []
        payload = json.dumps(record, ensure_ascii=True)
        services = []
        try:
            # Windows Credential Manager permits only 2560 bytes per blob.
            # keyring writes strings as UTF-16, so keep each ASCII part <=2000 bytes.
            if len(payload) > 1000:
                parts = [payload[i:i + 1000] for i in range(0, len(payload), 1000)]
                manifest = {'_vault_chunks': len(parts), '_vault_generation': uuid.uuid4().hex}
                services = self._chunk_services(client_id, manifest)
                for service, part in zip(services, parts):
                    vault.set_password(service, client_id, part)
                payload = json.dumps(manifest)
            # Publish only after every chunk has been saved successfully.
            vault.set_password(SERVICE, client_id, payload)
        except Exception:
            self._remove_chunks(vault, client_id, services)
            raise
        self._remove_chunks(vault, client_id, old_services)

    def _delete_tokens(self, client_id: str):
        vault = self._vault()
        value = vault.get_password(SERVICE, client_id)
        if value:
            services = self._chunk_services(client_id, json.loads(value))
            vault.delete_password(SERVICE, client_id)
            self._remove_chunks(vault, client_id, services)

    @staticmethod
    def _validate_id_token(id_token: str, client_id: str, nonce: str) -> dict:
        _, jwt, _ = _deps()
        signing_key = jwt.PyJWKClient(JWKS_URL).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, signing_key.key,
                            algorithms=['RS256', 'ES256'], audience=client_id,
                            issuer=ISSUER, leeway=5, options={'require': ['sub', 'exp', 'iat']})
        if claims.get('nonce') != nonce or not claims.get('sub'):
            raise RuntimeError('Login identity verification failed (nonce/subject).')
        return claims

    @staticmethod
    def _exchange(form: dict) -> dict:
        httpx, _, _ = _deps()
        try:
            response = httpx.post(TOKEN_URL, data=form, timeout=35)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(_safe_request_error(exc)) from None
        if not isinstance(payload, dict) or not payload.get('access_token'):
            raise RuntimeError('Authorization response did not contain an access token.')
        return payload

    def sign_in(self, *, add_account: bool = False, timeout: int = 240) -> Account:
        """Run in a background thread (not on Tk's UI thread)."""
        existing = self.selected() if not add_account else None
        client_id = existing.client_id if existing else 'dynamic_agent_client'
        previous = self._read_tokens(client_id) if existing else None
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        done = threading.Event()
        callback = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != '/auth/callback':
                    self.send_error(404)
                    return
                if done.is_set():
                    self.send_error(409)
                    return
                values = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
                if not secrets.compare_digest(values.get('state', ''), state):
                    self.send_error(400, 'Invalid state')
                    return
                callback.update(values)
                done.set()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(b'<!doctype html><html><meta charset="utf-8"><title>MimetiCraft</title><body><h3>Authentication received.</h3><p>Return to MimetiCraft. You can close this tab.</p></body></html>')

            def log_message(self, fmt, *args):
                # Never log callback URI; it contains the authorization code.
                return

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        redirect_uri = f'http://127.0.0.1:{server.server_port}/auth/callback'
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        listener.start()
        try:
            url = authorization_url(client_id=client_id, redirect_uri=redirect_uri,
                                    state=state, nonce=nonce, verifier=verifier,
                                    host_id=self.config['host_id'],
                                    id_token_hint=previous.get('id_token') if previous else None)
            if not webbrowser.open(url):
                raise RuntimeError('Could not open your browser for ChatGPT login. Check default browser settings.')
            if not done.wait(timeout):
                raise TimeoutError('ChatGPT login timed out. Click Continue with ChatGPT to try again.')
        finally:
            server.shutdown()
            server.server_close()
            listener.join(timeout=2)

        if callback.get('error'):
            raise RuntimeError('ChatGPT sign-in was declined or canceled; no AI request was made.')
        code = callback.get('code')
        issued = callback.get('client_id')
        if not code:
            raise RuntimeError('No authorization code was returned.')
        if existing:
            if issued and issued != client_id:
                raise RuntimeError('ChatGPT returned a different registered client ID; sign-in canceled.')
            issued = client_id
        elif not issued or issued == 'dynamic_agent_client':
            raise RuntimeError('ChatGPT did not return an issued OAuth client ID.')
        tokens = self._exchange({'grant_type': 'authorization_code', 'client_id': issued,
                                 'code': code, 'redirect_uri': redirect_uri,
                                 'code_verifier': verifier, 'resource': RESOURCE})
        claims = self._validate_id_token(tokens['id_token'], issued, nonce)
        if existing and existing.subject != claims['sub']:
            raise RuntimeError('The signed-in account does not match the selected registration.')
        scopes = set(str(tokens.get('scope') or '').split())
        if 'chatgpt.tokens.use.direct' not in scopes or 'resource.invoke' not in scopes:
            raise RuntimeError('You signed in but did not authorize ChatGPT plan usage. Reconnect and grant AI access.')
        if not tokens.get('refresh_token'):
            raise RuntimeError('Refresh token missing; ChatGPT plan access was not saved.')
        record = {**tokens, 'saved_at': time.time(), 'subject': claims['sub'],
                  'email': claims.get('email', ''), 'scopes': sorted(scopes)}
        self._save_tokens(issued, record)
        account = Account(issued, claims['sub'], claims.get('email') or 'ChatGPT account')
        if issued not in {a.client_id for a in self.accounts()}:
            self.config['accounts'].append(vars(account))
        self.config['active'] = issued
        self._save()
        return account

    def access_token(self) -> str:
        with self._token_lock:
            account = self.selected()
            if not account:
                raise RuntimeError('Not signed in. Click Continue with ChatGPT first.')
            tokens = self._read_tokens(account.client_id)
            if not tokens:
                raise RuntimeError('No saved session. Click Continue with ChatGPT to sign in again.')
            if 'chatgpt.tokens.use.direct' not in set(tokens.get('scopes', [])):
                raise RuntimeError('ChatGPT plan permission is missing; sign in again and grant permission.')
            if time.time() < float(tokens['saved_at']) + float(tokens.get('expires_in', 3600)) - 120:
                return tokens['access_token']
            if not tokens.get('refresh_token'):
                raise RuntimeError('Session expired. Sign in again.')
            refreshed = self._exchange({'grant_type': 'refresh_token',
                                        'client_id': account.client_id,
                                        'refresh_token': tokens['refresh_token'],
                                        'resource': RESOURCE})
            if not refreshed.get('refresh_token'):
                raise RuntimeError('Token renewal did not return a replacement refresh token.')
            merged = {**tokens, **refreshed, 'saved_at': time.time(),
                      'scopes': str(refreshed.get('scope') or ' '.join(tokens['scopes'])).split()}
            if 'chatgpt.tokens.use.direct' not in merged['scopes']:
                raise RuntimeError('ChatGPT plan access was removed. Sign in again.')
            self._save_tokens(account.client_id, merged)
            return merged['access_token']

    def models(self) -> list[tuple[str, str]]:
        httpx, _, _ = _deps()
        token = self.access_token()
        try:
            response = httpx.get(RESOURCE + '/models', headers={'Authorization': 'Bearer ' + token}, timeout=35)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(_safe_request_error(exc)) from None
        results = []
        for item in data.get('models', []):
            if item.get('visibility') == 'list' and item.get('slug'):
                results.append((str(item.get('display_name') or item['slug']), str(item['slug'])))
        if not results:
            raise RuntimeError('This ChatGPT connection returned no available listed models.')
        return results

    def sign_out(self) -> str:
        """Revoke active refresh session and remove saved credentials; retain registration."""
        account = self.selected()
        if not account:
            return 'No signed-in account selected.'
        tokens = self._read_tokens(account.client_id)
        confirmed = False
        if tokens and tokens.get('refresh_token'):
            httpx, _, _ = _deps()
            try:
                discovery = httpx.get(ISSUER + '/.well-known/openid-configuration', timeout=15)
                discovery.raise_for_status()
                revocation = discovery.json().get('revocation_endpoint')
                if revocation and urlparse(revocation).scheme == 'https' and urlparse(revocation).hostname == 'auth.openai.com':
                    r = httpx.post(revocation, data={'token': tokens['refresh_token'],
                                                    'token_type_hint': 'refresh_token',
                                                    'client_id': account.client_id}, timeout=15)
                    confirmed = r.status_code == 200
            except Exception:
                pass
        self._delete_tokens(account.client_id) if tokens else None
        return ('Signed out; remote session revoked.' if confirmed else
                'Local sign-out complete. Remote revocation could not be confirmed; disconnect in ChatGPT Settings if needed.')
