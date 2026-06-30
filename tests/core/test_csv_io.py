from pathlib import Path

from edusync_ad.core.csv_io import export_created_accounts, load_preview, load_rows
from edusync_ad.core.models import GeneratedUser, RawUserRow

EXAMPLE_CSV = (
    Path(__file__).resolve().parents[2] / "resources" / "csv_examples" / "creation_comptes_exemple.csv"
)


def test_load_preview_detects_headers_and_semicolon_delimiter():
    preview = load_preview(EXAMPLE_CSV)
    assert preview.delimiter == ";"
    assert preview.headers == [
        "prenom",
        "nom",
        "ou",
        "email_perso",
        "date_naissance",
        "numero",
    ]
    assert len(preview.rows) == 3
    assert preview.rows[0]["prenom"] == "Thomas"


def test_load_preview_suggests_mapping_from_matching_headers():
    preview = load_preview(EXAMPLE_CSV)
    assert preview.suggested_mapping["prenom"] == "prenom"
    assert preview.suggested_mapping["nom"] == "nom"
    assert preview.suggested_mapping["ou"] == "ou"


def test_load_preview_suggests_empty_mapping_for_unmatched_headers(tmp_path):
    path = tmp_path / "custom.csv"
    path.write_text("Prénom;Nom de famille;Unite\nThomas;Martin;3emeA\n", encoding="utf-8")
    preview = load_preview(path)
    assert preview.suggested_mapping["prenom"] == ""
    assert preview.suggested_mapping["nom"] == ""
    assert preview.suggested_mapping["ou"] == ""


def test_load_rows_with_matching_headers():
    mapping = {
        "prenom": "prenom",
        "nom": "nom",
        "ou": "ou",
        "email_perso": "email_perso",
        "date_naissance": "date_naissance",
        "numero": "numero",
    }
    result = load_rows(EXAMPLE_CSV, mapping)
    assert len(result.rows) == 3
    assert result.skipped_row_numbers == []
    assert result.rows[0] == RawUserRow(
        prenom="Thomas",
        nom="Martin",
        ou="OU=3emeA,OU=Eleves,DC=lycee-victor-hugo,DC=local",
        email_perso="thomas.martin.perso@gmail.com",
        date_naissance="12/05/2011",
        numero="20251001",
    )


def test_load_rows_with_manual_mapping_for_mismatched_headers(tmp_path):
    path = tmp_path / "custom.csv"
    path.write_text(
        "Prenom;NomFamille;Unite\nThomas;Martin;OU=3emeA\n", encoding="utf-8"
    )
    mapping = {"prenom": "Prenom", "nom": "NomFamille", "ou": "Unite"}
    result = load_rows(path, mapping)
    assert len(result.rows) == 1
    assert result.rows[0].prenom == "Thomas"
    assert result.rows[0].ou == "OU=3emeA"


def test_load_rows_skips_incomplete_rows(tmp_path):
    path = tmp_path / "incomplete.csv"
    path.write_text(
        "prenom;nom;ou\nThomas;Martin;OU=3emeA\n;Dupont;OU=3emeA\nLea;;OU=3emeA\n",
        encoding="utf-8",
    )
    mapping = {"prenom": "prenom", "nom": "nom", "ou": "ou"}
    result = load_rows(path, mapping)
    assert len(result.rows) == 1
    assert result.skipped_row_numbers == [2, 3]


def test_load_rows_handles_latin1_encoding(tmp_path):
    path = tmp_path / "latin1.csv"
    path.write_bytes("prenom;nom;ou\nLéa;Dupont;OU=3emeA\n".encode("latin-1"))
    mapping = {"prenom": "prenom", "nom": "nom", "ou": "ou"}
    result = load_rows(path, mapping)
    assert result.rows[0].prenom == "Léa"


def test_export_created_accounts(tmp_path):
    out_path = tmp_path / "export.csv"
    users = [
        GeneratedUser(
            source=RawUserRow(prenom="Thomas", nom="Martin", ou="OU=3emeA"),
            identifiant="thomas.martin",
            mot_de_passe="Ab12$xyz",
            adresse_mail="thomas.martin@lycee-victor-hugo.fr",
            ou_cible="OU=3emeA",
        )
    ]
    export_created_accounts(out_path, users)
    content = out_path.read_text(encoding="utf-8-sig")
    assert "prenom;nom;identifiant;mot_de_passe;adresse_mail" in content
    assert "Thomas;Martin;thomas.martin;Ab12$xyz;thomas.martin@lycee-victor-hugo.fr" in content
