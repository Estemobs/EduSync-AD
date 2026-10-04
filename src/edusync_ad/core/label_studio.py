"""M24 — Étiquettes & trombinoscopes avancés.

Quatre apports par rapport au module d'export v1 (``core/export.py``) :

1. **Éditeur de modèles d'étiquettes** — positionnement libre des champs (en
   mm depuis le coin haut-gauche de l'étiquette), police, corps, couleur,
   alignement, QR code et photo : chaque modèle est un document JSON
   réutilisable, modifiable puis ré-enregistré.
2. **Modèles pré-enregistrés** — Avery L7160 (21 étiquettes) et L7163
   (14 étiquettes), dont la géométrie est **reprise de** ``core/export.py``
   pour rester strictement identique à l'impression, plus un badge
   85 × 55 mm et une carte de visite 85 × 54 mm.
3. **Trombinoscope** — grille photo + nom sur A4 ou A3, export PDF,
   photo AD (``jpegPhoto``/``thumbnailPhoto``) ou emplacement réservé.
4. **Envoi d'une étiquette par mail** — génération PDF d'un destinataire
   unique puis envoi SMTP, mot de passe stocké chiffré AES-256
   (``core/crypto.py``), jamais en clair sur disque.

Toutes les dimensions sont en millimètres (l'utilisateur) et converties en
points reportlab au moment de l'impression — ``1 mm = mm`` points.
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr as qr_codes
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from edusync_ad.core.config import config_dir
from edusync_ad.core.export import (
    DEFAULT_COLOR_THEME,
    EXPORT_FIELDS,
    LABEL_COLOR_THEMES,
    LABEL_FORMATS,
    LabelFormat,
)

# Configuration SMTP extraite dans core/mailer.py (M27) pour être partagée
# avec le portail auto-service — réexportée ici pour rester compatible avec
# les appels M24 existants.
from edusync_ad.core.mailer import (  # noqa: F401
    MAIL_CONFIG_FILE,
    MailConfig,
    MailError,
    build_message,
    load_mail_config,
    save_mail_config,
    send_message,
)

LABEL_TEMPLATES_FILE = config_dir() / "label_templates.json"

EPS_MM = 0.1  # tolérance d'arrondi pour les contrôles de débordement
MIN_FONT_PT = 4.0

PAGE_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# -- Planches (format de page + grille d'étiquettes) -------------------------------

@dataclass(frozen=True)
class SheetSpec:
    """Une planche imprimable : page + grille d'étiquettes."""

    key: str
    nom: str
    page: str                 # "A4" | "A3"
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

    @property
    def page_width_mm(self) -> float:
        return PAGE_SIZES_MM[self.page][0]

    @property
    def page_height_mm(self) -> float:
        return PAGE_SIZES_MM[self.page][1]

    @property
    def label_size_mm(self) -> tuple[float, float]:
        return (self.largeur_mm, self.hauteur_mm)

    def label_origin_mm(self, row: int, col: int) -> tuple[float, float]:
        """Coin haut-gauche d'une étiquette, en mm depuis le coin haut-gauche de la page."""
        return (
            self.marge_gauche_mm + col * self.pas_horizontal_mm,
            self.marge_haut_mm + row * self.pas_vertical_mm,
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.page not in PAGE_SIZES_MM:
            errors.append(f"Planche « {self.key} » : page « {self.page} » inconnue (A4 ou A3).")
            return errors
        if self.colonnes < 1 or self.lignes < 1:
            errors.append(f"Planche « {self.key} » : la grille doit avoir au moins 1 colonne et 1 ligne.")
        if self.largeur_mm <= 0 or self.hauteur_mm <= 0:
            errors.append(f"Planche « {self.key} » : dimensions d'étiquette invalides.")
        if self.marge_gauche_mm < 0 or self.marge_haut_mm < 0:
            errors.append(f"Planche « {self.key} » : marges négatives.")
        if self.pas_horizontal_mm + EPS_MM < self.largeur_mm:
            errors.append(f"Planche « {self.key} » : pas horizontal < largeur d'étiquette (chevauchement).")
        if self.pas_vertical_mm + EPS_MM < self.hauteur_mm:
            errors.append(f"Planche « {self.key} » : pas vertical < hauteur d'étiquette (chevauchement).")
        used_w = self.marge_gauche_mm + (self.colonnes - 1) * self.pas_horizontal_mm + self.largeur_mm
        if used_w > self.page_width_mm + EPS_MM:
            errors.append(
                f"Planche « {self.key} » : déborde en largeur ({used_w:.1f} mm > {self.page_width_mm:.0f} mm)."
            )
        used_h = self.marge_haut_mm + (self.lignes - 1) * self.pas_vertical_mm + self.hauteur_mm
        if used_h > self.page_height_mm + EPS_MM:
            errors.append(
                f"Planche « {self.key} » : déborde en hauteur ({used_h:.1f} mm > {self.page_height_mm:.0f} mm)."
            )
        return errors


def _sheet_from_label_format(fmt: LabelFormat) -> SheetSpec:
    """Reprend la géométrie officielle d'un format Avery (source : core/export.py)."""
    return SheetSpec(
        key=fmt.key,
        nom=fmt.nom,
        page="A4",
        largeur_mm=fmt.largeur_mm,
        hauteur_mm=fmt.hauteur_mm,
        colonnes=fmt.colonnes,
        lignes=fmt.lignes,
        marge_gauche_mm=fmt.marge_gauche_mm,
        marge_haut_mm=fmt.marge_haut_mm,
        pas_horizontal_mm=fmt.pas_horizontal_mm,
        pas_vertical_mm=fmt.pas_vertical_mm,
    )


SHEETS: dict[str, SheetSpec] = {
    **{key: _sheet_from_label_format(fmt) for key, fmt in LABEL_FORMATS.items()},
    "badge_85x55": SheetSpec(
        key="badge_85x55",
        nom="Badge porte-badge — 85 × 55 mm (8 par planche)",
        page="A4",
        largeur_mm=85.0, hauteur_mm=55.0,
        colonnes=2, lignes=4,
        marge_gauche_mm=10.0, marge_haut_mm=15.0,
        pas_horizontal_mm=92.0, pas_vertical_mm=62.0,
    ),
    "carte_85x54": SheetSpec(
        key="carte_85x54",
        nom="Carte de visite — 85 × 54 mm (8 par planche)",
        page="A4",
        largeur_mm=85.0, hauteur_mm=54.0,
        colonnes=2, lignes=4,
        marge_gauche_mm=20.0, marge_haut_mm=20.0,
        pas_horizontal_mm=90.0, pas_vertical_mm=65.0,
    ),
}


# -- Éléments d'une étiquette -------------------------------------------------------

ELEMENT_KINDS: dict[str, str] = {
    "texte": "Champ (variable par compte)",
    "statique": "Texte fixe (logo, établissement…)",
    "photo": "Photo d'identité",
    "qr": "QR code",
}

ALIGNMENTS: dict[str, str] = {"gauche": "left", "centre": "center", "droite": "right"}

FONTS: list[str] = [
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Times-Roman",
    "Times-Bold",
    "Courier",
]


@dataclass
class LabelElement:
    """Un élément positionné dans l'étiquette (coordonnées en mm, origine haut-gauche)."""

    kind: str = "texte"
    field: str = "nom_complet"   # clé EXPORT_FIELDS pour "texte" et "qr"
    text: str = ""               # valeur pour "statique"
    x_mm: float = 3.0
    y_mm: float = 3.0
    w_mm: float = 40.0
    h_mm: float = 9.0
    font: str = "Helvetica-Bold"
    size_pt: float = 11.0
    color_hex: str = "#1B4F91"
    align: str = "centre"

    def validate(self, label_size: tuple[float, float] | None = None) -> list[str]:
        errors: list[str] = []
        if self.kind not in ELEMENT_KINDS:
            errors.append(f"Élément « {self.field or self.text} » : type « {self.kind} » inconnu.")
        if self.kind in ("texte", "qr") and self.field not in EXPORT_FIELDS:
            errors.append(f"Élément « {self.field} » : champ inconnu ({', '.join(EXPORT_FIELDS)}).")
        if self.kind == "statique" and not self.text.strip():
            errors.append("Texte fixe vide.")
        if self.kind in ("texte", "statique") and self.font not in FONTS:
            errors.append(f"Police « {self.font} » inconnue.")
        if self.align not in ALIGNMENTS:
            errors.append(f"Alignement « {self.align} » invalide (gauche/centre/droite).")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color_hex or ""):
            errors.append(f"Couleur « {self.color_hex} » invalide (format #RRGGBB).")
        if self.size_pt <= 0:
            errors.append("Corps de police positif requis.")
        if self.w_mm <= 0 or self.h_mm <= 0:
            errors.append("Largeur et hauteur strictement positives requises.")
        if self.x_mm < -EPS_MM or self.y_mm < -EPS_MM:
            errors.append("Position négative hors de l'étiquette.")
        if label_size is not None:
            label_w, label_h = label_size
            if self.x_mm + self.w_mm > label_w + EPS_MM:
                errors.append(f"Élément « {self.name} » : déborde à droite de l'étiquette.")
            if self.y_mm + self.h_mm > label_h + EPS_MM:
                errors.append(f"Élément « {self.name} » : déborde en bas de l'étiquette.")
        return errors

    @property
    def name(self) -> str:
        """Libellé court utilisé dans les messages de validation et l'UI."""
        if self.kind == "statique":
            return self.text.strip() or "texte fixe"
        if self.kind == "photo":
            return "photo"
        if self.kind == "qr":
            return f"QR {self.field}"
        return self.field

    def value(self, user: dict) -> str:
        """Valeur affichée pour ce compte (vide si absente → l'élément est ignoré)."""
        if self.kind == "statique":
            return self.text
        return str(user.get(self.field, "") or "")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "field": self.field,
            "text": self.text,
            "x": round(self.x_mm, 2),
            "y": round(self.y_mm, 2),
            "w": round(self.w_mm, 2),
            "h": round(self.h_mm, 2),
            "font": self.font,
            "size": round(self.size_pt, 1),
            "color": self.color_hex,
            "align": self.align,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LabelElement":
        return cls(
            kind=str(data.get("kind", "texte")),
            field=str(data.get("field", "nom_complet")),
            text=str(data.get("text", "")),
            x_mm=float(data.get("x", 3.0)),
            y_mm=float(data.get("y", 3.0)),
            w_mm=float(data.get("w", 40.0)),
            h_mm=float(data.get("h", 9.0)),
            font=str(data.get("font", "Helvetica-Bold")),
            size_pt=float(data.get("size", 11.0)),
            color_hex=str(data.get("color", "#1B4F91")),
            align=str(data.get("align", "centre")),
        )


# -- Modèles ------------------------------------------------------------------------

@dataclass
class LabelTemplate:
    """Un modèle d'étiquette : planche + décor + éléments positionnés."""

    id: str
    name: str
    sheet: str = "avery_l7160"
    background_hex: str = "#FFFFFF"
    border_hex: str = ""
    elements: list[LabelElement] = field(default_factory=list)
    builtin: bool = False

    @property
    def spec(self) -> SheetSpec | None:
        return SHEETS.get(self.sheet)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not _ID_RE.fullmatch(self.id or ""):
            errors.append(f"Identifiant « {self.id} » invalide (minuscules, chiffres, « - » et « _ »).")
        if not (self.name or "").strip():
            errors.append("Nom du modèle vide.")
        spec = self.spec
        if spec is None:
            errors.append(f"Planche « {self.sheet} » inconnue.")
            return errors
        errors.extend(spec.validate())
        for key, value in (("fond", self.background_hex), ("bordure", self.border_hex)):
            if value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                errors.append(f"Couleur de {key} « {value} » invalide (format #RRGGBB).")
        if not self.elements:
            errors.append("Aucun élément : le modèle doit contenir au moins un champ.")
        for element in self.elements:
            errors.extend(element.validate(spec.label_size_mm))
        return errors

    def clone(self) -> "LabelTemplate":
        return replace(self, elements=[replace(e) for e in self.elements], builtin=False)

    def element_index_at(self, x_mm: float, y_mm: float) -> int | None:
        """Dernier élément contenant le point (hit-test, sens de z à l'écran)."""
        found = None
        for index, element in enumerate(self.elements):
            if (
                element.x_mm <= x_mm <= element.x_mm + element.w_mm
                and element.y_mm <= y_mm <= element.y_mm + element.h_mm
            ):
                found = index
        return found

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sheet": self.sheet,
            "background": self.background_hex,
            "border": self.border_hex,
            "builtin": self.builtin,
            "elements": [element.to_dict() for element in self.elements],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LabelTemplate":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            sheet=str(data.get("sheet", "avery_l7160")),
            background_hex=str(data.get("background", "#FFFFFF")),
            border_hex=str(data.get("border", "")),
            builtin=bool(data.get("builtin", False)),
            elements=[LabelElement.from_dict(item) for item in data.get("elements", []) or []],
        )


# -- Modèles pré-enregistrés ----------------------------------------------------------

def builtin_templates() -> list[LabelTemplate]:
    """Les 4 modèles livrés avec l'application (tous valides)."""
    theme = LABEL_COLOR_THEMES[DEFAULT_COLOR_THEME]
    return [
        LabelTemplate(
            id="etiquette_classe",
            name="Étiquette de classe — Avery L7160",
            sheet="avery_l7160",
            background_hex=theme.fond_hex,
            border_hex=theme.texte_hex,
            builtin=True,
            elements=[
                LabelElement(kind="texte", field="nom_complet", x_mm=3, y_mm=5, w_mm=44, h_mm=9,
                             font="Helvetica-Bold", size_pt=11, color_hex=theme.texte_hex),
                LabelElement(kind="texte", field="identifiant", x_mm=3, y_mm=16, w_mm=44, h_mm=7,
                             font="Helvetica", size_pt=9, color_hex=theme.texte_hex),
                LabelElement(kind="texte", field="classe", x_mm=3, y_mm=25, w_mm=44, h_mm=7,
                             font="Helvetica", size_pt=8, color_hex="#4A6FA5"),
                LabelElement(kind="qr", field="identifiant", x_mm=49, y_mm=12, w_mm=12, h_mm=12,
                             color_hex=theme.texte_hex),
            ],
        ),
        LabelTemplate(
            id="etiquette_large_photo",
            name="Étiquette large + photo — Avery L7163",
            sheet="avery_l7163",
            background_hex=LABEL_COLOR_THEMES["vert"].fond_hex,
            border_hex=LABEL_COLOR_THEMES["vert"].texte_hex,
            builtin=True,
            elements=[
                LabelElement(kind="photo", x_mm=4, y_mm=5, w_mm=20, h_mm=26),
                LabelElement(kind="texte", field="nom_complet", x_mm=27, y_mm=8, w_mm=45, h_mm=10,
                             font="Helvetica-Bold", size_pt=13, color_hex=LABEL_COLOR_THEMES["vert"].texte_hex,
                             align="gauche"),
                LabelElement(kind="texte", field="identifiant", x_mm=27, y_mm=19, w_mm=45, h_mm=7,
                             font="Helvetica", size_pt=9, color_hex=LABEL_COLOR_THEMES["vert"].texte_hex,
                             align="gauche"),
                LabelElement(kind="texte", field="classe", x_mm=27, y_mm=27, w_mm=45, h_mm=7,
                             font="Helvetica", size_pt=8, color_hex="#4A6FA5", align="gauche"),
                LabelElement(kind="qr", field="identifiant", x_mm=76, y_mm=11, w_mm=14, h_mm=14,
                             color_hex=LABEL_COLOR_THEMES["vert"].texte_hex),
            ],
        ),
        LabelTemplate(
            id="badge_porteur",
            name="Badge porte-badge — 85 × 55 mm",
            sheet="badge_85x55",
            background_hex="#FFFFFF",
            border_hex="#1B4F91",
            builtin=True,
            elements=[
                LabelElement(kind="statique", text="ÉTABLISSEMENT", x_mm=38, y_mm=3, w_mm=42, h_mm=6,
                             font="Helvetica-Bold", size_pt=8, color_hex="#4A6FA5", align="gauche"),
                LabelElement(kind="photo", x_mm=5, y_mm=8, w_mm=30, h_mm=40),
                LabelElement(kind="texte", field="nom_complet", x_mm=38, y_mm=12, w_mm=42, h_mm=10,
                             font="Helvetica-Bold", size_pt=12, color_hex="#1B4F91", align="gauche"),
                LabelElement(kind="texte", field="classe", x_mm=38, y_mm=24, w_mm=42, h_mm=8,
                             font="Helvetica", size_pt=10, color_hex="#333333", align="gauche"),
                LabelElement(kind="texte", field="identifiant", x_mm=38, y_mm=34, w_mm=42, h_mm=7,
                             font="Helvetica", size_pt=9, color_hex="#333333", align="gauche"),
                LabelElement(kind="qr", field="identifiant", x_mm=38, y_mm=41, w_mm=12, h_mm=12,
                             color_hex="#1B4F91"),
            ],
        ),
        LabelTemplate(
            id="carte_visite",
            name="Carte de visite — 85 × 54 mm",
            sheet="carte_85x54",
            background_hex="#FFFFFF",
            border_hex="#ECECEC",
            builtin=True,
            elements=[
                LabelElement(kind="statique", text="NOM DE L'ÉTABLISSEMENT", x_mm=5, y_mm=4, w_mm=60, h_mm=7,
                             font="Times-Bold", size_pt=9, color_hex="#1B4F91", align="gauche"),
                LabelElement(kind="texte", field="nom_complet", x_mm=5, y_mm=13, w_mm=60, h_mm=9,
                             font="Helvetica-Bold", size_pt=12, color_hex="#333333", align="gauche"),
                LabelElement(kind="texte", field="identifiant", x_mm=5, y_mm=24, w_mm=55, h_mm=6,
                             font="Helvetica", size_pt=8.5, color_hex="#333333", align="gauche"),
                LabelElement(kind="texte", field="mail", x_mm=5, y_mm=32, w_mm=55, h_mm=6,
                             font="Helvetica", size_pt=8.5, color_hex="#333333", align="gauche"),
                LabelElement(kind="texte", field="classe", x_mm=5, y_mm=40, w_mm=55, h_mm=6,
                             font="Helvetica", size_pt=8, color_hex="#4A6FA5", align="gauche"),
                LabelElement(kind="qr", field="identifiant", x_mm=65, y_mm=18, w_mm=15, h_mm=15,
                             color_hex="#1B4F91"),
            ],
        ),
    ]


def load_templates(path: Path | None = None) -> list[LabelTemplate]:
    """Modèles intégrés + personnalisés enregistrés (fichier absent → intégrés seuls)."""
    dest = path if path is not None else LABEL_TEMPLATES_FILE
    if dest.exists():
        try:
            with dest.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get("modeles", data) if isinstance(data, dict) else data
            templates = [LabelTemplate.from_dict(item) for item in items]
            return [t for t in templates if t.id.strip() and t.name.strip()]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return builtin_templates()


def save_templates(templates: list[LabelTemplate], path: Path | None = None) -> Path:
    dest = path if path is not None else LABEL_TEMPLATES_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump({"modeles": [t.to_dict() for t in templates]}, fh, indent=2, ensure_ascii=False)
    return dest


def unique_template_id(templates: list[LabelTemplate], base: str) -> str:
    """Identifiant libre pour une duplication (base, base-2, base-3…)."""
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", (base or "").strip().lower())
        if not unicodedata.combining(char)
    )
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", folded).strip("-") or "modele"
    if not _ID_RE.fullmatch(cleaned):
        cleaned = "modele"
    existing = {template.id for template in templates}
    candidate = cleaned
    counter = 2
    while candidate in existing:
        candidate = f"{cleaned}-{counter}"
        counter += 1
    return candidate


def duplicate_template(
    templates: list[LabelTemplate], template: LabelTemplate, new_name: str = ""
) -> LabelTemplate:
    """Copie profonde avec nouvel id/nom — la liste n'est pas modifiée."""
    clone = template.clone()
    clone.id = unique_template_id(templates, template.id)
    clone.name = new_name.strip() or f"{template.name} (copie)"
    return clone


def export_template(template: LabelTemplate, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(template.to_dict(), fh, indent=2, ensure_ascii=False)
    return dest


def import_template(path: Path) -> LabelTemplate:
    """Import JSON d'un modèle (échoue si invalide)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Lecture du modèle impossible : {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Format de modèle invalide (objet JSON attendu).")
    template = LabelTemplate.from_dict(data)
    errors = template.validate()
    if errors:
        raise ValueError("Modèle invalide : " + "; ".join(errors))
    return template


# -- Rendu PDF --------------------------------------------------------------------------

def _draw_qr(c: canvas.Canvas, value: str, x: float, y: float, size: float, color_hex: str = "") -> None:
    """QR code carré de `size` points, coin bas-gauche en (x, y)."""
    widget = qr_codes.QrCodeWidget(value)
    if color_hex:
        try:
            widget.fillColor = HexColor(color_hex)  # type: ignore[attr-defined]
        except Exception:
            pass
    x0, y0, x1, y1 = widget.getBounds()
    width, height = x1 - x0, y1 - y0
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _image_reader(data: bytes) -> ImageReader | None:
    """ImageReader reportlab depuis des octets JPEG/PNG, ou None si illisible."""
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return ImageReader(image)
    except Exception:
        return None


def _draw_photo(
    c: canvas.Canvas, data: bytes, x: float, y: float, w: float, h: float, placeholder_hex: str
) -> bool:
    """Photo ajustée à la boîte (ratio préservé, centrée) ; emplacement réservé sinon.

    Retourne False si les octets sont absents ou illisibles (placeholder dessiné)."""
    reader = _image_reader(data)
    if reader is None:
        c.setFillColor(HexColor(placeholder_hex))
        c.roundRect(x, y, w, h, 3, fill=1, stroke=0)
        return False
    image_w, image_h = reader.getSize()
    if image_w <= 0 or image_h <= 0:
        return False
    scale = min(w / image_w, h / image_h)
    draw_w, draw_h = image_w * scale, image_h * scale
    c.drawImage(
        reader,
        x + (w - draw_w) / 2,
        y + (h - draw_h) / 2,
        draw_w,
        draw_h,
        mask="auto",
    )
    return True


def _draw_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y_top: float,
    w: float,
    h: float,
    font: str,
    size_pt: float,
    color_hex: str,
    align: str,
) -> None:
    """Texte une ligne, centré verticalement dans la boîte, réduit/tronqué pour tenir."""
    size = size_pt
    while size > MIN_FONT_PT and pdfmetrics.stringWidth(text, font, size) > w:
        size -= 0.5
    if pdfmetrics.stringWidth(text, font, size) > w:
        while text and pdfmetrics.stringWidth(text + "…", font, size) > w:
            text = text[:-1]
        text = text + "…" if text else ""
    baseline = y_top - h / 2 - size * 0.36
    c.setFillColor(HexColor(color_hex))
    c.setFont(font, size)
    if align == "gauche":
        c.drawString(x, baseline, text)
    elif align == "droite":
        c.drawRightString(x + w, baseline, text)
    else:
        c.drawCentredString(x + w / 2, baseline, text)


def render_labels_pdf(
    path: Path,
    users: list[dict],
    template: LabelTemplate,
    *,
    photos: dict[str, bytes] | None = None,
) -> int:
    """Génère la planche d'étiquettes PDF pour `users` — retourne le nb de pages.

    `photos` est indexé par identifiant (``sAMAccountName``) : photo AD
    (`jpegPhoto`/`thumbnailPhoto`) ou fichier traité par ``core/photos.py``.
    """
    errors = template.validate()
    if errors:
        raise ValueError("Modèle d'étiquette invalide : " + "; ".join(errors))
    spec = template.spec
    assert spec is not None  # validate() a déjà vérifié
    photos = photos or {}

    page_w_pt = spec.page_width_mm * mm
    page_h_pt = spec.page_height_mm * mm
    c = canvas.Canvas(str(path), pagesize=(page_w_pt, page_h_pt))
    pages = 0

    for index, user in enumerate(users):
        pos_in_page = index % spec.par_planche
        if pos_in_page == 0 and index > 0:
            c.showPage()
            pages += 1
        row, col = divmod(pos_in_page, spec.colonnes)

        label_x_mm, label_y_mm = spec.label_origin_mm(row, col)
        label_x = label_x_mm * mm
        label_top = page_h_pt - label_y_mm * mm
        label_w = spec.largeur_mm * mm
        label_h = spec.hauteur_mm * mm

        # Fond + bordure de l'étiquette
        c.setFillColor(HexColor(template.background_hex))
        c.rect(label_x, label_top - label_h, label_w, label_h, fill=1, stroke=0)
        if template.border_hex:
            c.setStrokeColor(HexColor(template.border_hex))
            c.setLineWidth(0.6)
            c.rect(label_x + 0.5, label_top - label_h + 0.5, label_w - 1, label_h - 1, fill=0, stroke=1)

        for element in template.elements:
            value = element.value(user)
            elem_x = label_x + element.x_mm * mm
            elem_top = label_top - element.y_mm * mm
            elem_w = element.w_mm * mm
            elem_h = element.h_mm * mm
            elem_y = elem_top - elem_h

            if element.kind == "photo":
                photo = photos.get(str(user.get("identifiant", "")).lower()) or user.get("photo") or b""
                _draw_photo(c, photo, elem_x, elem_y, elem_w, elem_h, placeholder_hex="#E4E4E4")
            elif element.kind == "qr":
                if not value:
                    continue
                size = min(elem_w, elem_h)
                _draw_qr(c, value, elem_x, elem_y, size, element.color_hex)
            elif value:
                _draw_text(
                    c, value, elem_x, elem_top, elem_w, elem_h,
                    element.font, element.size_pt, element.color_hex, element.align,
                )

    c.showPage()
    pages += 1
    c.save()
    return pages


# -- Trombinoscope -----------------------------------------------------------------------

TROMBINOSCOPIC_PAGES: list[str] = ["A4", "A3"]
DEFAULT_TROMBI_COLUMNS = 4


def generate_trombinoscope_pdf(
    path: Path,
    entries: list[dict],
    *,
    page: str = "A4",
    columns: int = DEFAULT_TROMBI_COLUMNS,
    title: str = "",
    subtitle: str = "",
    photos: dict[str, bytes] | None = None,
    accent_hex: str | None = None,
) -> int:
    """Grille photo + nom exportée en PDF (A4 ou A3) — retourne le nb de pages.

    `entries` : lignes d'export (``build_export_row``) avec au minimum
    ``nom_complet``. Les comptes sans photo reçoivent un emplacement réservé
    avec leurs initiales — la grille reste imprimable même à moitié vide.
    """
    if page not in PAGE_SIZES_MM:
        raise ValueError(f"Format de page « {page} » inconnu (A4 ou A3).")
    columns = max(1, min(int(columns), 8))
    if not entries:
        raise ValueError("Aucun compte à mettre en page.")

    accent = accent_hex or LABEL_COLOR_THEMES[DEFAULT_COLOR_THEME].texte_hex
    photos = photos or {}
    page_w_mm, page_h_mm = PAGE_SIZES_MM[page]
    margin_mm = 12.0
    gap_mm = 6.0

    cell_w_mm = (page_w_mm - 2 * margin_mm - (columns - 1) * gap_mm) / columns
    photo_w_mm = cell_w_mm - 6.0
    photo_h_mm = photo_w_mm * 4 / 3
    name_h_mm, class_h_mm, pad_mm = 7.0, 5.5, 3.0
    cell_h_mm = pad_mm * 2 + photo_h_mm + name_h_mm + class_h_mm

    if title and subtitle:
        header_h_mm = 16.0
    elif title or subtitle:
        header_h_mm = 10.0
    else:
        header_h_mm = 4.0
    rows = int((page_h_mm - 2 * margin_mm - header_h_mm) // (cell_h_mm + gap_mm))
    if rows < 1:
        raise ValueError("Trombinoscope : la page est trop petite pour cette mise en page.")

    page_w_pt, page_h_pt = page_w_mm * mm, page_h_mm * mm
    c = canvas.Canvas(str(path), pagesize=(page_w_pt, page_h_pt))
    pages = 0

    for index, entry in enumerate(entries):
        pos = index % (rows * columns)
        if pos == 0:
            if index > 0:
                c.showPage()
                pages += 1
            _draw_trombi_header(c, title, subtitle, page_w_mm, page_h_mm, margin_mm, header_h_mm, accent)
        row, col = divmod(pos, columns)

        x_mm = margin_mm + col * (cell_w_mm + gap_mm)
        y_top_mm = margin_mm + header_h_mm + row * (cell_h_mm + gap_mm)
        _draw_trombi_cell(
            c, entry,
            x_mm=x_mm, y_top_mm=y_top_mm, cell_w_mm=cell_w_mm, cell_h_mm=cell_h_mm,
            photo_w_mm=photo_w_mm, photo_h_mm=photo_h_mm, pad_mm=pad_mm,
            name_h_mm=name_h_mm, class_h_mm=class_h_mm,
            page_h_mm=page_h_mm, photos=photos, accent=accent,
        )

    c.showPage()
    pages += 1
    c.save()
    return pages


def _draw_trombi_header(
    c: canvas.Canvas, title: str, subtitle: str,
    page_w_mm: float, page_h_mm: float, margin_mm: float, header_h_mm: float, accent: str,
) -> None:
    if not title and not subtitle:
        return
    top_y = page_h_mm * mm - margin_mm * mm
    width_pt = (page_w_mm - 2 * margin_mm) * mm
    cursor_mm = 0.0
    if title:
        _draw_text(c, title, margin_mm * mm, top_y, width_pt, 9 * mm,
                   "Helvetica-Bold", 16, accent, "gauche")
        cursor_mm = 9.0
    if subtitle:
        _draw_text(c, subtitle, margin_mm * mm, top_y - cursor_mm * mm, width_pt, 5 * mm,
                   "Helvetica", 9, "#666666", "gauche")
    # Filet sous le bloc titre, 1 mm au-dessus de la première rangée.
    c.setStrokeColor(HexColor(accent))
    c.setLineWidth(0.8)
    y = top_y - (header_h_mm - 1) * mm
    c.line(margin_mm * mm, y, (page_w_mm - margin_mm) * mm, y)


def _draw_trombi_cell(
    c: canvas.Canvas, entry: dict, *,
    x_mm: float, y_top_mm: float, cell_w_mm: float, cell_h_mm: float,
    photo_w_mm: float, photo_h_mm: float, pad_mm: float,
    name_h_mm: float, class_h_mm: float, page_h_mm: float,
    photos: dict[str, bytes], accent: str,
) -> None:
    x = x_mm * mm
    cell_top = (page_h_mm - y_top_mm) * mm
    cell_h = cell_h_mm * mm
    cell_w = cell_w_mm * mm

    c.setFillColor(HexColor("#FFFFFF"))
    c.setStrokeColor(HexColor("#D9D9D9"))
    c.setLineWidth(0.5)
    c.roundRect(x, cell_top - cell_h, cell_w, cell_h, 3, fill=1, stroke=1)

    identifiant = str(entry.get("identifiant", "") or "")
    photo_x = x + (cell_w - photo_w_mm * mm) / 2
    photo_top = cell_top - pad_mm * mm
    photo_h = photo_h_mm * mm
    photo = photos.get(identifiant.lower()) or entry.get("photo") or b""
    drawn = _draw_photo(c, photo, photo_x, photo_top - photo_h, photo_w_mm * mm, photo_h, placeholder_hex="#EFEFEF")

    if not drawn:
        # Emplacement réservé : initiales centrées dans la vignette
        initials = _initials(entry.get("nom_complet") or identifiant)
        _draw_text(c, initials, photo_x, photo_top - photo_h, photo_w_mm * mm, photo_h,
                   "Helvetica-Bold", 22, "#9A9A9A", "centre")

    text_top = photo_top - photo_h - 1 * mm
    name = str(entry.get("nom_complet") or entry.get("cn") or identifiant or "")
    _draw_text(c, name, x + pad_mm * mm, text_top, cell_w - 2 * pad_mm * mm, name_h_mm * mm,
               "Helvetica-Bold", 10, "#333333", "centre")
    classe = str(entry.get("classe", "") or "")
    if classe:
        _draw_text(c, classe, x + pad_mm * mm, text_top - name_h_mm * mm,
                   cell_w - 2 * pad_mm * mm, class_h_mm * mm,
                   "Helvetica", 8.5, accent, "centre")


def _initials(label: str) -> str:
    """Initiales d'un nom complet (« Thomas Martin » → « TM », « Eleve 10 » → « EL »)."""
    text = (label or "").strip()
    tokens = [t for t in re.split(r"\s+", text) if t]
    alpha = [t for t in tokens if any(char.isalpha() for char in t)]
    initials = "".join(token[0].upper() for token in alpha[:2])
    if len(initials) >= 2:
        return initials
    stripped = re.sub(r"[^0-9A-Za-zÀ-ÿ]+", "", text)
    return (stripped[:2].upper() or "?") if stripped else "?"


# -- Envoi par mail ------------------------------------------------------------------------
# La configuration SMTP (MailConfig, MailError, MAIL_CONFIG_FILE, load/save)
# vit dans core/mailer.py — partagée avec le portail auto-service (M27).


def send_label_email(
    config: MailConfig,
    to_addr: str,
    subject: str,
    body: str,
    pdf_path: Path,
    *,
    filename: str | None = None,
    smtp_factory=None,
) -> None:
    """Envoie l'étiquette PDF d'un destinataire — lève ``MailError`` en français.

    `smtp_factory(hôte, port, timeout)` → objet compatible ``smtplib.SMTP``
    (injectable pour les tests).
    """
    message = build_message(
        config,
        to_addr,
        subject,
        body or "Veuillez trouver en pièce jointe votre étiquette.",
        default_subject="Votre étiquette",
    )
    try:
        payload = Path(pdf_path).read_bytes()
    except OSError as exc:
        raise MailError(f"Lecture de l'étiquette PDF impossible : {exc}") from exc
    if not payload.startswith(b"%PDF"):
        raise MailError("Le fichier à joindre n'est pas un PDF.")

    attachment = filename or Path(pdf_path).name or "etiquette.pdf"
    message.add_attachment(
        payload,
        maintype="application",
        subtype="pdf",
        filename=attachment,
    )
    send_message(config, message, smtp_factory=smtp_factory)
