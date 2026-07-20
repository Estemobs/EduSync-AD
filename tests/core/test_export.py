"""Tests du module d'export (CSV + étiquettes PDF)."""

from edusync_ad.core.export import (
    EXPORT_FIELDS,
    LABEL_COLOR_THEMES,
    LABEL_FORMATS,
    build_export_row,
    export_users_csv,
    generate_labels_pdf,
)


def test_label_formats_have_correct_grid_size():
    l7160 = LABEL_FORMATS["avery_l7160"]
    assert l7160.colonnes * l7160.lignes == 21
    assert l7160.par_planche == 21

    l7163 = LABEL_FORMATS["avery_l7163"]
    assert l7163.colonnes * l7163.lignes == 14
    assert l7163.par_planche == 14


def test_label_formats_fit_within_a4_width():
    # A4 = 210 mm de large : marge gauche + (colonnes * pas horizontal) doit
    # tenir dans la largeur de la page, sinon la dernière colonne déborderait.
    for fmt in LABEL_FORMATS.values():
        largeur_utilisee = fmt.marge_gauche_mm + fmt.colonnes * fmt.pas_horizontal_mm
        assert largeur_utilisee <= 210, f"{fmt.key} déborde en largeur : {largeur_utilisee}mm"


def test_build_export_row_derives_classe_from_dn():
    attrs = {
        "dn": "CN=Thomas Martin,OU=3emeA,OU=eleves,DC=lycee,DC=local",
        "sam": "thomas.martin",
        "cn": "Thomas Martin",
        "givenName": "Thomas",
        "sn": "Martin",
        "mail": "thomas.martin@lycee.fr",
        "disabled": False,
    }
    row = build_export_row(attrs)
    assert row["classe"] == "3emeA"
    assert row["identifiant"] == "thomas.martin"
    assert row["etat"] == "Actif"


def test_build_export_row_disabled_state():
    row = build_export_row({"dn": "CN=X,OU=Y,DC=a,DC=b", "disabled": True})
    assert row["etat"] == "Désactivé"


def test_export_users_csv_writes_selected_fields_only(tmp_path):
    users = [
        {"identifiant": "thomas.martin", "nom_complet": "Thomas Martin", "classe": "3emeA"},
        {"identifiant": "lea.dupont", "nom_complet": "Léa Dupont", "classe": "4emeB"},
    ]
    path = tmp_path / "export.csv"
    export_users_csv(path, users, ["identifiant", "classe"])

    content = path.read_text(encoding="utf-8-sig")
    lines = content.strip().splitlines()
    assert lines[0] == f"{EXPORT_FIELDS['identifiant']};{EXPORT_FIELDS['classe']}"
    assert lines[1] == "thomas.martin;3emeA"
    assert lines[2] == "lea.dupont;4emeB"
    # "nom_complet" n'a pas été demandé : ne doit apparaître nulle part.
    assert "Thomas Martin" not in content


def test_generate_labels_pdf_produces_valid_pdf_file(tmp_path):
    users = [build_export_row({
        "dn": f"CN=Eleve {i},OU=3emeA,DC=a,DC=b", "sam": f"eleve{i}", "cn": f"Eleve {i}",
    }) for i in range(25)]  # plus qu'une planche (21) pour forcer une 2e page
    path = tmp_path / "etiquettes.pdf"
    generate_labels_pdf(path, users, ["nom_complet", "identifiant"], "avery_l7160")

    assert path.exists()
    data = path.read_bytes()
    assert data.startswith(b"%PDF-")
    # 25 étiquettes à 21/planche : doit produire 2 pages.
    assert data.count(b"/Type /Page") + data.count(b"/Type/Page") >= 1


def test_generate_labels_pdf_skips_users_with_no_selected_field_populated(tmp_path):
    users = [{"nom_complet": "", "identifiant": ""}]
    path = tmp_path / "vide.pdf"
    generate_labels_pdf(path, users, ["nom_complet", "identifiant"], "avery_l7160")
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF-")


def test_all_color_themes_are_valid_hex():
    for theme in LABEL_COLOR_THEMES.values():
        assert theme.fond_hex.startswith("#") and len(theme.fond_hex) == 7
        assert theme.texte_hex.startswith("#") and len(theme.texte_hex) == 7


def test_generate_labels_pdf_with_color_theme_produces_valid_pdf(tmp_path):
    users = [build_export_row({
        "dn": "CN=Eleve 1,OU=3emeA,DC=a,DC=b", "sam": "eleve1", "cn": "Eleve 1",
    })]
    path = tmp_path / "couleur.pdf"
    generate_labels_pdf(path, users, ["nom_complet"], "avery_l7160", color_theme="vert")
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF-")


def test_generate_labels_pdf_with_qr_code_produces_valid_pdf(tmp_path):
    users = [build_export_row({
        "dn": "CN=Eleve 1,OU=3emeA,DC=a,DC=b", "sam": "eleve1", "cn": "Eleve 1",
    })]
    path = tmp_path / "qr.pdf"
    generate_labels_pdf(path, users, ["nom_complet", "identifiant"], "avery_l7160", qr_code=True)
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF-")


def test_generate_labels_pdf_qr_code_skipped_when_no_identifiant(tmp_path):
    # Sans identifiant, pas de QR possible : ne doit pas planter, juste l'omettre.
    users = [{"nom_complet": "Eleve Sans Id", "identifiant": ""}]
    path = tmp_path / "sans_id.pdf"
    generate_labels_pdf(path, users, ["nom_complet"], "avery_l7160", qr_code=True)
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF-")
