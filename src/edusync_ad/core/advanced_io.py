"""M22 — Import/Export avancés.

- **Import LDAP** : lecture depuis une OU, un groupe AD ou un filtre LDAP
  personnalisé (via ``ADConnection.search_entries``, recherche générique).
- **Export LDIF** (RFC 2849) paramétrable : attributs au choix, encodage
  base64 automatique des valeurs non sûres, pliage des lignes à 76 caractères,
  ``changetype`` optionnel — destiné aux annuaires LDAP tiers.
- **Export vCard 3.0** : cartes de visite (FN / N / ORG / TITLE / EMAIL / TEL
  / ADR / NOTE), photo JPEG optionnelle encodée en base64, CRLF, pliage 75.
- **Publipostage HTML** : gabarit avec ``{{champ}}`` → un fichier par objet
  (nom de fichier paramétrable) + ``index.html`` de synthèse.
- **Import GEP / base Éducation nationale** : produit par un **outil tiers
  fourni à l'établissement** dont le format est libre — association
  automatique des colonnes (prénom, nom, classe, …) puis conversion en CSV au
  format d'import EduSync (``csv_io.EXPECTED_COLUMNS``), directement
  utilisable par la création de comptes (§4).
"""

from __future__ import annotations

import base64
import csv
import html
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from edusync_ad.core.config import config_dir
from edusync_ad.core.csv_io import EXPECTED_COLUMNS, detect_delimiter

ADV_IO_CONFIG_FILE = config_dir() / "adv_io.json"

# -- Import LDAP ----------------------------------------------------------------------------

LDAP_SOURCE_MODES: dict[str, str] = {
    "ou": "Depuis une OU",
    "groupe": "Depuis un groupe AD",
    "filtre": "Filtre LDAP personnalisé",
}

DEFAULT_EXPORT_ATTRIBUTES: list[str] = [
    "cn", "givenName", "sn", "displayName",
    "sAMAccountName", "userPrincipalName", "mail",
    "telephoneNumber", "mobile", "department", "title",
    "physicalDeliveryOfficeName", "streetAddress", "l", "postalCode", "co",
    "description", "memberOf",
]

MAX_SIZE_LIMIT = 50_000
USERS_FILTER = "(&(objectClass=user)(objectCategory=person))"


def validate_ldap_filter(ldap_filter: str) -> list[str]:
    """Contrôle syntaxique basique avant envoi au serveur LDAP
    (parenthèses équilibrées, entre parenthèses, pas d'octet nul)."""
    text = (ldap_filter or "").strip()
    if not text:
        return ["Filtre LDAP vide."]
    errors: list[str] = []
    if "\x00" in text:
        errors.append("Le filtre contient un octet nul.")
    if not (text.startswith("(") and text.endswith(")")):
        errors.append(
            "Le filtre doit être entre parenthèses "
            "(ex : (&(objectClass=user)(objectCategory=person)))."
        )
    depth = 0
    balanced = True
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                balanced = False
                break
    if not balanced or depth != 0:
        errors.append("Parenthèses déséquilibrées dans le filtre.")
    if len(text) > 2048:
        errors.append("Filtre trop long (2048 caractères maximum).")
    return errors


@dataclass
class LDAPSource:
    """Source LDAP d'un import : OU, groupe ou filtre personnalisé."""

    mode: str = "ou"
    base_dn: str = ""                  # base de recherche (domaine) — groupe/filtre
    ou_dn: str = ""
    group_dn: str = ""
    ldap_filter: str = USERS_FILTER
    attributes: list[str] = field(default_factory=lambda: list(DEFAULT_EXPORT_ATTRIBUTES))
    size_limit: int = 5_000

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in LDAP_SOURCE_MODES:
            errors.append("Source LDAP inconnue (ou / groupe / filtre).")
        if not self.attributes:
            errors.append("Aucun attribut sélectionné.")
        if not (0 < self.size_limit <= MAX_SIZE_LIMIT):
            errors.append(f"Limite invalide (1 à {MAX_SIZE_LIMIT}).")
        if self.mode == "ou":
            if not self.ou_dn.strip():
                errors.append("OU vide.")
        elif self.mode == "groupe":
            if not self.group_dn.strip():
                errors.append("Groupe vide.")
            if not self.base_dn.strip():
                errors.append("Base DN vide (domaine non résolu).")
        elif self.mode == "filtre":
            if not self.base_dn.strip():
                errors.append("Base DN vide (domaine non résolu).")
            errors.extend(validate_ldap_filter(self.ldap_filter))
        return errors


def build_source_query(source: LDAPSource) -> tuple[str, str]:
    """(base_dn, filtre) LDAP correspondant à la source choisie."""
    if source.mode == "ou":
        return source.ou_dn, USERS_FILTER
    if source.mode == "groupe":
        from ldap3.utils.conv import escape_filter_chars

        return source.base_dn, (
            f"(&(objectClass=user)(objectCategory=person)"
            f"(memberOf={escape_filter_chars(source.group_dn)}))"
        )
    return source.base_dn, source.ldap_filter.strip()


def import_from_ad(connection, source: LDAPSource) -> list[dict]:
    """Charge les objets LDAP décrits par ``source`` (raise ValueError si
    source invalide, ADError côté connexion)."""
    errors = source.validate()
    if errors:
        raise ValueError("; ".join(errors))
    base_dn, ldap_filter = build_source_query(source)
    return connection.search_entries(
        base_dn, ldap_filter, source.attributes, size_limit=source.size_limit
    )


def export_entries_csv(entries: Sequence[dict], dest: Path) -> Path:
    """CSV générique des entrées chargées (';' UTF-8 BOM, convention Excel FR).
    Les listes sont jointes par « | », les octets binaires ignorés."""
    columns: list[str] = []
    for entry in entries:
        for key in entry:
            if key not in columns:
                columns.append(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(columns)
        for entry in entries:
            row = []
            for key in columns:
                value = entry.get(key, "")
                if isinstance(value, bytes):
                    row.append("")
                elif isinstance(value, list):
                    row.append(" | ".join(str(v) for v in value))
                else:
                    row.append(value)
            writer.writerow(row)
    return dest


# -- Export LDIF (RFC 2849) --------------------------------------------------------------------

LDIF_LINE_MAX = 76


def _to_bytes(value) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def ldif_needs_base64(data: bytes) -> bool:
    """RFC 2849 : base64 si la valeur commence par espace / «:» / «<»,
    contient NUL, CR, LF, ou tout octet hors ASCII imprimable (UTF-8
    accentué compris)."""
    if not data:
        return False
    if data[0] in (0x20, 0x3A, 0x3C):
        return True
    return any(byte in (0x00, 0x0A, 0x0D) or byte > 0x7E for byte in data)


def _fold_line(line: str) -> list[str]:
    """Pliage RFC 2849 : 76 caractères maximum, suites préfixées d'une espace."""
    if len(line) <= LDIF_LINE_MAX:
        return [line]
    folded = [line[:LDIF_LINE_MAX]]
    rest = line[LDIF_LINE_MAX:]
    while rest:
        folded.append(" " + rest[: LDIF_LINE_MAX - 1])
        rest = rest[LDIF_LINE_MAX - 1 :]
    return folded


def ldif_line(attribute: str, value) -> str:
    """Une ligne LDIF (pliée) : ``attr: valeur`` ou ``attr:: base64``."""
    data = _to_bytes(value)
    if not data:
        return f"{attribute}:"
    if ldif_needs_base64(data):
        raw = f"{attribute}:: {base64.b64encode(data).decode('ascii')}"
    else:
        raw = f"{attribute}: {data.decode('ascii')}"
    return "\n".join(_fold_line(raw))


def ldif_entry(
    entry: dict,
    attributes: Sequence[str] | None = None,
    changetype: str | None = None,
) -> str:
    """Bloc LDIF d'une entrée : ``dn`` en tête, puis les attributs demandés
    (multi-valués = lignes répétées), ``changetype`` optionnel (« add »)."""
    lines = [ldif_line("dn", entry.get("dn", ""))]
    if changetype:
        lines.append(f"changetype: {changetype}")
    keys = list(attributes) if attributes is not None else [
        k for k in entry if k != "dn"
    ]
    for attr in keys:
        if attr == "dn":
            continue
        value = entry.get(attr)
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            for item in value:
                lines.append(ldif_line(attr, item))
        else:
            lines.append(ldif_line(attr, value))
    return "\n".join(lines)


def export_ldif(
    entries: Sequence[dict],
    dest: Path,
    *,
    attributes: Sequence[str] | None = None,
    changetype: str | None = None,
) -> Path:
    """Écrit un fichier LDIF (UTF-8) — entrées séparées par une ligne vide."""
    blocks = [
        ldif_entry(entry, attributes=attributes, changetype=changetype)
        for entry in entries
    ]
    content = ("\n\n".join(blocks) + "\n") if blocks else ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


# -- Export vCard 3.0 ---------------------------------------------------------------------------

VCARD_MAX_LINE = 75


def vcard_escape(value) -> str:
    """Échappement vCard : barre inverse, sauts de ligne, «;» et «,»."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _vcard_fold(line: str) -> list[str]:
    if len(line) <= VCARD_MAX_LINE:
        return [line]
    folded = [line[:VCARD_MAX_LINE]]
    rest = line[VCARD_MAX_LINE:]
    while rest:
        folded.append(" " + rest[: VCARD_MAX_LINE - 1])
        rest = rest[VCARD_MAX_LINE - 1 :]
    return folded


def _first(entry: dict, *attributes: str) -> str:
    """Première valeur non binaire parmi les attributs demandés."""
    for attr in attributes:
        value = entry.get(attr)
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, bytes) or value is None:
            continue
        if str(value).strip():
            return str(value)
    return ""


def vcard_for(entry: dict, *, include_photo: bool = False) -> str:
    """Carte vCard 3.0 (lignes CRLF, pliées à 75 caractères)."""
    family = _first(entry, "sn")
    given = _first(entry, "givenName")
    fn = (
        _first(entry, "displayName")
        or " ".join(part for part in (given, family) if part)
        or _first(entry, "cn")
    )
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{vcard_escape(fn)}",
        f"N:{vcard_escape(family)};{vcard_escape(given)};;;",
    ]
    if org := (_first(entry, "company") or _first(entry, "department")):
        lines.append(f"ORG:{vcard_escape(org)}")
    if title := _first(entry, "title"):
        lines.append(f"TITLE:{vcard_escape(title)}")
    if mail := _first(entry, "mail"):
        lines.append(f"EMAIL;TYPE=INTERNET,WORK:{vcard_escape(mail)}")
    if phone := _first(entry, "telephoneNumber"):
        lines.append(f"TEL;TYPE=WORK,VOICE:{vcard_escape(phone)}")
    if mobile := _first(entry, "mobile"):
        lines.append(f"TEL;TYPE=CELL:{vcard_escape(mobile)}")
    street = _first(entry, "streetAddress")
    locality = _first(entry, "l")
    region = _first(entry, "st")
    postal = _first(entry, "postalCode")
    country = _first(entry, "co")
    if any((street, locality, region, postal, country)):
        lines.append(
            "ADR;TYPE=WORK:;;"
            f"{vcard_escape(street)};{vcard_escape(locality)};"
            f"{vcard_escape(region)};{vcard_escape(postal)};{vcard_escape(country)}"
        )
    if note := _first(entry, "description"):
        lines.append(f"NOTE:{vcard_escape(note)}")
    if include_photo:
        photo = entry.get("jpegPhoto") or entry.get("thumbnailPhoto")
        if isinstance(photo, bytes) and photo:
            encoded = base64.b64encode(photo).decode("ascii")
            lines.append(f"PHOTO;ENCODING=b;TYPE=JPEG:{encoded}")
    lines.append("END:VCARD")

    folded: list[str] = []
    for line in lines:
        folded.extend(_vcard_fold(line))
    return "\r\n".join(folded)


def export_vcards(
    entries: Sequence[dict], dest: Path, *, include_photo: bool = False
) -> Path:
    """Écrit toutes les cartes dans un unique fichier ``.vcf`` (UTF-8)."""
    cards = [vcard_for(entry, include_photo=include_photo) for entry in entries]
    content = "\r\n".join(cards) + ("\r\n" if cards else "")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


# -- Publipostage HTML ---------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

DEFAULT_HTML_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Convention — {{cn}}</title>
  <style>
    body { font-family: Georgia, serif; margin: 3cm; line-height: 1.6; }
    h1 { border-bottom: 2px solid #1B4F91; padding-bottom: .3em; }
    table { border-collapse: collapse; margin-top: 1.5em; }
    td { border: 1px solid #999; padding: .5em 1em; }
  </style>
</head>
<body>
  <h1>Convention de poste</h1>
  <p>Entre l'établissement et :</p>
  <table>
    <tr><td>Nom</td><td>{{sn}} {{givenName}}</td></tr>
    <tr><td>Identifiant</td><td>{{sAMAccountName}}</td></tr>
    <tr><td>Adresse mail</td><td>{{mail}}</td></tr>
    <tr><td>Service</td><td>{{department}}</td></tr>
    <tr><td>Fonction</td><td>{{title}}</td></tr>
  </table>
  <p>Fait à __________________, le ____________</p>
</body>
</html>
"""


def find_placeholders(template: str) -> list[str]:
    """Champs ``{{…}}`` du gabarit, dans l'ordre et sans doublon."""
    found: list[str] = []
    for match in PLACEHOLDER_RE.finditer(template or ""):
        key = match.group(1)
        if key not in found:
            found.append(key)
    return found


def render_template(template: str, row: dict) -> str:
    """Remplace ``{{champ}}`` par la valeur HTML-échappée. Un champ absent du
    reste est laissé tel quel (visible dans le document = erreur signalée)."""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in row:
            return match.group(0)
        value = row[key]
        if isinstance(value, bytes):
            return ""
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        return html.escape(str(value))

    return PLACEHOLDER_RE.sub(replace, template or "")


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._")
    return cleaned or "document"


def merge_html(
    template: str,
    rows: Sequence[dict],
    out_dir: Path,
    *,
    filename_pattern: str = "{sAMAccountName}_document.html",
    make_index: bool = True,
) -> list[Path]:
    """Génère un fichier HTML par objet + un ``index.html`` de synthèse.

    ``filename_pattern`` est formaté avec les champs de chaque objet ;
    nom de fichier assaini, repli ``document_N.html`` si le motif échoue."""
    if not template.strip():
        raise ValueError("Gabarit HTML vide.")
    if not rows:
        raise ValueError("Aucun objet à publiposter.")
    if not find_placeholders(template):
        raise ValueError("Gabarit sans champ valide (syntaxe attendue : {{champ}}).")

    out_dir.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[str, str]] = []  # (chemin, libellé)
    paths: list[Path] = []
    for index, row in enumerate(rows, start=1):
        try:
            filename = safe_filename(filename_pattern.format_map(row))
        except (KeyError, ValueError, IndexError):
            filename = f"document_{index}.html"
        if not filename.lower().endswith(".html"):
            filename += ".html"
        path = out_dir / filename
        label = _first(row, "cn", "displayName", "sAMAccountName") or filename
        path.write_text(render_template(template, row), encoding="utf-8")
        generated.append((filename, label))
        paths.append(path)

    if make_index:
        items = "\n".join(
            f'<li><a href="{html.escape(file)}">{html.escape(label)}</a></li>'
            for file, label in generated
        )
        index_html = (
            "<!doctype html>\n<html lang=\"fr\">\n<head><meta charset=\"utf-8\">"
            f"<title>Publipostage — {len(generated)} document(s)</title></head>\n"
            f"<body><h1>Publipostage ({len(generated)} document(s))</h1>\n"
            f"<ul>\n{items}\n</ul>\n</body>\n</html>\n"
        )
        (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    return paths


# -- Import GEP / base Éducation nationale -------------------------------------------------

GEP_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "prenom": ("prenom", "prénom", "first name", "firstname", "given name"),
    "nom": ("nom", "lastname", "last name", "surname", "family name", "nom de famille"),
    "classe": ("classe", "class", "division", "groupe", "niveau", "level"),
    "ou": ("ou", "unite", "unité", "organizationalunit", "etablissement", "établissement"),
    "email": ("email", "e-mail", "mail", "adresse mail", "courriel"),
    "date_naissance": (
        "date de naissance", "date_naissance", "ddn", "naissance", "birthdate", "birth date",
    ),
    "numero": ("numero", "numéro", "matricule", "numero etudiant", "num etudiant", "id eleve"),
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


def suggest_gep_mapping(headers: Sequence[str]) -> dict[str, str]:
    """Associe chaque colonne attendue (csv_io.EXPECTED_COLUMNS) à l'en-tête
    réel : correspondance exacte d'abord (accents/casse ignorés), puis
    correspondance par sous-chaîne sur les en-têtes encore libres."""
    normalized = {_strip_accents(str(h).strip()): str(h) for h in headers}
    mapping: dict[str, str] = {field: "" for field in EXPECTED_COLUMNS}

    # 1. exact
    for expected in EXPECTED_COLUMNS:
        for alias in GEP_FIELD_ALIASES.get(expected, ()):
            match = normalized.get(_strip_accents(alias))
            if match:
                mapping[expected] = match
                break

    # 2. sous-chaîne (en-têtes déjà attribués exclus)
    used = {value for value in mapping.values() if value}
    for expected in EXPECTED_COLUMNS:
        if mapping[expected]:
            continue
        for alias in GEP_FIELD_ALIASES.get(expected, ()):
            needle = _strip_accents(alias)
            if len(needle) < 3:
                continue  # motif trop court : « ou » matcherait « cours », « groupe »…
            for key, original in normalized.items():
                if original in used:
                    continue
                if needle and needle in key:
                    mapping[expected] = original
                    used.add(original)
                    break
            if mapping[expected]:
                break
    return mapping


@dataclass
class GEPImportResult:
    rows: list[dict]                    # clés = csv_io.EXPECTED_COLUMNS
    skipped_row_numbers: list[int]      # lignes sans prénom ou nom (1-indexé)
    mapping: dict[str, str]
    delimiter: str


def parse_gep_csv(text: str, mapping: dict[str, str] | None = None) -> GEPImportResult:
    """Parse le CSV libre produit par l'outil tiers GEP (délimiteur détecté,
    colonnes associées automatiquement si ``mapping`` absent)."""
    delimiter = detect_delimiter((text or "")[:4096])
    reader = csv.DictReader((text or "").splitlines(), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    mapping = dict(mapping) if mapping is not None else suggest_gep_mapping(headers)

    rows: list[dict] = []
    skipped: list[int] = []
    for line_number, raw in enumerate(reader, start=1):

        def get(field: str) -> str:
            header = mapping.get(field)
            return (raw.get(header, "") or "").strip() if header else ""

        if not get("prenom") or not get("nom"):
            skipped.append(line_number)
            continue
        rows.append({column: get(column) for column in EXPECTED_COLUMNS})
    return GEPImportResult(
        rows=rows, skipped_row_numbers=skipped, mapping=mapping, delimiter=delimiter
    )


def gep_to_edusync_csv(rows: Sequence[dict], dest: Path) -> Path:
    """Convertit les lignes GEP en CSV au format d'import EduSync
    (``;`` UTF-8 BOM) — directement chargeable par ``csv_io.load_rows``
    avec l'association identité des colonnes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(list(EXPECTED_COLUMNS))
        for row in rows:
            writer.writerow([row.get(column, "") for column in EXPECTED_COLUMNS])
    return dest


# -- Outil tiers (GEP) -------------------------------------------------------------------------

@dataclass
class AdvancedIOConfig:
    """Configuration du module (outil tiers GEP)."""

    gep_tool_path: str = ""

    def to_dict(self) -> dict:
        return {"gep_tool_path": self.gep_tool_path}

    @classmethod
    def from_dict(cls, data: dict) -> "AdvancedIOConfig":
        return cls(gep_tool_path=str(data.get("gep_tool_path", "")))


def load_adv_io_config() -> AdvancedIOConfig:
    import json

    if ADV_IO_CONFIG_FILE.exists():
        try:
            with ADV_IO_CONFIG_FILE.open("r", encoding="utf-8") as fh:
                return AdvancedIOConfig.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError):
            pass
    return AdvancedIOConfig()


def save_adv_io_config(config: AdvancedIOConfig) -> None:
    import json

    ADV_IO_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ADV_IO_CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)


def run_external_tool(config: AdvancedIOConfig) -> str | None:
    """Lance l'outil tiers (GEP) fourni à l'établissement.

    Retourne ``None`` si le lancement est parti, sinon le message d'erreur
    (chemin vide / introuvable / échec du système)."""
    path = (config.gep_tool_path or "").strip()
    if not path:
        return "Aucun outil tiers configuré."
    tool = Path(path)
    if not tool.exists():
        return f"Outil introuvable : {path}"
    try:
        subprocess.Popen([str(tool.resolve())], cwd=str(tool.resolve().parent))
    except OSError as exc:
        return f"Lancement impossible : {exc}"
    return None
