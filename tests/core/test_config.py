from edusync_ad.core.config import AppConfig, load_config, save_config
from edusync_ad.core.models import DoublonRule, PrenomComposeRule


def test_default_config_has_expected_defaults():
    config = AppConfig()
    assert config.identifiant_format_eleve == "prenom.nom"
    assert config.regle_doublons == DoublonRule.SUFFIXE_NUMERIQUE
    assert config.politique_mdp_personnel.caracteres_speciaux is True
    assert config.politique_mdp_eleve.caracteres_speciaux is False
    # Sécurité LDAPS : validation du certificat activée par défaut (§ core/ad/connection.py).
    assert config.ldaps_verifier_certificat is True
    assert config.ldaps_chemin_certificat_ca == ""


def test_save_then_load_round_trip_preserves_ldaps_cert_settings(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(
        ldaps_verifier_certificat=False,
        ldaps_chemin_certificat_ca="/etc/edusync/ad-ca.pem",
    )
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.ldaps_verifier_certificat is False
    assert loaded.ldaps_chemin_certificat_ca == "/etc/edusync/ad-ca.pem"


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "missing.json")
    assert config == AppConfig()


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(
        identifiant_format_eleve="p.nom",
        regle_doublons=DoublonRule.ANNEE_SUFFIXE,
        regle_prenom_compose=PrenomComposeRule.CONCATENATION,
        domaine_mail="lycee-victor-hugo.fr",
    )
    config.politique_mdp_eleve.longueur = 9
    save_config(config, path)

    loaded = load_config(path)
    assert loaded == config


def test_load_config_corrupted_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not valid json", encoding="utf-8")
    assert load_config(path) == AppConfig()
