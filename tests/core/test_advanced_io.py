"""Tests du module Import/Export avancés (M22)."""

from __future__ import annotations

import base64

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.advanced_io import (
    DEFAULT_EXPORT_ATTRIBUTES,
    DEFAULT_HTML_TEMPLATE,
    GEP_FIELD_ALIASES,
    LDAP_SOURCE_MODES,
    AdvancedIOConfig,
    LDAPSource,
    build_source_query,
    export_entries_csv,
    export_ldif,
    export_vcards,
    find_placeholders,
    gep_to_edusync_csv,
    import_from_ad,
    ldif_entry,
    ldif_line,
    ldif_needs_base64,
    load_adv_io_config,
    merge_html,
    parse_gep_csv,
    render_template,
    run_external_tool,
    safe_filename,
    save_adv_io_config,
    suggest_gep_mapping,
    validate_ldap_filter,
    vcard_escape,
    vcard_for,
)

DOMAIN = "lycee.local"
BASE_DN = "dc=lycee,dc=local"
ADMIN_BIND_DN = f"cn=admin@{DOMAIN},{BASE_DN}"
ADMIN_PASSWORD = "AdminPass123!"
OU_PROFS_DN = f"ou=profs,{BASE_DN}"


def make_mock_factory():
    server = Server("mock-server")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(
        ADMIN_BIND_DN, {"userPassword": ADMIN_PASSWORD, "sAMAccountName": "admin"}
    )
    seed.strategy.add_entry(OU_PROFS_DN, {"objectClass": "organizationalUnit", "ou": "profs"})
    seed.strategy.add_entry(
        f"cn=Jean Durand,{OU_PROFS_DN}",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "objectCategory": "person",
            "cn": "Jean Durand",
            "givenName": "Jean",
            "sn": "Durand",
            "sAMAccountName": "jdurand",
            "mail": "jean.durand@lycee.fr",
            "telephoneNumber": "0123456789",
            "department": "SVT",
            "memberOf": [f"cn=Profs,{BASE_DN}", f"cn=SVT,{BASE_DN}"],
        },
    )
    seed.strategy.add_entry(
        f"cn=Alice Martin,{OU_PROFS_DN}",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "objectCategory": "person",
            "cn": "Alice Martin",
            "givenName": "Alice",
            "sn": "Martin",
            "sAMAccountName": "amartin",
            "mail": "alice.martin@lycee.fr",
        },
    )

    def factory(controller, bind_user, password, use_ssl):
        return Connection(server, user=bind_user, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad():
    conn = ADConnection(connection_factory=make_mock_factory())
    conn.connect(DOMAIN, "10.0.0.1", ADMIN_BIND_DN, ADMIN_PASSWORD)
    return conn


class TestValidateLdapFilter:
    def test_valid_filters(self):
        assert validate_ldap_filter("(objectClass=group)") == []
        assert validate_ldap_filter("(&(objectClass=user)(objectCategory=person))") == []
        assert validate_ldap_filter("  (cn=*)  ") == []

    def test_empty(self):
        assert any("vide" in e for e in validate_ldap_filter(""))
        assert any("vide" in e for e in validate_ldap_filter("   "))

    def test_missing_parentheses(self):
        errors = validate_ldap_filter("objectClass=user")
        assert any("parenthèses" in e for e in errors)

    def test_unbalanced(self):
        assert any("déséquilibrées" in e for e in validate_ldap_filter("(&(a=1)(b=2)"))
        assert any("déséquilibrées" in e for e in validate_ldap_filter("(a=1))"))

    def test_nul_byte(self):
        assert any("octet nul" in e for e in validate_ldap_filter("(a=1)\x00"))

    def test_too_long(self):
        assert any("trop long" in e for e in validate_ldap_filter("(" + "a" * 3000 + ")"))


class TestLDAPSource:
    def test_modes_catalog(self):
        assert set(LDAP_SOURCE_MODES) == {"ou", "groupe", "filtre"}

    def test_default_attributes(self):
        assert "mail" in DEFAULT_EXPORT_ATTRIBUTES and "sn" in DEFAULT_EXPORT_ATTRIBUTES
        assert len(DEFAULT_EXPORT_ATTRIBUTES) >= 10

    def test_ou_mode_valid(self):
        source = LDAPSource(mode="ou", ou_dn="OU=X,DC=l,DC=local")
        assert source.validate() == []

    def test_ou_mode_empty(self):
        assert any("OU vide" in e for e in LDAPSource(mode="ou").validate())

    def test_groupe_mode(self):
        source = LDAPSource(mode="groupe", base_dn="DC=l,DC=local")
        assert any("Groupe vide" in e for e in source.validate())
        source.group_dn = "CN=G,DC=l,DC=local"
        assert source.validate() == []
        source.base_dn = ""
        assert any("Base DN" in e for e in source.validate())

    def test_filtre_mode(self):
        source = LDAPSource(mode="filtre", base_dn="DC=l,DC=local", ldap_filter="bad")
        assert any("parenthèses" in e for e in source.validate())

    def test_limits(self):
        assert any("attribut" in e for e in LDAPSource(mode="ou", ou_dn="x", attributes=[]).validate())
        assert any("Limite" in e for e in LDAPSource(mode="ou", ou_dn="x", size_limit=0).validate())
        assert any("Limite" in e for e in LDAPSource(mode="ou", ou_dn="x", size_limit=999_999).validate())

    def test_unknown_mode(self):
        assert any("inconnue" in e for e in LDAPSource(mode="citrix").validate())


class TestBuildSourceQuery:
    def test_ou_uses_users_filter(self):
        base, flt = build_source_query(LDAPSource(mode="ou", ou_dn="OU=X,DC=l"))
        assert base == "OU=X,DC=l"
        assert "(objectCategory=person)" in flt

    def test_groupe_escapes_filter_chars(self):
        from ldap3.utils.conv import escape_filter_chars

        dn = "CN=Profs (Nord),OU=G,DC=l"
        base, flt = build_source_query(
            LDAPSource(mode="groupe", base_dn="DC=l", group_dn=dn)
        )
        assert base == "DC=l"
        assert f"(memberOf={escape_filter_chars(dn)})" in flt
        assert "(" + dn not in flt  # jamais injecté brut

    def test_filtre_custom(self):
        base, flt = build_source_query(
            LDAPSource(mode="filtre", base_dn="DC=l", ldap_filter="  (objectClass=group)  ")
        )
        assert (base, flt) == ("DC=l", "(objectClass=group)")


class FakeSearchConnection:
    def __init__(self):
        self.calls: list[tuple] = []

    def search_entries(self, base, flt, attributes, size_limit=0):
        self.calls.append((base, flt, list(attributes), size_limit))
        return [{"dn": "CN=A,DC=l", "cn": "A", "memberOf": ["G1", "G2"]}]


class TestImportFromAd:
    def test_delegates_to_search_entries(self):
        fake = FakeSearchConnection()
        entries = import_from_ad(
            fake, LDAPSource(mode="ou", ou_dn="OU=X,DC=l", attributes=["cn"], size_limit=42)
        )
        assert entries[0]["cn"] == "A"
        assert fake.calls == [
            ("OU=X,DC=l", "(&(objectClass=user)(objectCategory=person))", ["cn"], 42)
        ]

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="OU vide"):
            import_from_ad(FakeSearchConnection(), LDAPSource(mode="ou"))

    def test_integration_ou_mode(self, ad):
        entries = import_from_ad(
            ad,
            LDAPSource(mode="ou", ou_dn=OU_PROFS_DN, attributes=["cn", "mail", "sn"]),
        )
        names = {e["cn"] for e in entries}
        assert names == {"Jean Durand", "Alice Martin"}
        jean = next(e for e in entries if e["cn"] == "Jean Durand")
        assert jean["mail"] == "jean.durand@lycee.fr"
        assert jean["dn"].startswith("cn=Jean Durand")

    def test_integration_filtre_mode(self, ad):
        entries = import_from_ad(
            ad,
            LDAPSource(
                mode="filtre", base_dn=BASE_DN,
                ldap_filter="(cn=Alice Martin)", attributes=["cn"],
            ),
        )
        assert len(entries) == 1 and entries[0]["cn"] == "Alice Martin"

    def test_not_connected_raises(self):
        plain = ADConnection(connection_factory=make_mock_factory())
        with pytest.raises(ADError):
            import_from_ad(plain, LDAPSource(mode="ou", ou_dn=OU_PROFS_DN))


class TestSearchEntries:
    def test_single_and_multi_valued(self, ad):
        entries = ad.search_entries(
            OU_PROFS_DN, "(objectClass=user)",
            attributes=["cn", "mail", "memberOf"],
        )
        jean = next(e for e in entries if e["cn"] == "Jean Durand")
        assert jean["mail"] == "jean.durand@lycee.fr"          # mono-valué → str
        assert jean["memberOf"] == [f"cn=Profs,{BASE_DN}", f"cn=SVT,{BASE_DN}"]
        alice = next(e for e in entries if e["cn"] == "Alice Martin")
        assert alice["memberOf"] == ""                         # absent → ""

    def test_missing_attribute_is_empty_string(self, ad):
        entries = ad.search_entries(OU_PROFS_DN, "(cn=Alice Martin)", attributes=["title"])
        assert entries[0]["title"] == ""
        assert entries[0]["dn"].startswith("cn=Alice Martin")

    def test_no_match_returns_empty(self, ad):
        assert ad.search_entries(BASE_DN, "(cn=Inexistant)", attributes=["cn"]) == []

    def test_requires_connection(self):
        plain = ADConnection(connection_factory=make_mock_factory())
        with pytest.raises(ADError):
            plain.search_entries(BASE_DN, "(objectClass=user)")


class TestExportEntriesCsv:
    def test_columns_and_values(self, tmp_path):
        entries = [
            {"dn": "CN=A", "cn": "A", "memberOf": ["x", "y"], "photo": b"\x00"},
            {"dn": "CN=B", "cn": "B", "mail": "b@x.fr"},
        ]
        path = export_entries_csv(entries, tmp_path / "e.csv")
        lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
        assert lines[0] == "dn;cn;memberOf;photo;mail"
        assert "x | y" in lines[1]
        assert lines[1].split(";")[3] == ""          # bytes ignorés
        assert lines[2].endswith("b@x.fr")

    def test_empty(self, tmp_path):
        path = export_entries_csv([], tmp_path / "e.csv")
        assert path.read_text(encoding="utf-8-sig").strip() == ""


class TestLDIF:
    def test_needs_base64_rules(self):
        assert not ldif_needs_base64(b"simple")
        assert not ldif_needs_base64(b"")
        assert ldif_needs_base64(b" leading")
        assert ldif_needs_base64(b":colon")
        assert ldif_needs_base64(b"<chevron")
        assert ldif_needs_base64("Müller".encode())      # UTF-8 non-ASCII
        assert ldif_needs_base64(b"tab\there") is False  # tab = SAFE-CHAR
        assert ldif_needs_base64(b"line\nbreak")
        assert ldif_needs_base64(b"del\x7f")

    def test_plain_and_base64_lines(self):
        assert ldif_line("mail", "j@x.fr") == "mail: j@x.fr"
        assert ldif_line("cn", "Müller") == "cn:: " + base64.b64encode("Müller".encode()).decode()
        assert ldif_line("description", "") == "description:"

    def test_line_folding(self):
        folded = ldif_line("description", "x" * 200).split("\n")
        assert all(len(line) <= 76 for line in folded)
        assert folded[1].startswith(" ")
        # déplié = ligne d'origine complète
        unfolded = folded[0] + "".join(line[1:] for line in folded[1:])
        assert unfolded == "description: " + "x" * 200

    def test_entry_structure(self):
        entry = {
            "dn": "CN=Müller,DC=l", "cn": "Müller",
            "mail": ["a@x.fr", "b@x.fr"], "description": "", "absent": None,
        }
        block = ldif_entry(
            entry, attributes=["cn", "mail", "description", "absent"], changetype="add"
        )
        lines = block.split("\n")
        assert lines[0].startswith("dn:: ")          # DN accentué → base64
        assert lines[1] == "changetype: add"
        assert "mail: a@x.fr" in lines and "mail: b@x.fr" in lines
        assert not any(line.startswith("description") for line in lines)
        assert not any(line.startswith("absent") for line in lines)

    def test_entry_without_attributes_argument_exports_all(self):
        entry = {"dn": "CN=A,DC=l", "cn": "A", "mail": "a@x.fr", "empty": ""}
        block = ldif_entry(entry)
        assert "cn: A" in block and "mail: a@x.fr" in block
        assert "empty" not in block

    def test_export_file(self, tmp_path):
        entries = [{"dn": "CN=A,DC=l", "cn": "A"}, {"dn": "CN=B,DC=l", "cn": "B"}]
        path = export_ldif(entries, tmp_path / "out.ldif", changetype="add")
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert "\n\n" in text                       # séparation des entrées
        assert text.count("changetype: add") == 2

    def test_export_empty_file(self, tmp_path):
        path = export_ldif([], tmp_path / "empty.ldif")
        assert path.read_text(encoding="utf-8") == ""


class TestVCard:
    def test_escaping(self):
        assert vcard_escape("a,b;c\nd\\e") == "a\\,b\\;c\\nd\\\\e"

    def test_basic_card(self):
        card = vcard_for({
            "sn": "Durand, Jr", "givenName": "Jean", "displayName": "Jean Durand",
            "department": "SVT", "title": "Professeur", "mail": "j@x.fr",
            "telephoneNumber": "0123", "mobile": "0612",
            "streetAddress": "1 rue des Écoles", "l": "Paris", "postalCode": "75000",
            "co": "France", "description": "L1\nL2",
        })
        assert card.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
        assert card.endswith("END:VCARD")
        assert "FN:Jean Durand" in card
        assert "N:Durand\\, Jr;Jean;;;" in card
        assert "ORG:SVT" in card and "TITLE:Professeur" in card
        assert "EMAIL;TYPE=INTERNET,WORK:j@x.fr" in card
        assert "TEL;TYPE=WORK,VOICE:0123" in card
        assert "TEL;TYPE=CELL:0612" in card
        assert "ADR;TYPE=WORK:;;1 rue des Écoles;Paris;;75000;France" in card
        assert "NOTE:L1\\nL2" in card

    def test_fn_fallback_without_display_name(self):
        card = vcard_for({"sn": "Martin", "givenName": "Alice"})
        assert "FN:Alice Martin" in card
        card = vcard_for({"cn": "Seul CN"})
        assert "FN:Seul CN" in card

    def test_omits_empty_fields(self):
        card = vcard_for({"cn": "X"})
        assert "ORG:" not in card and "TEL;" not in card and "ADR;" not in card
        assert "N:;;;" in card

    def test_photo_optional(self):
        entry = {"cn": "X", "jpegPhoto": b"\xff\xd8BIN"}
        with_photo = vcard_for(entry, include_photo=True)
        assert "PHOTO;ENCODING=b;TYPE=JPEG:" in with_photo
        assert base64.b64encode(b"\xff\xd8BIN").decode() in with_photo
        assert "PHOTO" not in vcard_for(entry)
        assert "PHOTO" not in vcard_for({"cn": "X"}, include_photo=True)  # pas de photo

    def test_thumbnail_fallback(self):
        card = vcard_for({"cn": "X", "thumbnailPhoto": b"\xff\xd8TH"}, include_photo=True)
        assert base64.b64encode(b"\xff\xd8TH").decode() in card

    def test_line_folding(self):
        card = vcard_for({"cn": "X", "description": "y" * 300})
        assert all(len(line) <= 75 for line in card.split("\r\n"))

    def test_export_single_file(self, tmp_path):
        path = export_vcards([{"cn": "A"}, {"cn": "B"}], tmp_path / "c.vcf")
        raw = path.read_bytes()
        assert raw.count(b"BEGIN:VCARD") == 2
        assert b"\r\n" in raw


class TestMergeHtml:
    def test_find_placeholders_order_and_dedup(self):
        assert find_placeholders("{{b}} {{a}} {{b}}") == ["b", "a"]
        assert find_placeholders("aucun") == []

    def test_render_escapes_html(self):
        out = render_template("{{sn}} {{mail}}", {"sn": "<b>&", "mail": "a@b.fr"})
        assert out == "&lt;b&gt;&amp; a@b.fr"

    def test_render_leaves_unknown_placeholder(self):
        assert render_template("{{inconnu}}", {"cn": "x"}) == "{{inconnu}}"

    def test_render_lists_and_bytes(self):
        out = render_template("{{memberOf}}|{{photo}}", {"memberOf": ["a", "b"], "photo": b"\x00"})
        assert out == "a, b|"

    def test_default_template_has_fields(self):
        fields = find_placeholders(DEFAULT_HTML_TEMPLATE)
        assert "sn" in fields and "sAMAccountName" in fields and "mail" in fields

    def test_generates_files_and_index(self, tmp_path):
        rows = [
            {"sAMAccountName": "jdurand", "cn": "Jean Durand"},
            {"sAMAccountName": "a martin", "cn": "Alice Martin"},
        ]
        paths = merge_html(
            "{{cn}} — {{sAMAccountName}}", rows, tmp_path,
            filename_pattern="{sAMAccountName}_conv.html",
        )
        assert sorted(p.name for p in paths) == ["a_martin_conv.html", "jdurand_conv.html"]
        assert "Jean Durand" in paths[0].read_text(encoding="utf-8")
        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "jdurand_conv.html" in index and "2 document" in index

    def test_filename_sanitized_and_fallback(self, tmp_path):
        rows = [{"sAMAccountName": "a/b\\c", "cn": "X"}, {"cn": "Y"}]
        paths = merge_html("{{cn}}", rows, tmp_path, filename_pattern="{sAMAccountName}.html")
        assert paths[0].name == "a_b_c.html"
        paths2 = merge_html(
            "{{cn}}", rows, tmp_path / "sub", filename_pattern="{inconnu}.html"
        )
        assert [p.name for p in paths2] == ["document_1.html", "document_2.html"]

    def test_html_suffix_added(self, tmp_path):
        paths = merge_html(
            "{{cn}}", [{"cn": "X", "sAMAccountName": "x"}], tmp_path,
            filename_pattern="{sAMAccountName}",
        )
        assert paths[0].name == "x.html"

    def test_no_index_option(self, tmp_path):
        merge_html("{{cn}}", [{"cn": "X"}], tmp_path, make_index=False)
        assert not (tmp_path / "index.html").exists()

    @pytest.mark.parametrize(
        "template,rows,needle",
        [
            ("", [{"cn": "X"}], "vide"),
            ("{{cn}}", [], "Aucun objet"),
            ("sans champ", [{"cn": "X"}], "sans champ"),
            ("{{ }}", [{"cn": "X"}], "sans champ"),
        ],
    )
    def test_invalid_inputs(self, tmp_path, template, rows, needle):
        with pytest.raises(ValueError, match=needle):
            merge_html(template, rows, tmp_path)

    def test_safe_filename(self):
        assert safe_filename("a/b\\c d") == "a_b_c_d"
        assert safe_filename("###") == "document"
        assert safe_filename(".hidden") == "hidden"


class TestGEP:
    def test_aliases_catalog(self):
        assert set(GEP_FIELD_ALIASES) == {
            "prenom", "nom", "classe", "ou", "email", "date_naissance", "numero"
        }

    def test_exact_mapping_with_accents_and_case(self):
        mapping = suggest_gep_mapping(
            ["NOM DE FAMILLE", "PRENOM", "CLASSE", "MAIL", "DATE DE NAISSANCE", "MATRICULE"]
        )
        assert mapping["nom"] == "NOM DE FAMILLE"
        assert mapping["prenom"] == "PRENOM"
        assert mapping["email"] == "MAIL"
        assert mapping["date_naissance"] == "DATE DE NAISSANCE"
        assert mapping["numero"] == "MATRICULE"

    def test_substring_mapping(self):
        mapping = suggest_gep_mapping(["Nom", "Prenom", "Division", "Etablissement"])
        assert mapping["classe"] == "Division"
        assert mapping["ou"] == "Etablissement"

    def test_short_alias_ou_never_matches_cours(self):
        # « ou » (2 caractères) ne doit pas matcher « cours », « groupe »…
        assert suggest_gep_mapping(["Prenom", "Nom", "Cours"])["ou"] == ""

    def test_prenom_nom_not_cross_matched(self):
        mapping = suggest_gep_mapping(["eleve_prenom", "eleve_nom"])
        assert mapping["prenom"] == "eleve_prenom"
        assert mapping["nom"] == "eleve_nom"

    def test_parse_skips_rows_without_names(self):
        text = "NOM;PRENOM;CLASSE\nDurand;Jean;6A\n;SansNom;6A\nDubois;;6A\n"
        result = parse_gep_csv(text)
        assert [row["nom"] for row in result.rows] == ["Durand"]
        assert result.skipped_row_numbers == [2, 3]
        assert result.delimiter == ";"

    def test_parse_normalizes_to_expected_columns(self):
        result = parse_gep_csv("NOM;PRENOM;CLASSE\nDurand;Jean;6A\n")
        assert list(result.rows[0]) == [
            "prenom", "nom", "classe", "ou", "email", "date_naissance", "numero"
        ]
        assert result.rows[0]["prenom"] == "Jean"

    def test_explicit_mapping(self):
        result = parse_gep_csv(
            "ELEVE;FAMILIA\nPaul;Pereira",
            {"prenom": "ELEVE", "nom": "FAMILIA", "classe": "", "ou": "",
             "email": "", "date_naissance": "", "numero": ""},
        )
        assert result.rows[0]["prenom"] == "Paul" and result.rows[0]["nom"] == "Pereira"

    def test_empty_text(self):
        assert parse_gep_csv("").rows == []

    def test_converted_csv_loadable_by_edusync(self, tmp_path):
        from edusync_ad.core.csv_io import EXPECTED_COLUMNS, load_rows

        result = parse_gep_csv("NOM;PRENOM;CLASSE\nDurand;Jean;6A\n")
        path = gep_to_edusync_csv(result.rows, tmp_path / "gep.csv")
        loaded = load_rows(path, {column: column for column in EXPECTED_COLUMNS})
        assert len(loaded.rows) == 1
        assert loaded.rows[0].prenom == "Jean"
        assert loaded.rows[0].classe == "6A"


class TestConfigAndTool:
    def test_persistence(self, tmp_path, monkeypatch):
        import edusync_ad.core.advanced_io as mod

        monkeypatch.setattr(mod, "ADV_IO_CONFIG_FILE", tmp_path / "adv.json")
        assert load_adv_io_config() == AdvancedIOConfig()
        config = AdvancedIOConfig(gep_tool_path="/opt/gep/exporteur")
        save_adv_io_config(config)
        assert load_adv_io_config() == config

    def test_corrupted_file(self, tmp_path, monkeypatch):
        import edusync_ad.core.advanced_io as mod

        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        monkeypatch.setattr(mod, "ADV_IO_CONFIG_FILE", bad)
        assert load_adv_io_config() == AdvancedIOConfig()

    def test_run_tool_not_configured(self):
        assert run_external_tool(AdvancedIOConfig()) == "Aucun outil tiers configuré."

    def test_run_tool_missing_path(self):
        message = run_external_tool(AdvancedIOConfig(gep_tool_path="/nulle part/x.exe"))
        assert message is not None and "introuvable" in message

    def test_run_tool_not_executable(self, tmp_path):
        tool = tmp_path / "outil.txt"
        tool.write_text("x", encoding="utf-8")
        message = run_external_tool(AdvancedIOConfig(gep_tool_path=str(tool)))
        # selon la plateforme : impossible à exécuter, ou (rare) lancé
        assert message is None or "Lancement impossible" in message
