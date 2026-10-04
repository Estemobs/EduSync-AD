"""Tests du module Étiquettes & trombinoscopes avancés (M24)."""

from __future__ import annotations

import io
import json
import smtplib
from pathlib import Path

import pytest
from PIL import Image

from edusync_ad.core.export import LABEL_FORMATS, build_export_row
from edusync_ad.core.label_studio import (
    ALIGNMENTS,
    ELEMENT_KINDS,
    FONTS,
    SHEETS,
    LabelElement,
    LabelTemplate,
    MailConfig,
    MailError,
    SheetSpec,
    builtin_templates,
    duplicate_template,
    export_template,
    generate_trombinoscope_pdf,
    import_template,
    load_mail_config,
    load_templates,
    render_labels_pdf,
    save_mail_config,
    save_templates,
    send_label_email,
    unique_template_id,
)


# -- Utilitaires ---------------------------------------------------------------------------

def make_rows(count: int = 5) -> list[dict]:
    return [
        build_export_row({
            "dn": f"CN=Eleve {i},OU=3emeA,DC=lycee,DC=local",
            "sam": f"eleve{i}",
            "cn": f"Eleve {i}",
            "givenName": f"Prenom{i}",
            "sn": f"Nom{i}",
            "mail": f"eleve{i}@lycee.fr",
        })
        for i in range(count)
    ]


def jpeg_bytes(color: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeSMTP:
    """SMTP de test : enregistre la connexion, l'authentification et le message."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        FakeSMTP.last = self
        self.tls = False
        self.login_args: tuple | None = None
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self) -> None:
        self.tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        self.message = message


def smtp_factory() -> callable:
    return lambda host, port, timeout: FakeSMTP(host, port, timeout)


# -- Planches --------------------------------------------------------------------------------

class TestSheets:
    def test_all_builtin_sheets_are_valid(self):
        for key, sheet in SHEETS.items():
            assert sheet.validate() == [], (key, sheet.validate())

    def test_avery_geometry_comes_from_export_module(self):
        for key in ("avery_l7160", "avery_l7163"):
            fmt = LABEL_FORMATS[key]
            sheet = SHEETS[key]
            assert (sheet.largeur_mm, sheet.hauteur_mm) == (fmt.largeur_mm, fmt.hauteur_mm)
            assert (sheet.colonnes, sheet.lignes) == (fmt.colonnes, fmt.lignes)
            assert sheet.marge_gauche_mm == fmt.marge_gauche_mm
            assert sheet.pas_horizontal_mm == fmt.pas_horizontal_mm
            assert sheet.par_planche == fmt.par_planche

    def test_badge_and_card_sheets_offered(self):
        assert {"badge_85x55", "carte_85x54"} <= set(SHEETS)
        assert SHEETS["badge_85x55"].largeur_mm == 85.0
        assert SHEETS["carte_85x54"].hauteur_mm == 54.0

    def test_label_origin_places_cells_on_the_grid(self):
        sheet = SHEETS["avery_l7160"]
        assert sheet.label_origin_mm(0, 0) == (sheet.marge_gauche_mm, sheet.marge_haut_mm)
        assert sheet.label_origin_mm(1, 0)[1] == pytest.approx(sheet.marge_haut_mm + sheet.pas_vertical_mm)

    def test_overlapping_pitch_is_rejected(self):
        sheet = SheetSpec("bad", "Bad", "A4", 63.5, 38.1, 3, 7, 7.5, 15.5, 50.0, 38.1)
        assert any("chevauchement" in error for error in sheet.validate())

    def test_overflow_is_rejected(self):
        sheet = SheetSpec("bad", "Bad", "A4", 63.5, 38.1, 5, 7, 7.5, 15.5, 66.0, 38.1)
        assert any("largeur" in error for error in sheet.validate())

    def test_unknown_page_is_rejected(self):
        sheet = SheetSpec("bad", "Bad", "A5", 63.5, 38.1, 3, 7, 7.5, 15.5, 66.0, 38.1)
        assert any("A4 ou A3" in error for error in sheet.validate())


# -- Éléments -------------------------------------------------------------------------------

class TestLabelElement:
    def test_default_element_is_valid(self):
        assert LabelElement().validate() == []

    def test_catalogs_are_coherent(self):
        assert set(ELEMENT_KINDS) == {"texte", "statique", "photo", "qr"}
        assert set(ALIGNMENTS) == {"gauche", "centre", "droite"}

    def test_only_standard_reportlab_fonts_are_offered(self):
        # Polices base reportlab : disponibles sur toutes les plateformes sans
        # fichier de police externe (contrainte « 100% Desktop » du projet).
        assert FONTS == [
            "Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
            "Times-Roman", "Times-Bold", "Courier",
        ]
        used = {e.font for t in builtin_templates() for e in t.elements}
        assert used <= set(FONTS)

    def test_unknown_kind(self):
        errors = LabelElement(kind="logo").validate()
        assert any("inconnu" in error for error in errors)

    def test_unknown_field(self):
        errors = LabelElement(kind="texte", field="numero_tele").validate()
        assert any("champ inconnu" in error for error in errors)

    def test_empty_static_text(self):
        errors = LabelElement(kind="statique", text="   ").validate()
        assert any("Texte fixe vide" in error for error in errors)

    def test_bad_color(self):
        errors = LabelElement(color_hex="rouge").validate()
        assert any("#RRGGBB" in error for error in errors)

    def test_bad_alignment(self):
        errors = LabelElement(align="milieu").validate()
        assert any("Alignement" in error for error in errors)

    def test_unknown_font_for_text(self):
        errors = LabelElement(kind="texte", font="Comic").validate()
        assert any("Police" in error for error in errors)

    def test_overflow_outside_label(self):
        errors = LabelElement(x_mm=60, w_mm=40).validate((63.5, 38.1))
        assert any("droite" in error for error in errors)
        errors = LabelElement(y_mm=35, h_mm=10).validate((63.5, 38.1))
        assert any("bas" in error for error in errors)

    def test_negative_position(self):
        assert any("négative" in error for error in LabelElement(x_mm=-1).validate())

    def test_value_reads_the_user_row(self):
        row = make_rows(1)[0]
        assert LabelElement(kind="texte", field="nom_complet").value(row) == row["nom_complet"]
        assert LabelElement(kind="statique", text="ECOLE").value(row) == "ECOLE"
        assert LabelElement(kind="texte", field="mail").value({}) == ""

    def test_round_trip(self):
        element = LabelElement(kind="qr", field="identifiant", x_mm=12.5, size_pt=9.5, align="droite")
        assert LabelElement.from_dict(element.to_dict()) == element

    def test_hit_test(self):
        template = builtin_templates()[0]
        element = template.elements[0]
        inside = template.element_index_at(element.x_mm + 1, element.y_mm + 1)
        assert inside == 0
        outside = template.element_index_at(-5, -5)
        assert outside is None


# -- Modèles -------------------------------------------------------------------------------------

class TestLabelTemplate:
    def test_four_builtin_templates_all_valid(self):
        templates = builtin_templates()
        assert len(templates) == 4
        for template in templates:
            assert template.builtin is True
            assert template.validate() == [], (template.id, template.validate())

    def test_builtin_templates_cover_the_roadmap_features(self):
        templates = builtin_templates()
        kinds = {element.kind for template in templates for element in template.elements}
        assert {"texte", "statique", "photo", "qr"} <= kinds
        sheets = {template.sheet for template in templates}
        assert sheets == {"avery_l7160", "avery_l7163", "badge_85x55", "carte_85x54"}

    def test_unknown_sheet(self):
        template = LabelTemplate(id="x", name="X", sheet="inconnue",
                                 elements=[LabelElement()])
        errors = template.validate()
        assert any("Planche" in error for error in errors)

    def test_bad_id(self):
        template = LabelTemplate(id="Mauvais Id!", name="X", elements=[LabelElement()])
        assert any("Identifiant" in error for error in template.validate())

    def test_empty_name(self):
        template = LabelTemplate(id="x", name="  ", elements=[LabelElement()])
        assert any("Nom du modèle vide" in error for error in template.validate())

    def test_no_elements(self):
        template = LabelTemplate(id="x", name="X", elements=[])
        assert any("Aucun élément" in error for error in template.validate())

    def test_bad_background_color(self):
        template = LabelTemplate(id="x", name="X", background_hex="bleu",
                                 elements=[LabelElement()])
        assert any("fond" in error for error in template.validate())

    def test_round_trip(self):
        template = builtin_templates()[1]
        assert LabelTemplate.from_dict(template.to_dict()) == template

    def test_clone_is_deep_and_editable(self):
        template = builtin_templates()[0]
        clone = template.clone()
        clone.id = "copie"
        clone.elements[0].x_mm = 42
        assert template.elements[0].x_mm != 42
        assert clone.builtin is False


# -- Persistance ---------------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "label_templates.json"
        templates = builtin_templates()
        save_templates(templates, path)
        loaded = load_templates(path)
        assert [t.id for t in loaded] == [t.id for t in templates]
        assert loaded[0].to_dict() == templates[0].to_dict()

    def test_missing_file_returns_builtins(self, tmp_path):
        loaded = load_templates(tmp_path / "absent.json")
        assert [t.id for t in loaded] == [t.id for t in builtin_templates()]

    def test_corrupted_file_returns_builtins(self, tmp_path):
        path = tmp_path / "label_templates.json"
        path.write_text("{ pas du json", encoding="utf-8")
        assert [t.id for t in load_templates(path)] == [t.id for t in builtin_templates()]

    def test_unique_template_id(self):
        templates = builtin_templates()
        assert unique_template_id(templates, "nouveau") == "nouveau"
        assert unique_template_id(templates, "carte_visite") == "carte_visite-2"
        # Casse et accents pliés avant la recherche de conflit.
        assert unique_template_id(templates, "Carte_Visite") == "carte_visite-2"
        assert unique_template_id(templates, "Étiquette classe") == "etiquette-classe"
        assert unique_template_id(templates, "Étiquette_Classe") == "etiquette_classe-2"

    def test_duplicate_template_keeps_the_original(self):
        templates = builtin_templates()
        clone = duplicate_template(templates, templates[0])
        assert clone.id != templates[0].id
        assert clone.builtin is False
        assert len(templates) == 4

    def test_export_then_import(self, tmp_path):
        source = builtin_templates()[2]
        dest = tmp_path / "modele.json"
        export_template(source, dest)
        imported = import_template(dest)
        assert imported.to_dict() == source.to_dict()

    def test_import_rejects_invalid_model(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"id": "x", "name": "X", "elements": []}), encoding="utf-8")
        with pytest.raises(ValueError):
            import_template(bad)

    def test_import_rejects_garbage(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("pas un objet", encoding="utf-8")
        with pytest.raises(ValueError):
            import_template(bad)


# -- Rendu PDF -------------------------------------------------------------------------------------

class TestRenderLabels:
    def test_produces_a_valid_multi_page_pdf(self, tmp_path):
        template = builtin_templates()[0]  # 21 étiquettes/planche
        path = tmp_path / "etiquettes.pdf"
        pages = render_labels_pdf(path, make_rows(25), template)
        assert pages == 2
        assert path.read_bytes().startswith(b"%PDF-")

    def test_badge_and_card_templates_render(self, tmp_path):
        for template in builtin_templates():
            path = tmp_path / f"{template.id}.pdf"
            pages = render_labels_pdf(path, make_rows(3), template)
            assert pages >= 1
            assert path.read_bytes().startswith(b"%PDF-")

    def test_invalid_template_raises(self, tmp_path):
        template = LabelTemplate(id="x", name="X", sheet="inconnue",
                                 elements=[LabelElement()])
        with pytest.raises(ValueError):
            render_labels_pdf(tmp_path / "out.pdf", make_rows(1), template)

    def test_photo_is_embedded_without_error(self, tmp_path):
        template = next(t for t in builtin_templates() if t.sheet == "badge_85x55")
        rows = make_rows(2)
        photos = {rows[0]["identifiant"]: jpeg_bytes()}
        path = tmp_path / "avec_photo.pdf"
        assert render_labels_pdf(path, rows, template, photos=photos) == 1
        assert len(path.read_bytes()) > 1000

    def test_missing_photo_falls_back_to_placeholder(self, tmp_path):
        template = next(t for t in builtin_templates() if t.sheet == "badge_85x55")
        path = tmp_path / "sans_photo.pdf"
        assert render_labels_pdf(path, make_rows(1), template, photos={}) == 1
        assert path.read_bytes().startswith(b"%PDF-")

    def test_qr_is_skipped_without_identifier(self, tmp_path):
        template = builtin_templates()[0]
        path = tmp_path / "sans_id.pdf"
        assert render_labels_pdf(path, [{"nom_complet": "Sans Id", "identifiant": ""}], template) == 1
        assert path.read_bytes().startswith(b"%PDF-")

    def test_empty_user_list_still_produces_a_page(self, tmp_path):
        path = tmp_path / "vide.pdf"
        render_labels_pdf(path, [], builtin_templates()[0])
        assert path.read_bytes().startswith(b"%PDF-")


# -- Trombinoscope ---------------------------------------------------------------------------------

class TestTrombinoscope:
    def test_generates_a_pdf_on_a4_and_a3(self, tmp_path):
        for page in ("A4", "A3"):
            path = tmp_path / f"trombi_{page}.pdf"
            pages = generate_trombinoscope_pdf(
                path, make_rows(9), page=page, columns=4,
                title="Trombinoscope 3emeA", subtitle="Année 2026-2027",
            )
            assert pages >= 1
            assert path.read_bytes().startswith(b"%PDF-")

    def test_columns_are_clamped_to_a_valid_range(self, tmp_path):
        path = tmp_path / "trombi.pdf"
        pages = generate_trombinoscope_pdf(path, make_rows(3), columns=99)
        assert pages >= 1

    def test_unknown_page_raises(self, tmp_path):
        with pytest.raises(ValueError):
            generate_trombinoscope_pdf(tmp_path / "x.pdf", make_rows(1), page="A5")

    def test_empty_entries_raise(self, tmp_path):
        with pytest.raises(ValueError):
            generate_trombinoscope_pdf(tmp_path / "x.pdf", [])

    def test_multiple_pages_for_a_large_class(self, tmp_path):
        path = tmp_path / "trombi.pdf"
        pages = generate_trombinoscope_pdf(path, make_rows(40), columns=4)
        assert pages >= 2

    def test_photo_and_placeholders_render(self, tmp_path):
        rows = make_rows(3)
        photos = {rows[0]["identifiant"]: jpeg_bytes()}
        path = tmp_path / "trombi.pdf"
        assert generate_trombinoscope_pdf(path, rows, photos=photos) == 1
        assert path.read_bytes().startswith(b"%PDF-")

    def test_page_sizes_are_the_official_ones(self):
        from edusync_ad.core.label_studio import PAGE_SIZES_MM
        assert PAGE_SIZES_MM["A4"] == (210.0, 297.0)
        assert PAGE_SIZES_MM["A3"] == (297.0, 420.0)


# -- Configuration SMTP + envoi -----------------------------------------------------------------------

def valid_config() -> MailConfig:
    return MailConfig(
        host="smtp.lycee.fr", port=587, username="edusync",
        password="Secret-SMTP-123", from_addr="edusync@lycee.fr",
    )


class TestMailConfig:
    def test_valid_config_has_no_error(self):
        assert valid_config().validate() == []

    def test_missing_host(self):
        errors = MailConfig(from_addr="a@b.fr").validate()
        assert any("Serveur SMTP" in error for error in errors)

    def test_missing_or_bad_sender(self):
        assert any("expéditrice requise" in e for e in MailConfig(host="h").validate())
        assert any("invalide" in e for e in MailConfig(host="h", from_addr="pas-un-mail").validate())

    def test_bad_port(self):
        errors = MailConfig(host="h", from_addr="a@b.fr", port=0).validate()
        assert any("Port" in error for error in errors)

    def test_ssl_and_tls_are_exclusive(self):
        config = MailConfig(host="h", from_addr="a@b.fr", use_ssl=True, use_tls=True)
        assert any("exclusifs" in error for error in config.validate())

    def test_password_is_encrypted_on_disk(self, tmp_path):
        config_path = tmp_path / "label_mail.json"
        key_path = tmp_path / "secret.key"
        save_mail_config(valid_config(), config_path, key_path=key_path)

        raw = config_path.read_text(encoding="utf-8")
        assert "Secret-SMTP-123" not in raw
        assert "password_token" in raw

        loaded = load_mail_config(config_path, key_path=key_path)
        assert loaded.password == "Secret-SMTP-123"
        assert loaded.host == "smtp.lycee.fr"
        assert loaded.port == 587

    def test_lost_key_leaves_the_password_empty(self, tmp_path):
        config_path = tmp_path / "label_mail.json"
        save_mail_config(valid_config(), config_path, key_path=tmp_path / "key1")
        loaded = load_mail_config(config_path, key_path=tmp_path / "other-key")
        assert loaded.password == ""
        assert loaded.host == "smtp.lycee.fr"

    def test_save_rejects_invalid_config(self, tmp_path):
        with pytest.raises(MailError):
            save_mail_config(MailConfig(), tmp_path / "mail.json", key_path=tmp_path / "k")

    def test_missing_file_returns_empty_config(self, tmp_path):
        config = load_mail_config(tmp_path / "absent.json")
        assert config.host == "" and config.password == ""


class TestSendLabel:
    @pytest.fixture
    def pdf(self, tmp_path) -> Path:
        path = tmp_path / "etiquette.pdf"
        render_labels_pdf(path, make_rows(1), builtin_templates()[0])
        return path

    def test_sends_the_pdf_as_an_attachment(self, pdf, tmp_path):
        created: dict = {}

        def factory(host, port, timeout):
            smtp = FakeSMTP(host, port, timeout)
            created["smtp"] = smtp
            return smtp

        send_label_email(
            valid_config(), "eleve@lycee.fr", "Votre étiquette", "Bonjour", pdf,
            smtp_factory=factory,
        )
        smtp = created["smtp"]
        assert (smtp.host, smtp.port) == ("smtp.lycee.fr", 587)
        assert smtp.tls is True
        assert smtp.login_args == ("edusync", "Secret-SMTP-123")
        assert smtp.message["To"] == "eleve@lycee.fr"
        assert smtp.message["Subject"] == "Votre étiquette"
        attachments = list(smtp.message.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_content_type() == "application/pdf"

    def test_invalid_recipient_is_rejected(self, pdf):
        with pytest.raises(MailError):
            send_label_email(valid_config(), "pas-de-mail", "s", "b", pdf,
                             smtp_factory=smtp_factory())

    def test_invalid_config_is_rejected(self, pdf):
        with pytest.raises(MailError):
            send_label_email(MailConfig(), "a@b.fr", "s", "b", pdf,
                             smtp_factory=smtp_factory())

    def test_non_pdf_attachment_is_rejected(self, tmp_path):
        other = tmp_path / "note.txt"
        other.write_text("ce n'est pas un pdf", encoding="utf-8")
        with pytest.raises(MailError):
            send_label_email(valid_config(), "a@b.fr", "s", "b", other,
                             smtp_factory=smtp_factory())

    def test_missing_file_is_reported_in_french(self, tmp_path):
        with pytest.raises(MailError):
            send_label_email(valid_config(), "a@b.fr", "s", "b", tmp_path / "absent.pdf",
                             smtp_factory=smtp_factory())

    def test_authentication_failure_is_wrapped(self, pdf):
        def factory(host, port, timeout):
            smtp = FakeSMTP(host, port, timeout)

            def login(username, password):
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Auth failed")

            smtp.login = login  # type: ignore[method-assign]
            return smtp

        with pytest.raises(MailError) as exc:
            send_label_email(valid_config(), "a@b.fr", "s", "b", pdf, smtp_factory=factory)
        assert "Authentification" in str(exc.value)

    def test_connection_failure_is_wrapped(self, pdf):
        def factory(host, port, timeout):
            raise OSError("Connection refused")

        with pytest.raises(MailError) as exc:
            send_label_email(valid_config(), "a@b.fr", "s", "b", pdf, smtp_factory=factory)
        assert "Envoi impossible" in str(exc.value)


# -- Enregistrement comme plugin (T1) -----------------------------------------------------

class TestPluginRegistration:
    def test_entry_point_is_declared(self):
        from importlib.metadata import entry_points

        names = {entry.name for entry in entry_points(group="edusync_ad.modules")}
        assert "label_studio" in names

    def test_module_metadata_and_instantiation(self):
        from edusync_ad.plugins import get_plugin_manager

        module = get_plugin_manager().load_module("label_studio")
        assert module.metadata.id == "label_studio"
        assert module.metadata.requires_ad is True
        assert "export_data" in module.metadata.permissions
