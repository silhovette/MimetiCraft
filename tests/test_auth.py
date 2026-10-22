"""Network-free OAuth tests: fake redirect and fake token endpoint."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen

from chatgpt_auth import ChatGPTAuth, authorization_url, pkce_challenge, SCOPES

class Vault:
    def __init__(self):
        self.contents={}
    def get_password(self, service, key):
        return self.contents.get((service,key))
    def set_password(self, service, key, value):
        self.contents[(service,key)] = value
    def delete_password(self, service, key):
        del self.contents[(service,key)]

class AuthTests(unittest.TestCase):
    def test_large_credentials_roundtrip_rotation_and_delete(self):
        class WindowsSizedVault(Vault):
            def set_password(self, service, key, value):
                if len(value.encode('utf-16-le')) > 2560:
                    raise OSError('Credential blob too large')
                super().set_password(service, key, value)

        with tempfile.TemporaryDirectory() as root:
            auth = ChatGPTAuth(Path(root))
            vault = WindowsSizedVault()
            record = {'access_token': 'a' * 6000, 'refresh_token': 'r' * 3000,
                      'id_token': 'i' * 3000, 'email': '\u6d4b\u8bd5@example.org'}
            with patch.object(auth, '_vault', return_value=vault):
                auth._save_tokens('test-client', record)
                self.assertEqual(auth._read_tokens('test-client'), record)
                old_keys = set(vault.contents)
                rotated = {**record, 'refresh_token': 'new' * 1500}
                auth._save_tokens('test-client', rotated)
                self.assertEqual(auth._read_tokens('test-client'), rotated)
                from chatgpt_auth import SERVICE
                self.assertEqual(old_keys & set(vault.contents), {(SERVICE, 'test-client')})
                auth._delete_tokens('test-client')
                self.assertEqual(vault.contents, {})

    def test_failed_chunk_write_preserves_previous_credentials(self):
        class FailingVault(Vault):
            def set_password(self, service, key, value):
                if service.endswith('.2'):
                    raise OSError('Simulated write failure')
                super().set_password(service, key, value)

        with tempfile.TemporaryDirectory() as root:
            auth = ChatGPTAuth(Path(root))
            vault = FailingVault()
            with patch.object(auth, '_vault', return_value=vault):
                auth._save_tokens('test-client', {'access_token': 'old'})
                previous = dict(vault.contents)
                with self.assertRaises(OSError):
                    auth._save_tokens('test-client', {'access_token': 'x' * 6000})
                self.assertEqual(vault.contents, previous)
                self.assertEqual(auth._read_tokens('test-client'), {'access_token': 'old'})

    def test_url_contains_required_scopes_and_pkce(self):
        url=authorization_url(client_id='dynamic_agent_client',redirect_uri='http://127.0.0.1:3333/auth/callback',
                              state='abc',nonce='def',verifier='verifier',host_id='urn:uuid:abc')
        vals=parse_qs(urlparse(url).query)
        self.assertEqual(vals['scope'][0], SCOPES)
        self.assertEqual(vals['code_challenge'][0], pkce_challenge('verifier'))
        self.assertEqual(vals['agent_name_hint'][0], 'MimetiCraft')
        self.assertIn('chatgpt.tokens.use.direct', vals['scope'][0])

    def test_browser_registration_and_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            auth=ChatGPTAuth(Path(root))
            vault=Vault()
            def fake_browser(url):
                parsed=urlparse(url)
                params=parse_qs(parsed.query)
                self.assertEqual(params['client_id'], ['dynamic_agent_client'])
                target=params['redirect_uri'][0] + '?' + urlencode({
                    'code':'fakecode', 'state':params['state'][0], 'client_id':'oaiapp_test123'})
                def send():
                    with urlopen(target, timeout=3) as resp:
                        self.assertEqual(resp.status, 200)
                threading.Thread(target=send, daemon=True).start()
                return True
            tokens={'id_token':'signed-jwt-mock', 'access_token':'test-access', 'refresh_token':'test-refresh',
                    'expires_in':3600, 'scope':SCOPES}
            with (patch.object(auth, '_vault', return_value=vault),
                  patch('chatgpt_auth.webbrowser.open', side_effect=fake_browser),
                  patch.object(auth,'_exchange',return_value=tokens) as exchange,
                  patch.object(auth,'_validate_id_token',return_value={'sub':'subject1','email':'a@example.org'})):
                account=auth.sign_in(timeout=4)
                self.assertEqual(account.client_id,'oaiapp_test123')
                self.assertEqual(auth.access_token(),'test-access')
                self.assertEqual(auth.selected().email,'a@example.org')
                self.assertEqual(exchange.call_args.args[0]['client_id'], 'oaiapp_test123')
                self.assertEqual(exchange.call_args.args[0]['resource'], 'https://api.openai.com/v1')
                auth_records=json.loads((Path(root)/'registrations.json').read_text())
                self.assertNotIn('test-refresh',json.dumps(auth_records))
                # Expire the access token to exercise refresh-token rotation.
                from chatgpt_auth import SERVICE
                old=json.loads(vault.get_password(SERVICE,'oaiapp_test123'))
                old['saved_at']=0
                vault.set_password(SERVICE,'oaiapp_test123',json.dumps(old))
                exchange.return_value={'access_token':'new-access','refresh_token':'new-refresh',
                                       'expires_in':3600,'scope':SCOPES}
                self.assertEqual(auth.access_token(),'new-access')
                self.assertEqual(json.loads(vault.get_password(SERVICE,'oaiapp_test123'))['refresh_token'],'new-refresh')

if __name__=='__main__':
    unittest.main()
