"""Tests du client Microsoft Graph (M19)."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

from edusync_ad.core.graph import (
    GRAPH_SCOPE,
    GraphClient,
    GraphConfig,
    GraphError,
    load_m365_config,
    save_m365_config,
)

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

TOKEN_RESPONSE = {
    "token_type": "Bearer",
    "expires_in": 3600,
    "access_token": "jeton-fake",
}


def make_config(**overrides) -> GraphConfig:
    data = {
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "client_secret": "s3cret",
        "usage_location": "FR",
    }
    data.update(overrides)
    return GraphConfig(**data)


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status
        self.headers: dict = {}

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeOpener:
    """Opener type ``urlopen`` piloté par une file de réponses (status, corps).

    Une instance d'exception dans la file est levée telle quelle
    (``URLError``, etc.)."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if not self.responses:
            raise AssertionError(f"Réponse inattendue : {req.method} {req.full_url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status >= 400:
            raise HTTPError(req.full_url, status, "erreur", {}, io.BytesIO(body))
        return FakeResponse(body, status)


def client_with(responses: list, config: GraphConfig | None = None) -> GraphClient:
    return GraphClient(config or make_config(), opener=FakeOpener(responses))


class TestValidation:
    def test_valid_config(self):
        assert make_config().validate() == []

    def test_missing_tenant(self):
        errors = GraphConfig(tenant_id="", client_id=CLIENT, client_secret="x").validate()
        assert any("locataire" in e for e in errors)

    def test_tenant_not_a_guid(self):
        errors = GraphConfig(
            tenant_id="mon-école", client_id=CLIENT, client_secret="x"
        ).validate()
        assert any("GUID" in e for e in errors)

    def test_missing_client_id(self):
        errors = GraphConfig(tenant_id=TENANT, client_id="", client_secret="x").validate()
        assert any("application" in e for e in errors)

    def test_missing_secret(self):
        errors = GraphConfig(tenant_id=TENANT, client_id=CLIENT, client_secret="").validate()
        assert any("secret" in e for e in errors)

    def test_certificate_mode_needs_file(self):
        cfg = make_config(auth_mode="certificate", certificate_path="", client_secret="")
        errors = cfg.validate()
        assert any("certificat" in e for e in errors)

    def test_certificate_mode_missing_file(self):
        cfg = make_config(auth_mode="certificate", certificate_path="/inexistant.pem")
        errors = cfg.validate()
        assert any("introuvable" in e for e in errors)

    def test_usage_location(self):
        errors = make_config(usage_location="Français").validate()
        assert any("pays" in e for e in errors)


class TestPersistence:
    def test_round_trip_encrypts_secret(self, tmp_path, monkeypatch):
        import edusync_ad.core.graph as mod

        monkeypatch.setattr(mod, "M365_CONFIG_FILE", tmp_path / "m365.json")
        cfg = make_config(auth_mode="certificate", certificate_path="/cert.pem")
        save_m365_config(cfg)

        raw = json.loads((tmp_path / "m365.json").read_text(encoding="utf-8"))
        assert "client_secret" not in raw or "s3cret" not in str(raw.get("client_secret", ""))
        assert "s3cret" not in json.dumps(raw)  # jamais en clair sur disque

        restored = load_m365_config()
        assert restored.tenant_id == TENANT
        assert restored.client_secret == "s3cret"
        assert restored.auth_mode == "certificate"

    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.graph as mod

        monkeypatch.setattr(mod, "M365_CONFIG_FILE", tmp_path / "absent.json")
        assert load_m365_config() == GraphConfig()

    def test_corrupted_file_returns_defaults(self, tmp_path, monkeypatch):
        import edusync_ad.core.graph as mod

        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "M365_CONFIG_FILE", bad)
        assert load_m365_config() == GraphConfig()


class TestToken:
    def test_token_request_payload(self):
        client = client_with([(200, TOKEN_RESPONSE)])
        token = client.get_token()
        assert token == "jeton-fake"

        opener = client._opener  # noqa: SLF001 — vérification interne du test
        req = opener.requests[0]
        assert req.full_url == (
            f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
        )
        body = req.data.decode()
        assert "grant_type=client_credentials" in body
        assert "client_secret=s3cret" in body
        from urllib.parse import parse_qs

        assert parse_qs(body)["scope"] == [GRAPH_SCOPE]

    def test_token_is_cached(self):
        client = client_with([(200, TOKEN_RESPONSE)])
        client.get_token()
        client.get_token()
        assert len(client._opener.requests) == 1  # noqa: SLF001

    def test_token_refreshed_after_expiry(self):
        clock = [1000.0]
        client = GraphClient(
            make_config(),
            opener=FakeOpener([(200, TOKEN_RESPONSE), (200, TOKEN_RESPONSE)]),
            now=lambda: clock[0],
        )
        client.get_token()
        clock[0] += 4000  # > expires_in (3600) - marge (60)
        client.get_token()
        assert len(client._opener.requests) == 2  # noqa: SLF001

    def test_token_error_raises(self):
        client = client_with(
            [(401, {"error": {"code": "invalid_client", "message": "Client inconnu"}})]
        )
        try:
            client.get_token()
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert exc.status == 401
            assert "Client inconnu" in str(exc)

    def test_configuration_incomplete_blocke_before_request(self):
        client = GraphClient(
            GraphConfig(tenant_id="", client_id="", client_secret=""),
            opener=FakeOpener([]),
        )
        try:
            client.get_token()
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert "Configuration incomplète" in str(exc)
        assert client._opener.requests == []  # noqa: SLF001


class TestRequests:
    def _authorized_client(self, responses: list) -> GraphClient:
        return client_with([(200, TOKEN_RESPONSE), *responses])

    def test_authorization_header_present(self):
        client = self._authorized_client([(200, {"value": []})])
        client.request("GET", "/organization")
        req = client._opener.requests[1]  # noqa: SLF001
        assert req.headers["Authorization"] == "Bearer jeton-fake"

    def test_error_body_is_decoded(self):
        client = self._authorized_client(
            [(400, {"error": {"code": "BadRequest", "message": "Champ invalide"}})]
        )
        try:
            client.request("POST", "/users", data={})
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert exc.status == 400
            assert exc.code == "BadRequest"
            assert "Champ invalide" in str(exc)

    def test_rate_limit_message(self):
        client = self._authorized_client(
            [(429, {"error": {"code": "TooManyRequests", "message": "ralentir"}})]
        )
        try:
            client.request("GET", "/users")
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert "Limite de débit" in str(exc)

    def test_permission_message_on_403(self):
        client = self._authorized_client(
            [(403, {"error": {"code": "Authorization_RequestDenied", "message": "refusé"}})]
        )
        try:
            client.request("GET", "/users")
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert "permissions" in str(exc)

    def test_network_error(self):
        client = client_with([URLError("hors ligne")])
        try:
            client.get_token()
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert "Connexion impossible" in str(exc)

    def test_pagination_follows_next_link(self):
        client = self._authorized_client(
            [
                (200, {"value": [{"id": "1"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=a"}),
                (200, {"value": [{"id": "2"}]}),
            ]
        )
        users = client.list_users()
        assert [u["id"] for u in users] == ["1", "2"]
        assert len(client._opener.requests) == 3  # noqa: SLF001

    def test_empty_body_returns_dict(self):
        client = self._authorized_client([(204, b"")])
        assert client.request("DELETE", "/users/1") == {}


class TestEndpoints:
    def _client(self, responses: list) -> GraphClient:
        return client_with([(200, TOKEN_RESPONSE), *responses])

    def test_create_user_payload(self):
        client = self._client([(201, {"id": "u-1"})])
        client.create_user(
            user_principal_name="jdurand@lycee.local",
            display_name="Jean Durand",
            mail_nickname="jdurand",
            password="Passw0rd!123",
            given_name="Jean",
            surname="Durand",
            account_enabled=True,
            usage_location="FR",
        )
        payload = json.loads(client._opener.requests[1].data)  # noqa: SLF001
        assert payload["accountEnabled"] is True
        assert payload["usageLocation"] == "FR"
        assert payload["userPrincipalName"] == "jdurand@lycee.local"
        assert payload["passwordProfile"]["password"] == "Passw0rd!123"
        assert payload["passwordProfile"]["forceChangePasswordNextSignIn"] is False

    def test_subscribed_skus_mapping(self):
        client = self._client(
            [(200, {"value": [
                {"skuPartNumber": "M365EDU_A1", "skuId": "sku-1"},
                {"skuPartNumber": "EXCHANGESTANDARD", "skuId": "sku-2"},
            ]})]
        )
        assert client.subscribed_skus() == {
            "M365EDU_A1": "sku-1",
            "EXCHANGESTANDARD": "sku-2",
        }

    def test_assign_license_payload(self):
        client = self._client([(200, {"id": "u-1"})])
        client.assign_license("u-1", "sku-1")
        payload = json.loads(client._opener.requests[1].data)  # noqa: SLF001
        assert payload == {"addLicenses": [{"skuId": "sku-1"}], "removeLicenses": []}

    def test_create_group_kinds(self):
        client = self._client([(201, {"id": "g-1"}), (201, {"id": "g-2"})])
        client.create_group(display_name="6eme A", mail_nickname="6emea", kind="m365")
        client.create_group(display_name="Profs", mail_nickname="profs", kind="security")
        first = json.loads(client._opener.requests[1].data)  # noqa: SLF001
        second = json.loads(client._opener.requests[2].data)  # noqa: SLF001
        assert first["groupTypes"] == ["Unified"]
        assert first["mailEnabled"] is True and first["securityEnabled"] is False
        assert second["groupTypes"] == []
        assert second["mailEnabled"] is False and second["securityEnabled"] is True

    def test_add_group_member_uses_odata_ref(self):
        client = self._client([(204, b"")])
        client.add_group_member("g-1", "u-1")
        req = client._opener.requests[1]  # noqa: SLF001
        assert req.full_url.endswith("/groups/g-1/members/$ref")
        payload = json.loads(req.data)
        assert payload["@odata.id"].endswith("/users/u-1")

    def test_upload_photo_is_raw_jpeg(self):
        client = self._client([(204, b"")])
        client.upload_user_photo("u-1", b"\xff\xd8jpeg")
        req = client._opener.requests[1]  # noqa: SLF001
        assert req.full_url.endswith("/users/u-1/photo/$content")
        assert req.headers["Content-type"] == "image/jpeg"
        assert req.data == b"\xff\xd8jpeg"


class TestCertificateAuth:
    def test_client_assertion_jwt(self, tmp_path):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EduSync-AD")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2024, 1, 1))
            .not_valid_after(datetime.datetime(2034, 1, 1))
            .sign(key, hashes.SHA256())
        )
        pem = (
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            + cert.public_bytes(serialization.Encoding.PEM)
        )
        pem_path = tmp_path / "app.pem"
        pem_path.write_bytes(pem)

        config = make_config(
            auth_mode="certificate", certificate_path=str(pem_path), client_secret=""
        )
        client = GraphClient(config, opener=FakeOpener([(200, TOKEN_RESPONSE)]))
        assert client.get_token() == "jeton-fake"

        body = client._opener.requests[0].data.decode()  # noqa: SLF001
        assert "client_assertion_type=" in body
        assert "client_assertion=" in body
        assertion = body.split("client_assertion=")[1]
        from urllib.parse import unquote

        parts = unquote(assertion).split(".")
        assert len(parts) == 3  # header.payload.signature

    def test_unreadable_pem_raises(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("pas un PEM", encoding="utf-8")
        config = make_config(
            auth_mode="certificate", certificate_path=str(bad), client_secret=""
        )
        client = GraphClient(config, opener=FakeOpener([(200, TOKEN_RESPONSE)]))
        try:
            client.get_token()
            raise AssertionError("GraphError attendue")
        except GraphError as exc:
            assert "PEM" in str(exc)
