"""Anmeldung über den Identity-Server (OpenID Connect, Repo Marcel-B/Identity).

Die App ist ein vertraulicher Client: Sie macht den Authorization Code Flow mit PKCE selbst,
löst den Code mit ihrem Secret ein und prüft das ID-Token. Der Browser bekommt danach nur das
Sitzungs-Cookie aus ustva/einstellungen.py, nie ein Token. Mit dem Refresh Token gleicht die
App eine Sitzung regelmäßig mit dem Server ab; ist es ungültig (Passwort geändert, Konto
gesperrt, überall abgemeldet), endet die Sitzung.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt

from .config import Config

ROLLE_CLAIM = "role"
SCOPES = "openid profile email roles offline_access"
VORGANG_GUELTIG = 600  # Sekunden zwischen Start der Anmeldung und Rückruf
DISCOVERY_GUELTIG = 3600
RUECKRUF = "/api/identity/rueckruf"

log = logging.getLogger(__name__)
_jetzt = time.time


class IdentityFehler(Exception):
    """Der Identity-Server ist nicht erreichbar oder antwortet unerwartet."""


class Ungueltig(Exception):
    """Antwort oder Token passt nicht (falscher state, Signatur, Ablauf, nonce ...)."""


class Abgelaufen(Exception):
    """Der Server hat das Refresh Token abgelehnt; die Sitzung muss enden."""


class KeinZugriff(Exception):
    """Anmeldung beim Identity-Server geklappt, aber das Konto darf nicht ins Finanztool."""


@dataclass
class Tokens:
    claims: dict
    id_token: str = ""
    refresh_token: str = ""

    @property
    def sub(self) -> str:
        return str(self.claims.get("sub", ""))

    @property
    def name(self) -> str:
        c = self.claims
        return str(c.get("name") or c.get("preferred_username") or c.get("email") or c.get("sub", ""))

    @property
    def rollen(self) -> list[str]:
        wert = self.claims.get(ROLLE_CLAIM) or []
        return [wert] if isinstance(wert, str) else [str(r) for r in wert]


@dataclass
class Vorgang:
    """Was zwischen Start und Rückruf im (signierten) Cookie liegt."""

    state: str
    nonce: str
    verifier: str
    bis: float = field(default=0.0)


def _b64(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


def _b64_lesen(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Identity:
    def __init__(self, cfg: Config, schluessel: bytes, client: httpx.Client | None = None):
        self.authority = cfg.identity_authority.rstrip("/")
        self.client_id = cfg.identity_client_id
        self.secret = cfg.identity_client_secret
        self.rolle = cfg.identity_rolle
        self.redirect_uri = cfg.app_adresse.rstrip("/") + RUECKRUF
        self.abmelde_ziel = cfg.app_adresse.rstrip("/") + "/"
        self._schluessel = schluessel  # signiert das Vorgangs-Cookie
        self._client = client or httpx.Client(timeout=10)
        self._lock = threading.Lock()
        self._discovery: tuple[dict, float] | None = None
        self._jwks: jwt.PyJWKSet | None = None

    @property
    def konto_url(self) -> str:
        return self.authority + "/Account/Manage"

    # Server-Metadaten

    def _anfrage(self, methode: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(methode, url, **kwargs)
        except httpx.HTTPError as e:
            raise IdentityFehler(f"Identity-Server nicht erreichbar: {e}") from e

    def _json(self, antwort: httpx.Response) -> dict:
        try:
            daten = antwort.json()
        except ValueError as e:
            raise IdentityFehler(f"Unerwartete Antwort vom Identity-Server ({antwort.status_code})") from e
        if not isinstance(daten, dict):
            raise IdentityFehler("Unerwartete Antwort vom Identity-Server")
        return daten

    def metadaten(self) -> dict:
        with self._lock:
            if self._discovery and _jetzt() - self._discovery[1] < DISCOVERY_GUELTIG:
                return self._discovery[0]
        antwort = self._anfrage("GET", self.authority + "/.well-known/openid-configuration")
        if antwort.status_code != 200:
            raise IdentityFehler(f"Discovery-Dokument nicht lesbar ({antwort.status_code})")
        daten = self._json(antwort)
        if daten.get("issuer", "").rstrip("/") != self.authority:
            raise IdentityFehler(f"Issuer {daten.get('issuer')!r} passt nicht zu IDENTITY_AUTHORITY")
        with self._lock:
            self._discovery = (daten, _jetzt())
        return daten

    def _schluessel_fuer(self, kid: str | None) -> jwt.PyJWK:
        for neu_laden in (False, True):  # unbekannte kid: Schlüssel gewechselt, einmal neu laden
            if neu_laden or self._jwks is None:
                antwort = self._anfrage("GET", self.metadaten()["jwks_uri"])
                if antwort.status_code != 200:
                    raise IdentityFehler(f"Schlüssel des Identity-Servers nicht lesbar ({antwort.status_code})")
                try:
                    self._jwks = jwt.PyJWKSet.from_dict(self._json(antwort))
                except jwt.PyJWTError as e:
                    raise IdentityFehler(f"Schlüssel des Identity-Servers unbrauchbar: {e}") from e
            for k in self._jwks.keys:
                if kid is None or k.key_id == kid:
                    return k
        raise Ungueltig("ID-Token mit unbekanntem Schlüssel signiert")

    # Anmeldung

    def start(self) -> tuple[str, str]:
        """Adresse der Anmeldeseite und Wert für das Vorgangs-Cookie."""
        v = Vorgang(state=secrets.token_urlsafe(24), nonce=secrets.token_urlsafe(24),
                    verifier=secrets.token_urlsafe(48), bis=_jetzt() + VORGANG_GUELTIG)
        challenge = _b64(hashlib.sha256(v.verifier.encode()).digest())
        parameter = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": v.state,
            "nonce": v.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.metadaten()['authorization_endpoint']}?{urlencode(parameter)}", self._signieren(v)

    def _signieren(self, v: Vorgang) -> str:
        daten = _b64(json.dumps(v.__dict__, separators=(",", ":")).encode())
        return f"{daten}.{_b64(hmac.new(self._schluessel, daten.encode(), hashlib.sha256).digest())}"

    def vorgang(self, cookie: str | None) -> Vorgang:
        try:
            daten, signatur = (cookie or "").split(".")
            erwartet = _b64(hmac.new(self._schluessel, daten.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signatur, erwartet):
                raise ValueError
            v = Vorgang(**json.loads(_b64_lesen(daten)))
        except (ValueError, TypeError):
            raise Ungueltig("Anmeldevorgang unbekannt. Bitte die Anmeldung neu starten.") from None
        if v.bis < _jetzt():
            raise Ungueltig("Die Anmeldung hat zu lange gedauert. Bitte neu starten.")
        return v

    def einloesen(self, code: str, state: str, v: Vorgang) -> Tokens:
        """Löst den Code nach dem Rückruf ein und prüft das ID-Token."""
        if not hmac.compare_digest(state, v.state):
            raise Ungueltig("Der Rückruf gehört nicht zu dieser Anmeldung. Bitte neu starten.")
        daten = self._token({"grant_type": "authorization_code", "code": code,
                             "redirect_uri": self.redirect_uri, "code_verifier": v.verifier})
        if not daten.get("id_token"):
            raise Ungueltig("Der Identity-Server hat kein ID-Token geliefert")
        claims = self.id_token_pruefen(daten["id_token"], v.nonce)
        return Tokens(claims, daten["id_token"], daten.get("refresh_token", ""))

    def erneuern(self, refresh_token: str) -> Tokens:
        """Holt mit dem Refresh Token neue Tokens; Abgelaufen, wenn der Server es ablehnt."""
        if not refresh_token:
            raise Abgelaufen("kein Refresh Token")
        daten = self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})
        if daten.get("id_token"):
            claims = self.id_token_pruefen(daten["id_token"])
        else:
            claims = self._userinfo(daten.get("access_token", ""))
        return Tokens(claims, daten.get("id_token", ""), daten.get("refresh_token") or refresh_token)

    def _zugang(self) -> dict:
        # client_secret_post: Basic Auth müsste Sonderzeichen im Secret erst URL-kodieren
        return {"client_id": self.client_id, "client_secret": self.secret}

    def _token(self, formular: dict) -> dict:
        antwort = self._anfrage("POST", self.metadaten()["token_endpoint"], data=formular | self._zugang())
        daten = self._json(antwort)
        if antwort.status_code == 200:
            return daten
        fehler = daten.get("error", "")
        if fehler == "invalid_grant" and formular["grant_type"] == "refresh_token":
            raise Abgelaufen(daten.get("error_description", fehler))
        if 400 <= antwort.status_code < 500:
            raise Ungueltig(f"Identity-Server lehnt ab: {fehler} {daten.get('error_description', '')}".strip())
        raise IdentityFehler(f"Identity-Server antwortet mit {antwort.status_code}")

    def _userinfo(self, access_token: str) -> dict:
        antwort = self._anfrage("GET", self.metadaten()["userinfo_endpoint"],
                                headers={"Authorization": f"Bearer {access_token}"})
        if antwort.status_code in (401, 403):
            raise Abgelaufen("UserInfo lehnt das Token ab")
        if antwort.status_code != 200:
            raise IdentityFehler(f"UserInfo antwortet mit {antwort.status_code}")
        return self._json(antwort)

    def id_token_pruefen(self, token: str, nonce: str | None = None) -> dict:
        try:
            kopf = jwt.get_unverified_header(token)
            schluessel = self._schluessel_fuer(kopf.get("kid"))
            claims = jwt.decode(token, schluessel.key, algorithms=["RS256", "RS384", "RS512", "PS256", "ES256"],
                                audience=self.client_id, leeway=60,
                                options={"require": ["sub", "iss", "aud", "exp", "iat"]})
        except jwt.PyJWTError as e:
            raise Ungueltig(f"ID-Token ungültig: {e}") from e
        # OpenIddict schreibt den Issuer mit abschließendem Schrägstrich
        if str(claims["iss"]).rstrip("/") != self.authority:
            raise Ungueltig(f"ID-Token von fremdem Aussteller {claims['iss']!r}")
        if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise Ungueltig("ID-Token gehört nicht zu dieser Anmeldung (nonce)")
        return claims

    # Abmelden

    def widerrufen(self, refresh_token: str) -> None:
        """Widerruft das Refresh Token; Fehler nur ins Log, das Abmelden geht trotzdem weiter."""
        if not refresh_token:
            return
        try:
            url = self.metadaten().get("revocation_endpoint")
            if url:
                self._anfrage("POST", url, data={"token": refresh_token, "token_type_hint": "refresh_token"}
                              | self._zugang())
        except IdentityFehler as e:
            log.warning("Refresh Token nicht widerrufen: %s", e)

    def abmelde_url(self, id_token: str) -> str:
        """Abmeldung beim Identity-Server; mit id_token_hint fragt er nicht nach."""
        try:
            url = self.metadaten().get("end_session_endpoint") or self.authority + "/connect/logout"
        except IdentityFehler:
            url = self.authority + "/connect/logout"
        parameter = {"post_logout_redirect_uri": self.abmelde_ziel, "client_id": self.client_id}
        if id_token:
            parameter["id_token_hint"] = id_token
        return f"{url}?{urlencode(parameter)}"
