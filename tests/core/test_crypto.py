import json

from edusync_ad.core.crypto import (
    KEY_SIZE_BYTES,
    RememberedConnection,
    clear_remembered_connection,
    decrypt_str,
    encrypt_str,
    get_or_create_key,
    load_remembered_connection,
    save_remembered_connection,
)


def test_get_or_create_key_is_stable_and_correct_size(tmp_path):
    key_path = tmp_path / "secret.key"
    key1 = get_or_create_key(key_path)
    key2 = get_or_create_key(key_path)
    assert key1 == key2
    assert len(key1) == KEY_SIZE_BYTES


def test_encrypt_decrypt_round_trip(tmp_path):
    key = get_or_create_key(tmp_path / "secret.key")
    token = encrypt_str(key, "lycee-victor-hugo.local")
    assert token != "lycee-victor-hugo.local"
    assert decrypt_str(key, token) == "lycee-victor-hugo.local"


def test_remembered_connection_round_trip(tmp_path):
    key_path = tmp_path / "secret.key"
    conn_path = tmp_path / "connection.local.json"
    info = RememberedConnection(
        domaine="lycee-victor-hugo.local", controleur="10.0.0.5", utilisateur="admin"
    )
    save_remembered_connection(info, key_path=key_path, path=conn_path)

    loaded = load_remembered_connection(key_path=key_path, path=conn_path)
    assert loaded == info


def test_remembered_connection_password_never_persisted(tmp_path):
    key_path = tmp_path / "secret.key"
    conn_path = tmp_path / "connection.local.json"
    info = RememberedConnection(
        domaine="lycee-victor-hugo.local", controleur="10.0.0.5", utilisateur="admin"
    )
    save_remembered_connection(info, key_path=key_path, path=conn_path)

    raw_on_disk = conn_path.read_text(encoding="utf-8")
    assert "password" not in json.loads(raw_on_disk)
    assert "motdepasse" not in raw_on_disk.lower()


def test_load_remembered_connection_missing_file_returns_none(tmp_path):
    assert load_remembered_connection(path=tmp_path / "missing.json") is None


def test_clear_remembered_connection(tmp_path):
    key_path = tmp_path / "secret.key"
    conn_path = tmp_path / "connection.local.json"
    info = RememberedConnection(domaine="d", controleur="c", utilisateur="u")
    save_remembered_connection(info, key_path=key_path, path=conn_path)
    assert conn_path.exists()
    clear_remembered_connection(conn_path)
    assert not conn_path.exists()
