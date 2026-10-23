#!/usr/bin/env python3
"""One-time Google Health authorisation. Run on your own machine:

    python3 tools/health_auth.py ~/Downloads/client_secret_XXXX.json

It opens Google's consent page in your browser, catches the redirect on localhost, exchanges the code
for a refresh token, and stores everything as GitHub Actions secrets with `gh secret set`.
Needs an OAuth client of type "Desktop app" from the Google Cloud console.
"""
import http.server, json, secrets, subprocess, sys, urllib.parse, urllib.request, webbrowser

SCOPES = ' '.join('https://www.googleapis.com/auth/googlehealth.' + s for s in
                  ('activity_and_fitness.readonly', 'sleep.readonly', 'health_metrics_and_measurements.readonly'))
PORT = 8765
REDIRECT = f'http://127.0.0.1:{PORT}/'

if len(sys.argv) < 2: sys.exit(__doc__)
raw = json.load(open(sys.argv[1]))
cfg = raw.get('installed') or raw.get('web') or raw
cid, csec = cfg['client_id'], cfg['client_secret']
if 'web' in raw and REDIRECT not in (cfg.get('redirect_uris') or []):
    print(f'Note: this is a "Web application" client. Google will reject the login (redirect_uri_mismatch) unless\n'
          f'{REDIRECT} is listed under its Authorized redirect URIs in the Cloud console — or create a "Desktop app" client instead.\n')
state = secrets.token_urlsafe(16)
url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
    'client_id': cid, 'redirect_uri': REDIRECT, 'response_type': 'code', 'scope': SCOPES,
    'access_type': 'offline', 'prompt': 'consent', 'state': state,
})
code = {}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        ok = q.get('state', [''])[0] == state and 'code' in q
        if ok: code['v'] = q['code'][0]
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
        self.wfile.write(b'<h2>Done, you can close this tab.</h2>' if ok else b'<h2>Something went wrong, try again.</h2>')
    def log_message(self, *a): pass


print('Opening Google consent in your browser. If nothing opens, paste this URL:\n\n' + url + '\n')
webbrowser.open(url)
srv = http.server.HTTPServer(('127.0.0.1', PORT), H)
while 'v' not in code: srv.handle_request()

form = urllib.parse.urlencode({'code': code['v'], 'client_id': cid, 'client_secret': csec,
                               'redirect_uri': REDIRECT, 'grant_type': 'authorization_code'}).encode()
tok = json.load(urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', data=form)))
rt = tok.get('refresh_token')
if not rt: sys.exit('No refresh token returned — revoke the app at myaccount.google.com/permissions and run again.')
print('Got a refresh token. Storing GitHub Actions secrets with gh …')
for k, v in {'GHEALTH_CLIENT_ID': cid, 'GHEALTH_CLIENT_SECRET': csec, 'GHEALTH_REFRESH_TOKEN': rt}.items():
    subprocess.run(['gh', 'secret', 'set', k, '--body', v], check=True)
print('\nSecrets set. Still needed once: gh secret set GIST_TOKEN   (your gist token, paste when prompted)')
print('Then run the workflow: gh workflow run health.yml')
