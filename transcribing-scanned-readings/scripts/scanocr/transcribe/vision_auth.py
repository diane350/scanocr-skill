"""Signing in to Google for the Vision OCR without gcloud: you make one Desktop-app OAuth
client in your own Google Cloud project (a JSON file), sign in through the browser once, and
the login is kept in this computer's config folder. ``ocr`` uses it when it is there, and the
``gcloud auth application-default login`` login otherwise.

Google Cloud console > APIs & Services > Credentials > Create credentials > OAuth client ID >
Application type "Desktop app" > download the JSON. (The OAuth consent screen: "External", with
your own address as a test user. While it is in "Testing", Google ends the login after 7 days:
sign in again, or publish the consent screen.)"""
import json
import os
import sys
from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/cloud-vision']
CONFIG = Path(os.environ.get('APPDATA', Path.home()) if sys.platform == 'win32' else
              os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'scanocr'
TOKEN = CONFIG / 'google-login.json'
CLIENT = CONFIG / 'google-oauth-client.json'


def gcloud_login_file():
    """Where ``gcloud auth application-default login`` puts its login."""
    base = (Path(os.environ.get('APPDATA', Path.home())) / 'gcloud' if sys.platform == 'win32'
            else Path(os.environ.get('CLOUDSDK_CONFIG', Path.home() / '.config' / 'gcloud')))
    return base / 'application_default_credentials.json'


def status():
    """'app' (signed in here), 'gcloud' (a gcloud login or key file exists) or None."""
    if TOKEN.is_file():
        return 'app'
    key = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if (key and Path(key).is_file()) or gcloud_login_file().is_file():
        return 'gcloud'
    return None


def has_client():
    return CLIENT.is_file()


def save_client(path):
    """Keep a copy of the downloaded OAuth client JSON; SystemExit if it is not a Desktop one."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get('installed'), dict) or 'client_id' not in data['installed']:
        raise SystemExit(f'{path} is not a Desktop app OAuth client: in Google Cloud Credentials, '
                         'create an OAuth client ID of type "Desktop app" and download its JSON.')
    CONFIG.mkdir(parents=True, exist_ok=True)
    CLIENT.write_text(json.dumps(data), encoding='utf-8')
    os.chmod(CLIENT, 0o600)


def sign_in(client=None):
    """Sign in through the browser (the OAuth client: the one saved, or ``client``'s JSON,
    which is then saved) and keep the login."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if client:
        save_client(client)
    if not has_client():
        raise SystemExit('Choose the OAuth client JSON from your Google Cloud project first.')
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, prompt='consent',
                                  authorization_prompt_message='Opening your browser to sign in to Google...',
                                  success_message='Signed in. You can close this tab and return to the app.')
    CONFIG.mkdir(parents=True, exist_ok=True)
    data = json.loads(creds.to_json())
    project = json.loads(CLIENT.read_text(encoding='utf-8'))['installed'].get('project_id')
    if project:
        data['quota_project_id'] = project
    TOKEN.write_text(json.dumps(data), encoding='utf-8')
    os.chmod(TOKEN, 0o600)


def sign_out():
    TOKEN.unlink(missing_ok=True)


def credentials():
    """The app's own login, refreshed if it ran out; None if the app has not signed in."""
    if not TOKEN.is_file():
        return None
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError:
            raise SystemExit('Your Google sign-in has ended (Google ends it after 7 days while the '
                             'consent screen is in Testing). Sign in again.')
        data = json.loads(creds.to_json())
        data['quota_project_id'] = creds.quota_project_id
        TOKEN.write_text(json.dumps(data), encoding='utf-8')
    return creds
