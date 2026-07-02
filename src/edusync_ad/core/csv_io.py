"""Import/export CSV (§4 du cahier des charges).

Import : aperçu des 5 premières lignes, détection automatique du délimiteur
et de l'encodage, association manuelle des colonnes si les en-têtes ne
correspondent pas aux noms attendus.

Export : prénom, nom, identifiant, mot de passe en clair, adresse mail —
destiné à l'impression et à la distribution après création des comptes.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from edusync_ad.core.models import GeneratedUser, RawUserRow

EXPECTED_COLUMNS = ["prenom", "nom", "classe", "ou", "email", "date_naissance", "numero"]
REQUIRED_COLUMNS = ["prenom", "nom"]
ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "latin-1"]


@dataclass
class CsvPreview:
    headers: list[str]
    rows: list[dict[str, str]]
    suggested_mapping: dict[str, str]
    delimiter: str
    encoding: str


@dataclass
class CsvImportResult:
    rows: list[RawUserRow]
    skipped_row_numbers: list[int]  # numéros de ligne (1-indexé, hors en-tête)


def _read_text(path: Path) -> tuple[str, str]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODINGS_TO_TRY:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"Impossible de décoder le fichier CSV : {last_error}")


def detect_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        return dialect.delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def _suggest_mapping(headers: list[str]) -> dict[str, str]:
    normalized = {h.strip().lower(): h for h in headers}
    return {expected: normalized.get(expected, "") for expected in EXPECTED_COLUMNS}


def load_preview(path: Path, *, preview_rows: int = 5) -> CsvPreview:
    text, encoding = _read_text(path)
    delimiter = detect_delimiter(text[:4096])
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    headers = reader.fieldnames or []
    rows: list[dict[str, str]] = []
    for i, row in enumerate(reader):
        if i >= preview_rows:
            break
        rows.append(row)
    return CsvPreview(
        headers=headers,
        rows=rows,
        suggested_mapping=_suggest_mapping(headers),
        delimiter=delimiter,
        encoding=encoding,
    )


def load_rows(
    path: Path,
    mapping: dict[str, str],
    *,
    delimiter: str | None = None,
    encoding: str | None = None,
) -> CsvImportResult:
    """`mapping` associe chaque colonne attendue (prenom, nom, ou, ...) à
    l'en-tête réel du fichier."""
    if encoding is not None:
        text = path.read_text(encoding=encoding)
    else:
        text, encoding = _read_text(path)
    delimiter = delimiter or detect_delimiter(text[:4096])
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)

    rows: list[RawUserRow] = []
    skipped: list[int] = []
    for line_number, raw in enumerate(reader, start=1):

        def get(field: str) -> str:
            header = mapping.get(field)
            return (raw.get(header, "") or "").strip() if header else ""

        prenom, nom = get("prenom"), get("nom")
        if not prenom or not nom:
            skipped.append(line_number)
            continue
        rows.append(
            RawUserRow(
                prenom=prenom,
                nom=nom,
                ou=get("ou"),
                classe=get("classe") or None,
                email=get("email") or None,
                date_naissance=get("date_naissance") or None,
                numero=get("numero") or None,
            )
        )
    return CsvImportResult(rows=rows, skipped_row_numbers=skipped)


def export_created_accounts(path: Path, users: list[GeneratedUser]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["prenom", "nom", "identifiant", "mot_de_passe", "adresse_mail"])
        for user in users:
            writer.writerow(
                [
                    user.source.prenom,
                    user.source.nom,
                    user.identifiant,
                    user.mot_de_passe,
                    user.adresse_mail,
                ]
            )
