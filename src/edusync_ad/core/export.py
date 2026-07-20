"""Export d'un lot de comptes en CSV ou en étiquettes PDF imprimables.

Formats d'étiquettes : dimensions et positionnement (marges, pas horizontal/
vertical) vérifiés depuis les gabarits officiels Avery L7160 et L7163 — les
deux formats de planches A4 les plus courants, en vente dans n'importe quelle
papeterie ou grande surface en France (rayon "étiquettes autocollantes A4").
Positions données en points par les gabarits sources, converties ici en mm.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


@dataclass(frozen=True)
class LabelFormat:
    key: str
    nom: str
    largeur_mm: float
    hauteur_mm: float
    colonnes: int
    lignes: int
    marge_gauche_mm: float
    marge_haut_mm: float
    pas_horizontal_mm: float
    pas_vertical_mm: float

    @property
    def par_planche(self) -> int:
        return self.colonnes * self.lignes


LABEL_FORMATS: dict[str, LabelFormat] = {
    "avery_l7160": LabelFormat(
        key="avery_l7160",
        nom="Avery L7160 — 21 étiquettes/planche (63,5 × 38,1 mm)",
        largeur_mm=63.5, hauteur_mm=38.1,
        colonnes=3, lignes=7,
        marge_gauche_mm=7.5, marge_haut_mm=15.5,
        pas_horizontal_mm=66.0, pas_vertical_mm=38.1,
    ),
    "avery_l7163": LabelFormat(
        key="avery_l7163",
        nom="Avery L7163 — 14 étiquettes/planche, format large (99,1 × 38,1 mm)",
        largeur_mm=99.1, hauteur_mm=38.1,
        colonnes=2, lignes=7,
        marge_gauche_mm=3.5, marge_haut_mm=15.2,
        pas_horizontal_mm=103.0, pas_vertical_mm=38.1,
    ),
}


# -- Champs disponibles pour l'export (CSV et étiquettes) --------------------

EXPORT_FIELDS: dict[str, str] = {
    "identifiant": "Identifiant",
    "nom_complet": "Nom complet",
    "prenom": "Prénom",
    "nom": "Nom",
    "classe": "Classe / OU",
    "mail": "Adresse mail",
    "etat": "État (actif/désactivé)",
}


def export_users_csv(path: Path, users: list[dict], fields: list[str]) -> None:
    """Exporte une liste d'utilisateurs (dicts avec les clés EXPORT_FIELDS)
    en CSV — même convention que le reste de l'appli (';' , utf-8-sig)."""
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([EXPORT_FIELDS[key] for key in fields])
        for user in users:
            writer.writerow([user.get(key, "") for key in fields])


def generate_labels_pdf(path: Path, users: list[dict], fields: list[str], format_key: str) -> None:
    """Génère un PDF d'étiquettes imprimables — une étiquette par utilisateur,
    plusieurs planches si nécessaire, avec les champs sélectionnés empilés
    verticalement et centrés sur chaque étiquette."""
    fmt = LABEL_FORMATS[format_key]
    page_w, page_h = A4
    c = canvas.Canvas(str(path), pagesize=A4)

    for index, user in enumerate(users):
        pos_in_page = index % fmt.par_planche
        if index > 0 and pos_in_page == 0:
            c.showPage()
        row, col = divmod(pos_in_page, fmt.colonnes)

        x_left = fmt.marge_gauche_mm * mm + col * fmt.pas_horizontal_mm * mm
        y_top = page_h - fmt.marge_haut_mm * mm - row * fmt.pas_vertical_mm * mm
        x_center = x_left + (fmt.largeur_mm * mm) / 2
        label_h = fmt.hauteur_mm * mm

        lines = [str(user.get(f, "")) for f in fields if user.get(f)]
        if not lines:
            continue

        font_size = 10 if len(lines) <= 2 else 8
        line_height = font_size * 1.35
        block_height = line_height * len(lines)
        y_start = y_top - (label_h - block_height) / 2 - font_size

        c.setFont("Helvetica", font_size)
        for i, line in enumerate(lines):
            y = y_start - i * line_height
            c.drawCentredString(x_center, y, line)

    c.save()


def build_export_row(attrs: dict) -> dict:
    """Construit une ligne d'export à partir des attributs utilisateur bruts
    (dn, sam, cn, givenName, sn, mail, disabled…) — dérive la classe/OU
    depuis le DN, comme le fait déjà le reste de l'appli."""
    dn = attrs.get("dn", "")
    parent = dn.split(",", 1)[1] if "," in dn else ""
    leaf = parent.split(",")[0] if parent else ""
    classe = leaf.split("=", 1)[-1] if "=" in leaf else leaf
    return {
        "identifiant": attrs.get("sam") or attrs.get("sAMAccountName", ""),
        "nom_complet": attrs.get("cn", ""),
        "prenom": attrs.get("givenName", ""),
        "nom": attrs.get("sn", ""),
        "classe": classe,
        "mail": attrs.get("mail", ""),
        "etat": "Désactivé" if attrs.get("disabled") else "Actif",
    }
