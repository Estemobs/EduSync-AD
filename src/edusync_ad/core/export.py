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

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
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


# -- Thèmes de couleur pour les étiquettes ------------------------------------

@dataclass(frozen=True)
class ColorTheme:
    key: str
    nom: str
    fond_hex: str
    texte_hex: str


LABEL_COLOR_THEMES: dict[str, ColorTheme] = {
    "bleu": ColorTheme("bleu", "Bleu", "#DCEBFC", "#1B4F91"),
    "vert": ColorTheme("vert", "Vert", "#E1F5E9", "#1E6B3C"),
    "jaune": ColorTheme("jaune", "Jaune", "#FEF6DC", "#8A6D1D"),
    "corail": ColorTheme("corail", "Corail", "#FDE7E4", "#B23A2E"),
    "gris": ColorTheme("gris", "Gris", "#ECECEC", "#333333"),
}
DEFAULT_COLOR_THEME = "bleu"


def _draw_qr_code(c: canvas.Canvas, value: str, x: float, y: float, size: float) -> None:
    """Dessine un QR code carré de `size` points, coin bas-gauche en (x, y)."""
    widget = qr.QrCodeWidget(value)
    x0, y0, x1, y1 = widget.getBounds()
    width, height = x1 - x0, y1 - y0
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


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


def generate_labels_pdf(
    path: Path,
    users: list[dict],
    fields: list[str],
    format_key: str,
    *,
    color_theme: str = DEFAULT_COLOR_THEME,
    qr_code: bool = False,
) -> None:
    """Génère un PDF d'étiquettes imprimables — une étiquette par utilisateur,
    plusieurs planches si nécessaire, avec les champs sélectionnés empilés
    verticalement, un fond coloré (thème au choix) et, en option, un QR code
    encodant l'identifiant (ex. pour un scan rapide en salle informatique)."""
    fmt = LABEL_FORMATS[format_key]
    theme = LABEL_COLOR_THEMES.get(color_theme, LABEL_COLOR_THEMES[DEFAULT_COLOR_THEME])
    bg_color = HexColor(theme.fond_hex)
    text_color = HexColor(theme.texte_hex)
    inner_margin = 1.5 * mm

    page_w, page_h = A4
    c = canvas.Canvas(str(path), pagesize=A4)

    for index, user in enumerate(users):
        pos_in_page = index % fmt.par_planche
        if index > 0 and pos_in_page == 0:
            c.showPage()
        row, col = divmod(pos_in_page, fmt.colonnes)

        x_left = fmt.marge_gauche_mm * mm + col * fmt.pas_horizontal_mm * mm
        y_top = page_h - fmt.marge_haut_mm * mm - row * fmt.pas_vertical_mm * mm
        label_w = fmt.largeur_mm * mm
        label_h = fmt.hauteur_mm * mm
        y_bottom = y_top - label_h

        lines = [str(user.get(f, "")) for f in fields if user.get(f)]
        if not lines:
            continue

        c.saveState()
        c.setFillColor(bg_color)
        c.roundRect(
            x_left + inner_margin, y_bottom + inner_margin,
            label_w - 2 * inner_margin, label_h - 2 * inner_margin,
            2 * mm, fill=1, stroke=0,
        )
        c.restoreState()

        identifiant = user.get("identifiant", "")
        show_qr = qr_code and bool(identifiant)
        # Le QR occupe au plus ~1/3 de la largeur, sans jamais déborder en
        # hauteur — le texte se recale à gauche pour lui laisser la place.
        qr_size = min(label_h - 4 * mm, label_w * 0.32) if show_qr else 0
        text_right_edge = x_left + label_w - inner_margin - 2 * mm - (qr_size + 2 * mm if show_qr else 0)
        text_left_edge = x_left + inner_margin + 2 * mm
        text_x_center = (text_left_edge + text_right_edge) / 2

        font_size = 10 if len(lines) <= 2 else (8 if len(lines) <= 4 else 6.5)
        line_height = font_size * 1.35
        block_height = line_height * len(lines)
        y_start = y_top - (label_h - block_height) / 2 - font_size

        c.setFillColor(text_color)
        for i, line in enumerate(lines):
            y = y_start - i * line_height
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", font_size)
            c.drawCentredString(text_x_center, y, line)

        if show_qr:
            qr_x = x_left + label_w - inner_margin - qr_size
            qr_y = y_bottom + (label_h - qr_size) / 2
            _draw_qr_code(c, identifiant, qr_x, qr_y, qr_size)

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
