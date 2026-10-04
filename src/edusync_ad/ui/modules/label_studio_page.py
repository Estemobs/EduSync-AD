"""Module 24 — Étiquettes & trombinoscopes avancés.

Écran en trois onglets :

1. **Éditeur** — liste des modèles, canvas WYSIWYG (clic-glisser pour
   positionner les champs, en mm), propriétés de l'élément sélectionné
   (type, champ, police, corps, couleur, alignement) et du modèle
   (planche, fond, bordure). Duplication, export/import JSON.
2. **Export** — chargement d'une OU (± sous-OU), planche d'étiquettes PDF
   à partir d'un modèle, trombinoscope photo + nom sur A4/A3. Photos AD
   (`jpegPhoto`/`thumbnailPhoto`) ou depuis un dossier de fichiers.
3. **Envoi par mail** — configuration SMTP (mot de passe chiffré) puis
   envoi de l'étiquette PDF d'un destinataire unique.

Chaque opération est journalisée (M7).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.export import EXPORT_FIELDS, build_export_row
from edusync_ad.core.label_studio import (
    ALIGNMENTS,
    ELEMENT_KINDS,
    FONTS,
    SHEETS,
    LabelElement,
    LabelTemplate,
    MailConfig,
    MailError,
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
from edusync_ad.core.photos import map_photos_convention, process_photo_from_path

SCALE_PX_PER_MM = 8.0
CANVAS_PAD_PX = 16

# Valeurs d'exemple affichées dans le canvas (le PDF reste la référence).
SAMPLE_USER = {
    "identifiant": "thomas.martin",
    "nom_complet": "Thomas Martin",
    "prenom": "Thomas",
    "nom": "Martin",
    "classe": "3emeA",
    "mail": "thomas.martin@lycee.fr",
    "etat": "Actif",
}

QT_FONT_FAMILIES = {"Helvetica": "Helvetica", "Times": "Times New Roman", "Courier": "Courier New"}


def _qt_font(element: LabelElement) -> QFont:
    """Police reportlab (``Helvetica-Bold``) → QFont approximative pour l'aperçu."""
    family_key, _, style = element.font.partition("-")
    font = QFont(QT_FONT_FAMILIES.get(family_key, family_key))
    font.setBold("Bold" in style)
    font.setItalic("Oblique" in style or "Italic" in style)
    # 1 pt = 25,4/96 px à 96 dpi — échelle du canvas pour un aperçu fidèle.
    font.setPointSizeF(max(4.0, element.size_pt * SCALE_PX_PER_MM * 25.4 / 96))
    return font


class LabelCanvas(QWidget):
    """Aperçu WYSIWYG d'une étiquette : sélection au clic, déplacement au glisser."""

    selectedChanged = pyqtSignal(int)   # -1 = rien de sélectionné
    moved = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._template: LabelTemplate | None = None
        self._selected = -1
        self._drag_offset = (0.0, 0.0)
        self._dragging = False
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)

    # -- Modèle / sélection ------------------------------------------------------

    @property
    def selected_index(self) -> int:
        return self._selected

    def set_template(self, template: LabelTemplate | None) -> None:
        self._template = template
        self._selected = -1
        self._dragging = False
        self._resize_to_label()
        self.update()

    def set_selection(self, index: int) -> None:
        if index != self._selected:
            self._selected = index
            self.update()

    def _label_size(self) -> tuple[float, float]:
        if self._template is None or self._template.spec is None:
            return (63.5, 38.1)
        return self._template.spec.label_size_mm

    def _resize_to_label(self) -> None:
        width_mm, height_mm = self._label_size()
        self.setMinimumSize(
            int(width_mm * SCALE_PX_PER_MM + 2 * CANVAS_PAD_PX),
            int(height_mm * SCALE_PX_PER_MM + 2 * CANVAS_PAD_PX),
        )

    def _mm_at(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Coordonnées en mm par rapport au coin haut-gauche de l'étiquette."""
        return (
            (x_px - CANVAS_PAD_PX) / SCALE_PX_PER_MM,
            (y_px - CANVAS_PAD_PX) / SCALE_PX_PER_MM,
        )

    # -- Rendu ---------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#EFEFEF"))
        if self._template is None:
            painter.setPen(QColor("#888888"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Aucun modèle sélectionné")
            return

        scale = SCALE_PX_PER_MM
        width_mm, height_mm = self._label_size()
        label_rect = QRectF(
            CANVAS_PAD_PX, CANVAS_PAD_PX, width_mm * scale, height_mm * scale
        )
        painter.fillRect(label_rect, QColor(self._template.background_hex))
        if self._template.border_hex:
            painter.setPen(QPen(QColor(self._template.border_hex), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(label_rect)

        for index, element in enumerate(self._template.elements):
            rect = QRectF(
                CANVAS_PAD_PX + element.x_mm * scale,
                CANVAS_PAD_PX + element.y_mm * scale,
                element.w_mm * scale,
                element.h_mm * scale,
            )
            self._paint_element(painter, element, rect)
            if index == self._selected:
                painter.setPen(QPen(QColor("#2F6FEB"), 1.5, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
        painter.end()

    def _paint_element(self, painter: QPainter, element: LabelElement, rect: QRectF) -> None:
        if element.kind == "photo":
            painter.setPen(QPen(QColor("#B5B5B5"), 1))
            painter.setBrush(QColor("#E9E9E9"))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#8A8A8A"))
            painter.setFont(QFont("Helvetica", 10))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Photo")
            return
        if element.kind == "qr":
            painter.setPen(QPen(QColor("#8A8A8A"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#8A8A8A"))
            painter.setFont(QFont("Helvetica", 9))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "QR")
            return

        value = element.value(SAMPLE_USER)
        painter.setFont(_qt_font(element))
        painter.setPen(QColor(element.color_hex))
        flags = Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine
        flags |= {
            "gauche": Qt.AlignmentFlag.AlignLeft,
            "centre": Qt.AlignmentFlag.AlignHCenter,
            "droite": Qt.AlignmentFlag.AlignRight,
        }.get(element.align, Qt.AlignmentFlag.AlignHCenter)
        painter.drawText(rect.adjusted(2, 0, -2, 0), flags, value or "—")

    # -- Interaction ----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if self._template is None:
            return
        x_mm, y_mm = self._mm_at(event.position().x(), event.position().y())
        index = self._template.element_index_at(x_mm, y_mm)
        self._selected = index if index is not None else -1
        self._dragging = self._selected >= 0
        if self._dragging:
            element = self._template.elements[self._selected]
            self._drag_offset = (x_mm - element.x_mm, y_mm - element.y_mm)
        self.selectedChanged.emit(self._selected)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._template is None:
            return
        x_mm, y_mm = self._mm_at(event.position().x(), event.position().y())
        if self._dragging and self._selected >= 0:
            element = self._template.elements[self._selected]
            label_w, label_h = self._label_size()
            new_x = min(max(0.0, x_mm - self._drag_offset[0]), max(0.0, label_w - element.w_mm))
            new_y = min(max(0.0, y_mm - self._drag_offset[1]), max(0.0, label_h - element.h_mm))
            if (new_x, new_y) != (element.x_mm, element.y_mm):
                element.x_mm = round(new_x, 2)
                element.y_mm = round(new_y, 2)
                self.update()
                self.moved.emit()
            return
        hovered = self._template.element_index_at(x_mm, y_mm)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if hovered is not None else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False


class LabelStudioPage(QWidget):
    """Page M24 — Étiquettes & trombinoscopes avancés."""

    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.session_id = session_id

        self._templates: list[LabelTemplate] = load_templates()
        self._current: LabelTemplate | None = None
        self._loaded_users: list[dict] = []
        self._photos: dict[str, bytes] = {}
        self._mail_config: MailConfig = load_mail_config()
        self._syncing = False

        self._build_ui()
        self._refresh_template_list(select=0 if self._templates else None)
        self._populate_mail_config()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- Construction UI ----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_editor_tab(), "Éditeur")
        self.tabs.addTab(self._build_export_tab(), "Export & trombinoscope")
        self.tabs.addTab(self._build_mail_tab(), "Envoi par mail")
        layout.addWidget(self.tabs)

    # -- Onglet 1 : éditeur ---------------------------------------------------------

    def _build_editor_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # Colonne gauche : liste des modèles
        left = QVBoxLayout()
        left.addWidget(QLabel("Modèles :"))
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        self.template_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.template_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        left.addWidget(self.template_list, 1)
        for label, slot in (
            ("Nouveau", self._on_new_template),
            ("Dupliquer", self._on_duplicate_template),
            ("Supprimer", self._on_delete_template),
            ("Enregistrer", self._on_save_templates),
            ("Exporter…", self._on_export_template),
            ("Importer…", self._on_import_template),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            left.addWidget(button)
        layout.addLayout(left, 1)

        # Centre : canvas + légende
        middle = QVBoxLayout()
        self.canvas = LabelCanvas()
        self.canvas.selectedChanged.connect(self._on_canvas_selection)
        self.canvas.moved.connect(self._on_canvas_moved)
        middle.addWidget(self.canvas, 1, Qt.AlignmentFlag.AlignCenter)
        self.canvas_hint = QLabel(
            "Cliquez un champ pour le sélectionner, glissez-le pour le positionner "
            "(coordonnées en mm depuis le coin haut-gauche de l'étiquette)."
        )
        self.canvas_hint.setWordWrap(True)
        self.canvas_hint.setStyleSheet("color: #888;")
        middle.addWidget(self.canvas_hint)
        self.canvas_size_label = QLabel("")
        self.canvas_size_label.setStyleSheet("color: #888;")
        middle.addWidget(self.canvas_size_label)
        layout.addLayout(middle, 3)

        # Colonne droite : propriétés
        right = QVBoxLayout()
        right.addWidget(self._build_template_props())
        right.addWidget(self._build_element_props())
        right.addStretch()
        layout.addLayout(right, 2)
        return widget

    def _build_template_props(self) -> QGroupBox:
        group = QGroupBox("Modèle")
        form = QFormLayout(group)
        self.sheet_combo = QComboBox()
        for key, spec in SHEETS.items():
            self.sheet_combo.addItem(spec.nom, key)
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        form.addRow("Planche :", self.sheet_combo)

        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_name_changed)
        form.addRow("Nom :", self.name_edit)

        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        self.bg_btn = QPushButton()
        self.bg_btn.setFixedWidth(90)
        self.bg_btn.clicked.connect(lambda: self._pick_color("background"))
        self.border_btn = QPushButton()
        self.border_btn.setFixedWidth(90)
        self.border_btn.clicked.connect(lambda: self._pick_color("border"))
        color_layout.addWidget(self.bg_btn)
        color_layout.addWidget(self.border_btn)
        color_layout.addStretch()
        form.addRow("Couleurs :", color_row)
        return group

    def _build_element_props(self) -> QGroupBox:
        group = QGroupBox("Élément sélectionné")
        self.element_group = group
        form = QFormLayout(group)

        self.kind_combo = QComboBox()
        for key, label in ELEMENT_KINDS.items():
            self.kind_combo.addItem(label, key)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("Type :", self.kind_combo)

        self.field_combo = QComboBox()
        for key, label in EXPORT_FIELDS.items():
            self.field_combo.addItem(label, key)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        form.addRow("Champ :", self.field_combo)

        self.text_edit = QLineEdit()
        self.text_edit.editingFinished.connect(self._on_text_changed)
        form.addRow("Texte fixe :", self.text_edit)

        geom_row = QWidget()
        geom_layout = QHBoxLayout(geom_row)
        geom_layout.setContentsMargins(0, 0, 0, 0)
        self.x_spin = self._mm_spin("X (gauche)")
        self.y_spin = self._mm_spin("Y (haut)")
        self.w_spin = self._mm_spin("Largeur")
        self.h_spin = self._mm_spin("Hauteur")
        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            geom_layout.addWidget(spin)
        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            spin.valueChanged.connect(self._on_geometry_changed)
        form.addRow("Position / taille (mm) :", geom_row)

        style_row = QWidget()
        style_layout = QHBoxLayout(style_row)
        style_layout.setContentsMargins(0, 0, 0, 0)
        self.font_combo = QComboBox()
        self.font_combo.addItems(FONTS)
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(4.0, 72.0)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.valueChanged.connect(self._on_size_changed)
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(70)
        self.color_btn.clicked.connect(lambda: self._pick_color("element"))
        style_layout.addWidget(self.font_combo, 2)
        style_layout.addWidget(self.size_spin)
        style_layout.addWidget(self.color_btn)
        form.addRow("Police / corps / couleur :", style_row)

        align_row = QWidget()
        align_layout = QHBoxLayout(align_row)
        align_layout.setContentsMargins(0, 0, 0, 0)
        self.align_combo = QComboBox()
        for key in ALIGNMENTS:
            self.align_combo.addItem(key.capitalize(), key)
        self.align_combo.currentIndexChanged.connect(self._on_align_changed)
        align_layout.addWidget(self.align_combo)
        align_layout.addStretch()
        form.addRow("Alignement :", align_row)

        # Boutons toujours actifs : on peut ajouter un élément même sans sélection.
        self._element_widgets = [
            self.kind_combo, self.field_combo, self.text_edit,
            self.x_spin, self.y_spin, self.w_spin, self.h_spin,
            self.font_combo, self.size_spin, self.color_btn, self.align_combo,
        ]

        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        for label, kind in (
            ("+ Champ", "texte"),
            ("+ Texte fixe", "statique"),
            ("+ Photo", "photo"),
            ("+ QR code", "qr"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, k=kind: self._add_element(k))
            buttons_layout.addWidget(button)
        remove_btn = QPushButton("– Supprimer")
        remove_btn.clicked.connect(self._remove_element)
        buttons_layout.addWidget(remove_btn)
        form.addRow("", buttons)
        return group

    @staticmethod
    def _mm_spin(tooltip: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 500.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setFixedWidth(64)
        spin.setToolTip(tooltip)
        return spin

    # -- Onglet 2 : export & trombinoscope -------------------------------------------

    def _build_export_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        source_group = QGroupBox("1. Source")
        source_form = QHBoxLayout(source_group)
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(320)
        refresh_btn = QPushButton("Actualiser")
        refresh_btn.clicked.connect(self._load_ous)
        self.include_sub_ous = QCheckBox("Inclure les sous-OU")
        self.include_sub_ous.setChecked(True)
        load_btn = QPushButton("2. Charger les comptes")
        load_btn.clicked.connect(self._on_load_users)
        source_form.addWidget(QLabel("OU :"))
        source_form.addWidget(self.ou_combo)
        source_form.addWidget(refresh_btn)
        source_form.addWidget(self.include_sub_ous)
        source_form.addWidget(load_btn)
        source_form.addStretch()
        layout.addWidget(source_group)

        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(
            ["Identifiant", "Nom complet", "Classe / OU", "État"]
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.preview_table, 1)

        self.load_info = QLabel("Aucun compte chargé.")
        layout.addWidget(self.load_info)

        photo_row = QHBoxLayout()
        self.photo_ad_check = QCheckBox("Photos AD (jpegPhoto / thumbnailPhoto)")
        self.photo_ad_check.setChecked(True)
        self.load_photos_btn = QPushButton("Charger les photos")
        self.load_photos_btn.clicked.connect(self._on_load_photos)
        self.photo_dir_btn = QPushButton("Photos depuis un dossier…")
        self.photo_dir_btn.clicked.connect(self._on_load_photos_dir)
        photo_row.addWidget(self.photo_ad_check)
        photo_row.addWidget(self.load_photos_btn)
        photo_row.addWidget(self.photo_dir_btn)
        self.photo_info = QLabel("")
        photo_row.addWidget(self.photo_info)
        photo_row.addStretch()
        layout.addLayout(photo_row)

        labels_group = QGroupBox("3. Planche d'étiquettes")
        labels_form = QHBoxLayout(labels_group)
        self.export_template_combo = QComboBox()
        labels_form.addWidget(QLabel("Modèle :"))
        labels_form.addWidget(self.export_template_combo)
        export_btn = QPushButton("Exporter la planche PDF…")
        export_btn.clicked.connect(self._on_export_labels)
        labels_form.addWidget(export_btn)
        labels_form.addStretch()
        layout.addWidget(labels_group)

        trombi_group = QGroupBox("4. Trombinoscope (photo + nom)")
        trombi_form = QHBoxLayout(trombi_group)
        self.trombi_title = QLineEdit()
        self.trombi_title.setPlaceholderText("Titre (ex. : Trombinoscope 3emeA)")
        self.trombi_title.setMaximumWidth(320)
        self.trombi_subtitle = QLineEdit()
        self.trombi_subtitle.setPlaceholderText("Sous-titre (ex. : Année 2026-2027)")
        self.trombi_subtitle.setMaximumWidth(260)
        self.trombi_page = QComboBox()
        self.trombi_page.addItems(["A4", "A3"])
        self.trombi_columns = QSpinBox()
        self.trombi_columns.setRange(1, 8)
        self.trombi_columns.setValue(4)
        trombi_btn = QPushButton("Générer le trombinoscope…")
        trombi_btn.clicked.connect(self._on_export_trombinoscope)
        trombi_form.addWidget(QLabel("Titre :"))
        trombi_form.addWidget(self.trombi_title)
        trombi_form.addWidget(QLabel("Sous-titre :"))
        trombi_form.addWidget(self.trombi_subtitle)
        trombi_form.addWidget(QLabel("Format :"))
        trombi_form.addWidget(self.trombi_page)
        trombi_form.addWidget(QLabel("Colonnes :"))
        trombi_form.addWidget(self.trombi_columns)
        trombi_form.addWidget(trombi_btn)
        trombi_form.addStretch()
        layout.addWidget(trombi_group)
        return widget

    # -- Onglet 3 : envoi par mail -----------------------------------------------------

    def _build_mail_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        config_group = QGroupBox("Serveur SMTP")
        form = QFormLayout(config_group)
        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText("smtp.ac-paris.fr")
        form.addRow("Serveur :", self.smtp_host)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(587)
        form.addRow("Port :", self.smtp_port)
        self.smtp_username = QLineEdit()
        form.addRow("Utilisateur :", self.smtp_username)
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Mot de passe :", self.smtp_password)
        self.smtp_tls = QCheckBox("STARTTLS")
        self.smtp_tls.setChecked(True)
        self.smtp_ssl = QCheckBox("SSL/TLS implicite (port 465)")
        self.smtp_from_addr = QLineEdit()
        self.smtp_from_addr.setPlaceholderText("edusync@mon-etablissement.fr")
        self.smtp_from_name = QLineEdit("EduSync AD")
        form.addRow("Sécurité :", self._hrow(self.smtp_tls, self.smtp_ssl))
        form.addRow("Adresse d'expéditeur :", self.smtp_from_addr)
        form.addRow("Nom affiché :", self.smtp_from_name)
        save_cfg_btn = QPushButton("Enregistrer la configuration")
        save_cfg_btn.clicked.connect(self._on_save_mail_config)
        form.addRow("", save_cfg_btn)
        self.mail_cfg_hint = QLabel(
            "Le mot de passe est chiffré (AES-256) dans le profil utilisateur — jamais en clair."
        )
        self.mail_cfg_hint.setWordWrap(True)
        self.mail_cfg_hint.setStyleSheet("color: #888;")
        form.addRow("", self.mail_cfg_hint)
        layout.addWidget(config_group)

        send_group = QGroupBox("Destinataire & envoi")
        send_form = QFormLayout(send_group)
        self.mail_user_combo = QComboBox()
        self.mail_user_combo.currentIndexChanged.connect(self._on_mail_user_changed)
        send_form.addRow("Compte :", self.mail_user_combo)
        self.mail_to = QLineEdit()
        self.mail_to.setPlaceholderText("prenom.nom@mon-etablissement.fr")
        send_form.addRow("Adresse :", self.mail_to)
        self.mail_template_combo = QComboBox()
        send_form.addRow("Modèle d'étiquette :", self.mail_template_combo)
        self.mail_subject = QLineEdit("Votre étiquette")
        send_form.addRow("Objet :", self.mail_subject)
        self.mail_body = QTextEdit()
        self.mail_body.setPlainText(
            "Bonjour,\n\nVous trouverez en pièce jointe votre étiquette.\n\nCordialement."
        )
        self.mail_body.setFixedHeight(110)
        send_form.addRow("Message :", self.mail_body)
        send_btn = QPushButton("Envoyer l'étiquette…")
        send_btn.clicked.connect(self._on_send_label)
        send_form.addRow("", send_btn)
        layout.addWidget(send_group)
        layout.addStretch()
        return widget

    @staticmethod
    def _hrow(*widgets: QWidget) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row_layout.addWidget(widget)
        row_layout.addStretch()
        return row

    # -- Liste de modèles ---------------------------------------------------------------

    def _refresh_template_list(self, select: int | None = None) -> None:
        self.template_list.blockSignals(True)
        self.template_list.clear()
        for template in self._templates:
            suffixe = "  (intégré)" if template.builtin else ""
            item = QListWidgetItem(f"{template.name}{suffixe}")
            item.setToolTip(template.name)
            self.template_list.addItem(item)
        self.template_list.blockSignals(False)
        self._refresh_template_combos()
        if select is not None and 0 <= select < self.template_list.count():
            self.template_list.setCurrentRow(select)
        elif self.template_list.count():
            self.template_list.setCurrentRow(0)
        else:
            self._set_current(None)

    def _refresh_template_combos(self) -> None:
        for combo in (self.export_template_combo, self.mail_template_combo):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for template in self._templates:
                combo.addItem(template.name, template.id)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _on_template_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._templates):
            self._set_current(None)
            return
        self._set_current(self._templates[row])

    def _set_current(self, template: LabelTemplate | None) -> None:
        self._current = template
        self.canvas.set_template(template)
        self._populate_template_props()
        self._populate_element_props()

    def _current_index(self) -> int:
        return self.template_list.currentRow()

    def _on_new_template(self) -> None:
        base = unique_template_id(self._templates, "modele")
        sheet_key = self.sheet_combo.currentData() or "avery_l7160"
        template = LabelTemplate(
            id=base,
            name=f"Nouveau modèle {len(self._templates) + 1}",
            sheet=sheet_key,
            background_hex="#FFFFFF",
            border_hex="#C9C9C9",
            elements=[LabelElement(kind="texte", field="nom_complet")],
        )
        self._templates.append(template)
        self._refresh_template_list(select=len(self._templates) - 1)
        self.audit_log.record(
            "modele_etiquette", template.id, "succes", self.session_id, detail="création"
        )

    def _on_duplicate_template(self) -> None:
        if self._current is None:
            return
        clone = duplicate_template(self._templates, self._current)
        self._templates.append(clone)
        self._refresh_template_list(select=len(self._templates) - 1)
        self.audit_log.record(
            "modele_etiquette", clone.id, "succes", self.session_id, detail="duplication"
        )

    def _on_delete_template(self) -> None:
        index = self._current_index()
        if index < 0 or index >= len(self._templates):
            return
        template = self._templates[index]
        answer = QMessageBox.question(
            self, "Supprimer le modèle",
            f"Supprimer définitivement « {template.name} » ?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._templates[index]
        self._refresh_template_list(select=min(index, len(self._templates) - 1))
        self.audit_log.record(
            "modele_etiquette", template.id, "succes", self.session_id, detail="suppression"
        )

    def _on_save_templates(self) -> None:
        invalid = [(t.id, t.validate()) for t in self._templates]
        invalid = [(tid, errors) for tid, errors in invalid if errors]
        if invalid:
            details = "\n".join(f"{tid} : {'; '.join(errs)}" for tid, errs in invalid)
            QMessageBox.warning(self, "Modèle(s) invalide(s)", details)
            return
        path = save_templates(self._templates)
        self.audit_log.record(
            "modele_etiquette", "-", "succes", self.session_id,
            detail=f"{len(self._templates)} modèle(s) → {path}",
        )
        QMessageBox.information(self, "Enregistré", f"{len(self._templates)} modèle(s) enregistré(s).")

    def _on_export_template(self) -> None:
        if self._current is None:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter le modèle", f"{self._current.id}.json", "JSON (*.json)"
        )
        if not dest:
            return
        try:
            path = export_template(self._current, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record(
            "modele_etiquette", self._current.id, "succes", self.session_id, detail=f"export {path}"
        )

    def _on_import_template(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Importer un modèle", "", "JSON (*.json)")
        if not source:
            return
        try:
            template = import_template(Path(source))
        except ValueError as exc:
            QMessageBox.critical(self, "Import impossible", str(exc))
            return
        template.id = unique_template_id(self._templates, template.id)
        self._templates.append(template)
        self._refresh_template_list(select=len(self._templates) - 1)
        self.audit_log.record(
            "modele_etiquette", template.id, "succes", self.session_id, detail="import"
        )

    # -- Propriétés du modèle ------------------------------------------------------------

    def _populate_template_props(self) -> None:
        self._syncing = True
        try:
            if self._current is None:
                self._set_element_widgets_enabled(False)
                return
            index = self.sheet_combo.findData(self._current.sheet)
            self.sheet_combo.setCurrentIndex(index if index >= 0 else 0)
            self.name_edit.setText(self._current.name)
            self._style_color_button(self.bg_btn, self._current.background_hex)
            self._style_color_button(
                self.border_btn, self._current.border_hex, empty_label="Aucune"
            )
            spec = self._current.spec
            if spec is not None:
                self.canvas_size_label.setText(
                    f"{spec.nom} — étiquette {spec.largeur_mm:g} × {spec.hauteur_mm:g} mm, "
                    f"{spec.par_planche} par planche {spec.page}"
                )
        finally:
            self._syncing = False

    @staticmethod
    def _style_color_button(button: QPushButton, color_hex: str, empty_label: str = "…") -> None:
        button.setText(color_hex or empty_label)
        if color_hex:
            button.setStyleSheet(
                f"background-color: {color_hex}; color: {'#ffffff' if _is_dark(color_hex) else '#000000'};"
            )
        else:
            button.setStyleSheet("")

    def _on_sheet_changed(self) -> None:
        if self._syncing or self._current is None:
            return
        self._current.sheet = str(self.sheet_combo.currentData())
        self.canvas.set_template(self._current)
        self._populate_template_props()
        self._populate_element_props()

    def _on_name_changed(self) -> None:
        if self._syncing or self._current is None:
            return
        name = self.name_edit.text().strip()
        if name:
            self._current.name = name
            index = self._current_index()
            if 0 <= index < self.template_list.count():
                item = self.template_list.item(index)
                suffixe = "  (intégré)" if self._current.builtin else ""
                item.setText(f"{name}{suffixe}")
                item.setToolTip(name)
            self._refresh_template_combos()

    def _pick_color(self, target: str) -> None:
        element = self._selected_element()
        current = {
            "background": self._current.background_hex if self._current else "#FFFFFF",
            "border": self._current.border_hex if self._current else "#C9C9C9",
            "element": element.color_hex if element else "#1B4F91",
        }.get(target, "#000000")
        color = QColorDialog.getColor(QColor(current), self, "Choisir une couleur")
        if not color.isValid():
            return
        color_hex = color.name()
        if target == "background" and self._current is not None:
            self._current.background_hex = color_hex
        elif target == "border" and self._current is not None:
            self._current.border_hex = color_hex
        elif target == "element" and element is not None:
            element.color_hex = color_hex
        else:
            return
        self.canvas.update()
        self._populate_template_props()
        self._populate_element_props()

    # -- Propriétés de l'élément ----------------------------------------------------------

    def _selected_element(self) -> LabelElement | None:
        if self._current is None:
            return None
        index = self.canvas.selected_index
        if 0 <= index < len(self._current.elements):
            return self._current.elements[index]
        return None

    def _set_element_widgets_enabled(self, enabled: bool) -> None:
        for widget in self._element_widgets:
            widget.setEnabled(enabled)

    def _populate_element_props(self) -> None:
        self._syncing = True
        try:
            element = self._selected_element()
            if element is None:
                self._set_element_widgets_enabled(False)
                return
            self._set_element_widgets_enabled(True)
            index = self.kind_combo.findData(element.kind)
            self.kind_combo.setCurrentIndex(index if index >= 0 else 0)
            index = self.field_combo.findData(element.field)
            self.field_combo.setCurrentIndex(index if index >= 0 else 0)
            self.text_edit.setText(element.text)
            self.x_spin.setValue(element.x_mm)
            self.y_spin.setValue(element.y_mm)
            self.w_spin.setValue(element.w_mm)
            self.h_spin.setValue(element.h_mm)
            index = self.font_combo.findText(element.font)
            self.font_combo.setCurrentIndex(index if index >= 0 else 0)
            self.size_spin.setValue(element.size_pt)
            self._style_color_button(self.color_btn, element.color_hex)
            index = self.align_combo.findData(element.align)
            self.align_combo.setCurrentIndex(index if index >= 0 else 0)
            # Les widgets inutiles pour ce type restent grisés.
            self.text_edit.setEnabled(element.kind == "statique")
            self.field_combo.setEnabled(element.kind in ("texte", "qr"))
            is_text = element.kind in ("texte", "statique")
            for widget in (self.font_combo, self.size_spin, self.align_combo):
                widget.setEnabled(is_text)
        finally:
            self._syncing = False

    def _on_canvas_selection(self, index: int) -> None:
        self._populate_element_props()

    def _on_canvas_moved(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        self._syncing = True
        try:
            self.x_spin.setValue(element.x_mm)
            self.y_spin.setValue(element.y_mm)
        finally:
            self._syncing = False

    def _require_element(self) -> LabelElement | None:
        element = self._selected_element()
        if element is None:
            QMessageBox.information(self, "Aucun élément", "Sélectionnez d'abord un élément.")
        return element

    def _on_kind_changed(self) -> None:
        if self._syncing:
            return
        element = self._require_element()
        if element is None:
            return
        element.kind = str(self.kind_combo.currentData())
        self.canvas.update()
        self._populate_element_props()

    def _on_field_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.field = str(self.field_combo.currentData())
        self.canvas.update()

    def _on_text_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.text = self.text_edit.text()
        self.canvas.update()

    def _on_geometry_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.x_mm = self.x_spin.value()
        element.y_mm = self.y_spin.value()
        element.w_mm = max(1.0, self.w_spin.value())
        element.h_mm = max(1.0, self.h_spin.value())
        self.canvas.update()

    def _on_font_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.font = self.font_combo.currentText()
        self.canvas.update()

    def _on_size_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.size_pt = self.size_spin.value()
        self.canvas.update()

    def _on_align_changed(self) -> None:
        if self._syncing:
            return
        element = self._selected_element()
        if element is None:
            return
        element.align = str(self.align_combo.currentData())
        self.canvas.update()

    def _add_element(self, kind: str) -> None:
        if self._current is None:
            return
        defaults = {
            "texte": LabelElement(kind="texte", field="identifiant", x_mm=5, y_mm=5),
            "statique": LabelElement(kind="statique", text="ÉTABLISSEMENT", x_mm=5, y_mm=5),
            "photo": LabelElement(kind="photo", x_mm=5, y_mm=5, w_mm=20, h_mm=26),
            "qr": LabelElement(kind="qr", field="identifiant", x_mm=45, y_mm=5, w_mm=12, h_mm=12),
        }
        element = defaults[kind]
        spec = self._current.spec
        if spec is not None:
            label_w, label_h = spec.label_size_mm
            element.x_mm = min(element.x_mm, max(0.0, label_w - element.w_mm))
            element.y_mm = min(element.y_mm, max(0.0, label_h - element.h_mm))
        self._current.elements.append(element)
        self.canvas.set_selection(len(self._current.elements) - 1)
        self.canvas.update()
        self._populate_element_props()

    def _remove_element(self) -> None:
        element = self._selected_element()
        if self._current is None or element is None:
            return
        self._current.elements.remove(element)
        self.canvas.set_selection(-1)
        self.canvas.update()
        self._populate_element_props()

    # -- Onglet export : source ---------------------------------------------------------

    def _load_ous(self) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        previous = self.ou_combo.currentData()
        self.ou_combo.clear()
        self.ou_combo.addItem(f"(racine du domaine) {self.ad_connection.domain}", base_dn)
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        index = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_load_users(self) -> None:
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Aucune OU", "Sélectionnez une OU ou actualisez la liste.")
            return
        try:
            if self.include_sub_ous.isChecked():
                users = self.ad_connection.list_users_in_ou(ou_dn)
            else:
                users = [
                    u for u in self.ad_connection.list_ou_contents(ou_dn)
                    if u.get("kind", "user") == "user"
                ]
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        self._loaded_users = users
        self._photos = {}
        self._populate_preview()
        self._refresh_template_combos()
        self._populate_mail_recipients()
        self.load_info.setText(f"{len(users)} compte(s) chargé(s).")
        self.photo_info.setText("")

    def _populate_preview(self) -> None:
        self.preview_table.setRowCount(len(self._loaded_users))
        for row, user in enumerate(self._loaded_users):
            export_row = build_export_row({**user, "sAMAccountName": user.get("sam", "")})
            for column, key in enumerate(("identifiant", "nom_complet", "classe", "etat")):
                self.preview_table.setItem(row, column, QTableWidgetItem(export_row[key]))

    def _export_rows(self) -> list[dict]:
        return [
            build_export_row({**user, "sAMAccountName": user.get("sam", "")})
            for user in self._loaded_users
        ]

    # -- Onglet export : photos ------------------------------------------------------------

    def _on_load_photos(self) -> None:
        if not self._loaded_users:
            QMessageBox.information(self, "Aucun compte", "Chargez d'abord les comptes (étape 2).")
            return
        if not self.photo_ad_check.isChecked():
            QMessageBox.information(
                self, "Option désactivée",
                "Cochez « Photos AD » pour lire jpegPhoto / thumbnailPhoto."
            )
            return
        loaded, errors = 0, 0
        for user in self._loaded_users:
            try:
                attrs = self.ad_connection.get_user_attributes(user["dn"])
            except ADError:
                errors += 1
                continue
            photo = attrs.get("thumbnailPhoto") or attrs.get("jpegPhoto")
            if isinstance(photo, bytes) and photo:
                self._photos[str(user.get("sam", "")).lower()] = photo
                loaded += 1
        self.photo_info.setText(
            f"{loaded} photo(s) chargée(s)" + (f", {errors} erreur(s)" if errors else "")
        )

    def _on_load_photos_dir(self) -> None:
        if not self._loaded_users:
            QMessageBox.information(self, "Aucun compte", "Chargez d'abord les comptes (étape 2).")
            return
        directory = QFileDialog.getExistingDirectory(self, "Dossier des photos")
        if not directory:
            return
        mapping = map_photos_convention(Path(directory), self._loaded_users)

        loaded = 0
        for ident, path in mapping.matched.items():
            try:
                info = process_photo_from_path(path)
            except Exception:
                continue
            self._photos[ident.lower()] = info.data
            loaded += 1
        self.photo_info.setText(
            f"{loaded} photo(s) depuis un dossier ({len(mapping.unmatched_users)} compte(s) sans photo)."
        )

    # -- Onglet export : PDF ---------------------------------------------------------------

    def _require_template(self) -> LabelTemplate | None:
        template_id = self.export_template_combo.currentData()
        template = next((t for t in self._templates if t.id == template_id), None)
        if template is None and self._templates:
            template = self._templates[0]
        if template is None:
            QMessageBox.warning(self, "Aucun modèle", "Créez d'abord un modèle dans l'onglet Éditeur.")
        return template

    def _on_export_labels(self) -> None:
        rows = self._require_rows()
        if rows is None:
            return
        template = self._require_template()
        if template is None:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter la planche d'étiquettes", "etiquettes.pdf", "PDF (*.pdf)"
        )
        if not dest:
            return
        try:
            pages = render_labels_pdf(Path(dest), rows, template, photos=self._photos)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Erreur d'export", str(exc))
            return
        self.audit_log.record(
            "export_etiquette_avancee", template.id, "succes", self.session_id,
            detail=f"{len(rows)} étiquette(s), {pages} page(s), {len(self._photos)} photo(s) → {dest}",
        )
        QMessageBox.information(self, "Export terminé", f"Fichier enregistré : {dest}")

    def _on_export_trombinoscope(self) -> None:
        rows = self._require_rows()
        if rows is None:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Générer le trombinoscope", "trombinoscope.pdf", "PDF (*.pdf)"
        )
        if not dest:
            return
        try:
            pages = generate_trombinoscope_pdf(
                Path(dest), rows,
                page=str(self.trombi_page.currentText()),
                columns=self.trombi_columns.value(),
                title=self.trombi_title.text().strip(),
                subtitle=self.trombi_subtitle.text().strip(),
                photos=self._photos,
            )
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self.audit_log.record(
            "trombinoscope", "-", "succes", self.session_id,
            detail=f"{len(rows)} compte(s), {self.trombi_page.currentText()}, {pages} page(s) → {dest}",
        )
        QMessageBox.information(self, "Trombinoscope généré", f"Fichier enregistré : {dest}")

    def _require_rows(self) -> list[dict] | None:
        if not self._loaded_users:
            QMessageBox.information(self, "Aucun compte", "Chargez d'abord les comptes (étape 2).")
            return None
        return self._export_rows()

    # -- Onglet mail ------------------------------------------------------------------------

    def _populate_mail_config(self) -> None:
        cfg = self._mail_config
        self.smtp_host.setText(cfg.host)
        self.smtp_port.setValue(cfg.port)
        self.smtp_username.setText(cfg.username)
        self.smtp_password.setText(cfg.password)
        self.smtp_tls.setChecked(cfg.use_tls)
        self.smtp_ssl.setChecked(cfg.use_ssl)
        self.smtp_from_addr.setText(cfg.from_addr)
        self.smtp_from_name.setText(cfg.from_name)

    def _mail_config_from_ui(self) -> MailConfig:
        return MailConfig(
            host=self.smtp_host.text().strip(),
            port=self.smtp_port.value(),
            username=self.smtp_username.text().strip(),
            password=self.smtp_password.text(),
            use_tls=self.smtp_tls.isChecked(),
            use_ssl=self.smtp_ssl.isChecked(),
            from_addr=self.smtp_from_addr.text().strip(),
            from_name=self.smtp_from_name.text().strip() or "EduSync AD",
        )

    def _on_save_mail_config(self) -> None:
        config = self._mail_config_from_ui()
        try:
            path = save_mail_config(config)
        except MailError as exc:
            QMessageBox.warning(self, "Configuration invalide", str(exc))
            return
        self._mail_config = config
        self.audit_log.record(
            "config_smtp", config.host, "succes", self.session_id, detail=str(path)
        )
        QMessageBox.information(self, "Enregistré", f"Configuration SMTP enregistrée : {path}")

    def _populate_mail_recipients(self) -> None:
        current = self.mail_user_combo.currentData()
        self.mail_user_combo.blockSignals(True)
        self.mail_user_combo.clear()
        for user in self._loaded_users:
            label = f"{user.get('cn', '')}  ({user.get('sam', '')})"
            self.mail_user_combo.addItem(label, user.get("dn", ""))
        index = self.mail_user_combo.findData(current)
        self.mail_user_combo.setCurrentIndex(index if index >= 0 else 0)
        self.mail_user_combo.blockSignals(False)
        self._on_mail_user_changed()

    def _on_mail_user_changed(self) -> None:
        dn = self.mail_user_combo.currentData()
        if not dn:
            return
        try:
            attrs = self.ad_connection.get_user_attributes(dn)
        except ADError:
            return
        if attrs.get("mail"):
            self.mail_to.setText(str(attrs["mail"]))

    def _on_send_label(self) -> None:
        to_addr = self.mail_to.text().strip()
        if "@" not in to_addr:
            QMessageBox.warning(self, "Destinataire", "Renseignez une adresse mail valide.")
            return
        config = self._mail_config_from_ui()
        errors = config.validate()
        if errors:
            QMessageBox.warning(self, "Configuration SMTP", "\n".join(errors))
            return
        template_id = self.mail_template_combo.currentData()
        template = next((t for t in self._templates if t.id == template_id), None)
        if template is None:
            QMessageBox.warning(self, "Aucun modèle", "Choisissez un modèle d'étiquette.")
            return

        row = self._mail_recipient_row(to_addr)
        fd, name = tempfile.mkstemp(prefix="edusync-etiquette-", suffix=".pdf")
        os.close(fd)
        path = Path(name)
        try:
            render_labels_pdf(path, [row], template, photos=self._photos)
            send_label_email(
                config, to_addr,
                self.mail_subject.text(), self.mail_body.toPlainText(), path,
            )
        except (ValueError, MailError, OSError) as exc:
            QMessageBox.critical(self, "Envoi impossible", str(exc))
            self.audit_log.record(
                "mail_etiquette", to_addr, "echec", self.session_id, detail=str(exc)
            )
            return
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        self.audit_log.record(
            "mail_etiquette", to_addr, "succes", self.session_id,
            detail=f"modèle={template.id}, objet={self.mail_subject.text()}",
        )
        QMessageBox.information(self, "Envoyé", f"Étiquette envoyée à {to_addr}.")

    def _mail_recipient_row(self, to_addr: str) -> dict:
        """Ligne d'export du destinataire — synchrone avec l'onglet Export."""
        for user in self._loaded_users:
            if user.get("dn") == self.mail_user_combo.currentData():
                return build_export_row({**user, "sAMAccountName": user.get("sam", "")})
        local = to_addr.split("@", 1)[0]
        return build_export_row({"dn": f"CN={local},DC=example", "sam": local, "cn": local})


def _is_dark(color_hex: str) -> bool:
    """Luminance approximative pour choisir la couleur du texte sur la pastille."""
    try:
        red = int(color_hex[1:3], 16)
        green = int(color_hex[3:5], 16)
        blue = int(color_hex[5:7], 16)
    except (ValueError, IndexError):
        return False
    return (0.299 * red + 0.587 * green + 0.114 * blue) < 140
