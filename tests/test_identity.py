"""Anmeldung über den Identity-Server (OpenID Connect) mit einem nachgebauten Server."""

import base64
import hashlib
import json
import secrets
import time
from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ustva import einstellungen, identity
from ustva.config import Config
from ustva.web import create_app

ISSUER = "https://identity.example.test"
APP = "https://finanz-o-mat.example.test"
# Secret zur Laufzeit erzeugen, damit keine festen Zugangsdaten im Code stehen.
SECRET = secrets.token_urlsafe(16)


def _b64(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


class FakeIdentity:
    """Gerade so viel vom Identity-Server, wie das Finanztool braucht."""

    def __init__(self):
        self.schluessel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.konten = {
            "konto-1": {"name": "Erika Muster", "role": ["user", "finanzen"]},
            "konto-2": {"name": "Max Beispiel", "role": ["user", "finanzen"]},
            "konto-3": {"name": "Neu Registriert", "role": "user"},
        }
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, str] = {}  # Refresh Token -> sub
        self.widerrufen: list[str] = []
        self.refresh_anzahl = 0
        self.offline = False

    def id_token(self, sub: str, nonce: str | None = None, schluessel=None) -> str:
        jetzt = int(time.time())
        claims = {"iss": ISSUER, "aud": "finanz-o-mat", "sub": sub, "iat": jetzt, "exp": jetzt + 1200,
                  "name": self.konten[sub]["name"], "role": self.konten[sub]["role"]}
        if nonce:
            claims["nonce"] = nonce
        return jwt.encode(claims, schluessel or self.schluessel, algorithm="RS256", headers={"kid": "k1"})

    def code(self, autorisierung: str, sub: str = "konto-1") -> tuple[str, str]:
        """Simuliert die Anmeldung im Browser: gibt (code, state) für die Rückruf-Adresse."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(autorisierung).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        assert q["redirect_uri"] == APP + "/api/identity/rueckruf"
        assert "offline_access" in q["scope"] and "roles" in q["scope"]
        code = secrets.token_urlsafe(8)
        self.codes[code] = {"sub": sub, "nonce": q["nonce"], "challenge": q["code_challenge"]}
        return code, q["state"]

    def _token(self, form: dict) -> httpx.Response:
        if form.get("client_id") != "finanz-o-mat" or form.get("client_secret") != SECRET:
            return httpx.Response(400, json={"error": "invalid_client"})
        if form["grant_type"] == "authorization_code":
            c = self.codes.pop(form["code"], None)
            if not c or _b64(hashlib.sha256(form["code_verifier"].encode()).digest()) != c["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            sub, nonce = c["sub"], c["nonce"]
        else:
            self.refresh_anzahl += 1
            sub = self.refresh.pop(form["refresh_token"], None)
            if not sub:
                return httpx.Response(400, json={"error": "invalid_grant"})
            nonce = None
        rt = secrets.token_urlsafe(8)
        self.refresh[rt] = sub
        return httpx.Response(200, json={"id_token": self.id_token(sub, nonce), "refresh_token": rt,
                                         "access_token": "at", "token_type": "Bearer"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        pfad = request.url.path
        if pfad == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER + "/", "authorization_endpoint": ISSUER + "/connect/authorize",
                "token_endpoint": ISSUER + "/connect/token", "userinfo_endpoint": ISSUER + "/connect/userinfo",
                "end_session_endpoint": ISSUER + "/connect/logout", "revocation_endpoint": ISSUER + "/connect/revoke",
                "jwks_uri": ISSUER + "/.well-known/jwks"})
        if pfad == "/.well-known/jwks":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.schluessel.public_key()))
            return httpx.Response(200, json={"keys": [jwk | {"kid": "k1", "use": "sig", "alg": "RS256"}]})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if pfad == "/connect/token":
            return self._token(form)
        if pfad == "/connect/revoke":
            self.widerrufen.append(form["token"])
            self.refresh.pop(form["token"], None)
            return httpx.Response(200)
        return httpx.Response(404)


class Uhr:
    def __init__(self):
        self.jetzt = time.time()

    def __call__(self) -> float:
        return self.jetzt


@pytest.fixture
def uhr(monkeypatch):
    u = Uhr()
    monkeypatch.setattr(einstellungen, "_jetzt", u)
    return u


@pytest.fixture
def fake():
    return FakeIdentity()


def _cfg() -> Config:
    return Config(identity_authority=ISSUER, identity_client_secret=SECRET, app_adresse=APP)


@pytest.fixture
def app(db, umrechner, uhr, fake):
    idp = identity.Identity(_cfg(), b"k" * 32, client=httpx.Client(transport=httpx.MockTransport(fake)))
    return create_app(_cfg(), db, umrechner, heute=date(2026, 10, 10), identity_server=idp)


def _client(app) -> TestClient:
    return TestClient(app, base_url=APP, follow_redirects=False)


def _anmelden(c: TestClient, fake: FakeIdentity, sub: str = "konto-1") -> httpx.Response:
    start = c.get("/api/identity/anmelden")
    assert start.status_code == 303
    code, state = fake.code(start.headers["location"], sub)
    return c.get("/api/identity/rueckruf", params={"code": code, "state": state})


def test_anmelden_bindet_konto(app, fake, db):
    c = _client(app)
    assert c.get("/api/anmeldung").json() == {"aktiv": True, "art": einstellungen.ART_IDENTITY,
                                                  "angemeldet": False}
    assert c.get("/api/status").status_code == 401

    start = c.get("/api/identity/anmelden")
    assert start.headers["location"].startswith(ISSUER + "/connect/authorize?")
    vorgang = start.headers["set-cookie"].lower()
    assert "samesite=lax" in vorgang and "httponly" in vorgang and "path=/api/identity" in vorgang
    code, state = fake.code(start.headers["location"])
    r = c.get("/api/identity/rueckruf", params={"code": code, "state": state})
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.text
    sitzung = [h.lower() for h in r.headers.get_list("set-cookie") if h.startswith(einstellungen.COOKIE)][0]
    assert "samesite=strict" in sitzung and "secure" in sitzung and "httponly" in sitzung

    assert c.get("/api/status").status_code == 200
    assert db.einstellungen()[einstellungen.IDENTITY_SUB] == "konto-1"
    a = c.get("/api/einstellungen").json()["anmeldung"]
    assert a["art"] == einstellungen.ART_IDENTITY and a["benutzer"] == "Erika Muster"
    assert a["konto_url"] == ISSUER + "/Account/Manage"


def test_http_adresse_wechselt_zur_https_adresse(app):
    r = TestClient(app, follow_redirects=False).get("/api/identity/anmelden")
    assert r.status_code == 303 and r.headers["location"] == APP + "/api/identity/anmelden"


def test_konto_ohne_rolle_kommt_nicht_rein(app, fake, db):
    c = _client(app)
    r = _anmelden(c, fake, "konto-3")
    assert r.status_code == 403 and "finanzen" in r.text
    assert einstellungen.COOKIE not in c.cookies
    assert c.get("/api/status").status_code == 401
    assert einstellungen.IDENTITY_SUB not in db.einstellungen()


def test_nur_das_gebundene_konto_kommt_rein(app, fake, db):
    assert _anmelden(_client(app), fake, "konto-1").status_code == 200
    c = _client(app)
    r = _anmelden(c, fake, "konto-2")
    assert r.status_code == 403 and "anderes Konto" in r.text
    assert c.get("/api/status").status_code == 401

    # Bindung lösen (python -m ustva identity --zuruecksetzen): alle Sitzungen enden, nächstes Konto bindet
    einstellungen.Anmeldung(_cfg(), db).identity_loesen()
    assert _anmelden(c, fake, "konto-2").status_code == 200
    assert db.einstellungen()[einstellungen.IDENTITY_SUB] == "konto-2"


def test_rueckruf_ohne_passenden_vorgang(app, fake):
    c = _client(app)
    start = c.get("/api/identity/anmelden")
    code, _ = fake.code(start.headers["location"])
    assert c.get("/api/identity/rueckruf", params={"code": code, "state": "fremd"}).status_code == 400
    # ohne Vorgangs-Cookie, z. B. Rückruf in einem anderen Browser
    fremd = _client(app)
    code, state = fake.code(c.get("/api/identity/anmelden").headers["location"])
    assert fremd.get("/api/identity/rueckruf", params={"code": code, "state": state}).status_code == 400
    # Fehler vom Server wird angezeigt
    r = c.get("/api/identity/rueckruf", params={"error": "access_denied"})
    assert r.status_code == 400 and "access_denied" in r.text
    assert c.get("/api/status").status_code == 401


def test_id_token_wird_geprueft(fake):
    idp = identity.Identity(_cfg(), b"k" * 32, client=httpx.Client(transport=httpx.MockTransport(fake)))
    assert idp.id_token_pruefen(fake.id_token("konto-1", "n1"), "n1")["sub"] == "konto-1"
    with pytest.raises(identity.Ungueltig, match="nonce"):
        idp.id_token_pruefen(fake.id_token("konto-1", "n1"), "n2")
    fremd = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(identity.Ungueltig):
        idp.id_token_pruefen(fake.id_token("konto-1", schluessel=fremd))
    abgelaufen = jwt.encode({"iss": ISSUER, "aud": "finanz-o-mat", "sub": "x", "iat": 1, "exp": 2},
                            fake.schluessel, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(identity.Ungueltig):
        idp.id_token_pruefen(abgelaufen)
    jetzt = int(time.time())
    fremder_aussteller = jwt.encode({"iss": "https://anderer.example.test/", "aud": "finanz-o-mat", "sub": "x",
                                     "iat": jetzt, "exp": jetzt + 60},
                                    fake.schluessel, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(identity.Ungueltig, match="Aussteller"):
        idp.id_token_pruefen(fremder_aussteller)


def test_abgleich_beendet_gesperrte_sitzung(app, fake, uhr):
    c = _client(app)
    _anmelden(c, fake)
    uhr.jetzt += 10 * 60
    assert c.get("/api/status").status_code == 200
    assert fake.refresh_anzahl == 0
    uhr.jetzt += 6 * 60
    assert c.get("/api/status").status_code == 200
    assert fake.refresh_anzahl == 1  # neues Refresh Token gespeichert
    assert c.get("/api/status").status_code == 200 and fake.refresh_anzahl == 1

    # Rolle im Identity-Server entzogen
    fake.konten["konto-1"]["role"] = "user"
    uhr.jetzt += 16 * 60
    assert c.get("/api/status").status_code == 401
    fake.konten["konto-1"]["role"] = ["finanzen"]

    # Passwort geändert oder gesperrt: Server lehnt das Refresh Token ab
    _anmelden(c, fake)
    fake.refresh.clear()
    uhr.jetzt += 16 * 60
    assert c.get("/api/status").status_code == 401


def test_server_weg_sitzung_bleibt(app, fake, uhr):
    c = _client(app)
    _anmelden(c, fake)
    fake.offline = True
    uhr.jetzt += 16 * 60
    assert c.get("/api/status").status_code == 200
    # nicht bei jeder Anfrage erneut versuchen, erst nach einer Minute
    uhr.jetzt += 30
    assert c.get("/api/status").status_code == 200
    fake.offline = False
    uhr.jetzt += 31
    assert c.get("/api/status").status_code == 200
    assert fake.refresh_anzahl == 1


def test_abmelden_auch_beim_identity_server(app, fake):
    c = _client(app)
    _anmelden(c, fake)
    r = c.post("/api/abmelden")
    assert r.status_code == 200
    q = parse_qs(urlsplit(r.json()["weiter"]).query)
    assert r.json()["weiter"].startswith(ISSUER + "/connect/logout?")
    assert q["post_logout_redirect_uri"] == [APP + "/"] and q["id_token_hint"]
    assert len(fake.widerrufen) == 1
    assert c.get("/api/status").status_code == 401


def test_passwort_gilt_nicht(app, fake):
    c = _client(app)
    zugang = {"benutzer": "admin", "passwort": secrets.token_urlsafe(12)}
    assert c.post("/api/anmelden", json=zugang).status_code == 404
    _anmelden(c, fake)
    r = c.post("/api/passwort", json={"aktuell": "", "benutzer": "admin", "neu": secrets.token_urlsafe(12)})
    assert r.status_code == 409


def test_ohne_identity_bleibt_die_passwort_anmeldung(db, umrechner):
    c = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)), follow_redirects=False)
    assert c.get("/api/identity/anmelden").status_code == 404
    assert c.get("/api/anmeldung").json()["art"] == einstellungen.ART_PASSWORT


def test_konfiguration_wird_geprueft(monkeypatch):
    monkeypatch.setenv("IDENTITY_AUTHORITY", ISSUER)
    with pytest.raises(ValueError, match="IDENTITY_CLIENT_SECRET und APP_ADRESSE"):
        Config.aus_umgebung()
    monkeypatch.setenv("IDENTITY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("APP_ADRESSE", "http://192.0.2.10:8000")
    with pytest.raises(ValueError, match="APP_ADRESSE muss mit https://"):
        Config.aus_umgebung()
    monkeypatch.setenv("APP_ADRESSE", "http://localhost:5173/")
    cfg = Config.aus_umgebung()
    assert cfg.identity_aktiv and cfg.app_adresse == "http://localhost:5173"
    assert cfg.identity_client_id == "finanz-o-mat" and cfg.identity_rolle == "finanzen"
