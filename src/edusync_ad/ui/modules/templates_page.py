"""Module 23 — Modèles de groupes (templates).

Écran en trois onglets :

1. **Définition** — identité du modèle, OU parente + motif de sous-OU
   (``Eleves-{classe}``) et table des groupes auto-créés (motif, portée,
   description).
2. **Politiques** — quota FSRM, profil, dossier personnel, espace partagé,
   heures de connexion, politique de mot de passe, licences Microsoft 365 et
   script de connexion : tout ce que le modèle « attache » à la classe.
3. **Instanciation** — saisie des champs ``{…}`` (ex. nom de classe),
   choix de l'OU cible et du dossier d'artefacts puis bouton unique qui
   crée OU + groupes, enregistre les configs scopées et génère les scripts.

Duplication, export/import JSON **et** XML sont disponibles dans la colonne
de gauche ; chaque opération est journalisée (M7).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.class_spaces import ClassSpaceConfig
from edusync_ad.core.config import AppConfig
from edusync_ad.core.homedirs import HomeDirConfig
from edusync_ad.core.logon_hours import LogonHoursPreset
from edusync_ad.core.logon_scripts import (
    SUPPORTED_KINDS,
    SUPPORTED_TIMINGS,
    LogonScript,
    load_logon_scripts,
)
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.profiles import ProfileConfig, ProfileType
from edusync_ad.core.quotas import QuotaSettings
from edusync_ad.core.templates import (
    GROUP_SCOPES,
    TEMPLATE_KINDS,
    GroupSpec,
    GroupTemplate,
    duplicate_template,
    export_template,
    export_template_xml,
    import_template,
    import_template_xml,
    instantiate,
    load_templates,
    save_templates,
    unique_template_id,
    validate_values,
)

_TIMING_LABELS = {"logon": "à la connexion", "logoff": "à la déconnexion"}
_PROFILE_LABELS = {
    "local": "Local",
    "roaming": "Itinérant (roaming)",
    "mandatory": "Obligatoire (.man)",
}


class TemplatesPage(QWidget):
    """Page M23 — Modèles de groupes."""

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

        self._templates: list[GroupTemplate] = load_templates()
        self._current: GroupTemplate | None = None
        self._loading = False
        self._value_edits: dict[str, QLineEdit] = {}
        self._values_by_id: dict[str, dict[str, str]] = {}
        self._script_catalog: list[LogonScript] = load_logon_scripts()

        self._build_ui()
        self._refresh_list(select=0 if self._templates else None)

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.parent_combo.count() == 0:
            self._load_ous()

    # -- UI --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self.list_widget, 1)
        for label, slot in (
            ("Nouveau", self._on_new),
            ("Dupliquer", self._on_duplicate),
            ("Supprimer", self._on_delete),
            ("Enregistrer", self._on_save),
            ("Exporter JSON…", lambda: self._on_export("json")),
            ("Exporter XML…", lambda: self._on_export("xml")),
            ("Importer…", self._on_import),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            left.addWidget(button)
        left.addStretch()
        layout.addLayout(left, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_definition_tab(), "Définition")
        self.tabs.addTab(self._build_policies_tab(), "Politiques")
        self.tabs.addTab(self._build_instantiate_tab(), "Instanciation")
        layout.addWidget(self.tabs, 3)

    # -- Onglet 1 : définition --------------------------------------------------------

    def _build_definition_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        form = QFormLayout()
        self.id_edit = QLineEdit()
        form.addRow("Identifiant :", self.id_edit)
        self.name_edit = QLineEdit()
        form.addRow("Nom :", self.name_edit)
        self.kind_combo = QComboBox()
        for key, label in TEMPLATE_KINDS.items():
            self.kind_combo.addItem(label, key)
        form.addRow("Type :", self.kind_combo)
        self.desc_edit = QLineEdit()
        form.addRow("Description :", self.desc_edit)

        parent_row = QWidget()
        parent_layout = QHBoxLayout(parent_row)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        self.parent_combo = QComboBox()
        self.parent_combo.setMinimumWidth(300)
        parent_layout.addWidget(self.parent_combo)
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self._load_ous)
        parent_layout.addWidget(refresh)
        parent_layout.addStretch()
        form.addRow("OU parente :", parent_row)

        self.ou_pattern_edit = QLineEdit()
        self.ou_pattern_edit.setPlaceholderText(
            "Eleves-{classe}  —  vide = groupes créés dans l'OU parente"
        )
        form.addRow("Motif de sous-OU :", self.ou_pattern_edit)
        v.addLayout(form)

        v.addWidget(QLabel("Groupes auto-créés :"))
        self.groups_table = QTableWidget(0, 3)
        self.groups_table.setHorizontalHeaderLabels(
            ["Motif de nom", "Portée", "Description"]
        )
        header = self.groups_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.groups_table.setMinimumHeight(150)
        v.addWidget(self.groups_table)

        group_buttons = QHBoxLayout()
        add_group = QPushButton("Ajouter un groupe")
        add_group.clicked.connect(self._on_add_group)
        remove_group = QPushButton("Retirer la ligne")
        remove_group.clicked.connect(self._on_remove_group)
        group_buttons.addWidget(add_group)
        group_buttons.addWidget(remove_group)
        group_buttons.addStretch()
        v.addLayout(group_buttons)

        self.placeholders_label = QLabel("")
        self.placeholders_label.setStyleSheet("color: #888;")
        v.addWidget(self.placeholders_label)
        v.addStretch()
        return widget

    # -- Onglet 2 : politiques --------------------------------------------------------

    def _build_policies_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)

        # Quota FSRM (M14)
        self.quota_check = QCheckBox("Activer un quota FSRM (espaces + dossiers)")
        self.quota_size = QDoubleSpinBox()
        self.quota_size.setRange(0.5, 4096.0)
        self.quota_size.setSuffix(" Go")
        self.quota_size.setValue(5.0)
        self.quota_warn = QSpinBox()
        self.quota_warn.setRange(1, 99)
        self.quota_warn.setSuffix(" %")
        self.quota_warn.setValue(80)
        self.quota_hard = QCheckBox("Quota dur (blocage à l'atteinte)")
        quota_group = QGroupBox("Quota de disque (M14)")
        quota_form = QFormLayout(quota_group)
        quota_form.addRow(self.quota_check)
        quota_form.addRow("Taille :", self.quota_size)
        quota_form.addRow("Seuil d'alerte :", self.quota_warn)
        quota_form.addRow(self.quota_hard)
        v.addWidget(quota_group)

        # Profil (M13)
        self.profile_check = QCheckBox("Appliquer un profil aux comptes de l'OU")
        self.profile_type = QComboBox()
        for value, label in _PROFILE_LABELS.items():
            self.profile_type.addItem(label, value)
        self.profile_path = QLineEdit()
        self.profile_path.setPlaceholderText(r"\\srv\profils\%USERNAME%")
        profile_group = QGroupBox("Profil utilisateur (M13)")
        profile_form = QFormLayout(profile_group)
        profile_form.addRow(self.profile_check)
        profile_form.addRow("Type :", self.profile_type)
        profile_form.addRow("Chemin :", self.profile_path)
        v.addWidget(profile_group)

        # Dossier personnel (M17)
        self.home_check = QCheckBox("Configuration du dossier personnel")
        self.home_root = QLineEdit(r"\\srv\homes")
        self.home_drive = QLineEdit("H:")
        self.home_drive.setMaximumWidth(80)
        self.home_folder = QLineEdit("%USERNAME%")
        home_group = QGroupBox("Dossier personnel (M17)")
        home_form = QFormLayout(home_group)
        home_form.addRow(self.home_check)
        home_form.addRow("Partage :", self.home_root)
        home_form.addRow("Lecteur :", self.home_drive)
        home_form.addRow("Motif de dossier :", self.home_folder)
        v.addWidget(home_group)

        # Espace partagé (M18)
        self.space_check = QCheckBox("Créer un espace partagé par groupe")
        self.space_root = QLineEdit(r"\\srv\classes")
        self.space_folder = QLineEdit("%GROUP%")
        space_group = QGroupBox("Espace partagé de classe (M18)")
        space_form = QFormLayout(space_group)
        space_form.addRow(self.space_check)
        space_form.addRow("Partage :", self.space_root)
        space_form.addRow("Motif de sous-dossier :", self.space_folder)
        v.addWidget(space_group)

        # Heures de connexion (M16)
        self.hours_combo = QComboBox()
        self.hours_combo.addItem("Aucune restriction", "")
        for preset in LogonHoursPreset.builtin():
            self.hours_combo.addItem(preset.name, preset.name)
        hours_group = QGroupBox("Heures de connexion (M16)")
        hours_form = QFormLayout(hours_group)
        hours_form.addRow("Préréglage :", self.hours_combo)
        hours_form.addRow(
            QLabel("Consigné dans le rapport : à appliquer aux comptes depuis l'onglet M16.")
        )
        v.addWidget(hours_group)

        # Politique de mot de passe
        self.pwd_check = QCheckBox("Politique de génération des mots de passe")
        self.pwd_len = QSpinBox()
        self.pwd_len.setRange(6, 128)
        self.pwd_len.setValue(12)
        self.pwd_upper = QCheckBox("Majuscules")
        self.pwd_upper.setChecked(True)
        self.pwd_digits = QCheckBox("Chiffres")
        self.pwd_digits.setChecked(True)
        self.pwd_special = QCheckBox("Caractères spéciaux")
        flags = QHBoxLayout()
        flags.addWidget(self.pwd_upper)
        flags.addWidget(self.pwd_digits)
        flags.addWidget(self.pwd_special)
        flags.addStretch()
        pwd_group = QGroupBox("Politique de mot de passe")
        pwd_form = QFormLayout(pwd_group)
        pwd_form.addRow(self.pwd_check)
        pwd_form.addRow("Longueur :", self.pwd_len)
        pwd_form.addRow("Composition :", flags)
        v.addWidget(pwd_group)

        # Licences Microsoft 365 (M19)
        self.licenses_edit = QLineEdit()
        self.licenses_edit.setPlaceholderText("SKU 1, SKU 2, … (identifiants du tenant)")
        licenses_group = QGroupBox("Licences Microsoft 365 (M19)")
        licenses_form = QFormLayout(licenses_group)
        licenses_form.addRow("Skus :", self.licenses_edit)
        licenses_form.addRow(
            QLabel("Assignées via le module M365 — usage_location requis avant assignation.")
        )
        v.addWidget(licenses_group)

        # Script de connexion (M15)
        self.script_check = QCheckBox("Attacher un script de connexion")
        self.script_combo = QComboBox()
        self.script_combo.currentIndexChanged.connect(self._on_script_picked)
        self.script_name = QLineEdit()
        self.script_kind = QComboBox()
        for kind in SUPPORTED_KINDS:
            self.script_kind.addItem(kind, kind)
        self.script_timing = QComboBox()
        for timing in SUPPORTED_TIMINGS:
            self.script_timing.addItem(_TIMING_LABELS.get(timing, timing), timing)
        self.script_content = QTextEdit()
        self.script_content.setMinimumHeight(110)
        script_group = QGroupBox("Script de connexion (M15)")
        script_form = QFormLayout(script_group)
        script_form.addRow(self.script_check)
        script_form.addRow("Depuis un script enregistré :", self.script_combo)
        script_form.addRow("Nom :", self.script_name)
        script_form.addRow("Type :", self.script_kind)
        script_form.addRow("Moment :", self.script_timing)
        script_form.addRow("Contenu :", self.script_content)
        v.addWidget(script_group)

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    # -- Onglet 3 : instanciation ------------------------------------------------------

    def _build_instantiate_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        form = QFormLayout()
        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        self.inst_parent_combo = QComboBox()
        self.inst_parent_combo.setMinimumWidth(300)
        target_layout.addWidget(self.inst_parent_combo)
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self._load_ous)
        target_layout.addWidget(refresh)
        target_layout.addStretch()
        form.addRow("OU cible :", target_row)

        out_row = QWidget()
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText(
            "Dossier des scripts + rapport d'instanciation (vide = AD seul)"
        )
        out_layout.addWidget(self.out_edit)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._on_browse_out)
        out_layout.addWidget(browse)
        form.addRow("Dossier d'artefacts :", out_row)
        v.addLayout(form)

        self.values_form = QFormLayout()
        v.addLayout(self.values_form)

        self.register_check = QCheckBox(
            "Enregistrer profil / quota scopés sur l'OU créée (M13 / M14)"
        )
        self.register_check.setChecked(True)
        v.addWidget(self.register_check)
        self.home_global_check = QCheckBox(
            "Remplacer aussi la configuration globale du dossier personnel (M17)"
        )
        v.addWidget(self.home_global_check)

        run_row = QHBoxLayout()
        self.instantiate_btn = QPushButton("Instancier le modèle (1-clic)")
        self.instantiate_btn.clicked.connect(self._on_instantiate)
        run_row.addWidget(self.instantiate_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        v.addWidget(QLabel("Résultat :"))
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(140)
        v.addWidget(self.result_text, 1)
        return widget

    # -- Liste ------------------------------------------------------------------------

    def _refresh_list(self, select: int | None = None) -> None:
        self._loading = True
        self.list_widget.clear()
        for template in self._templates:
            self.list_widget.addItem(f"{template.name}  [{template.id}]")
        self._loading = False
        if select is not None and self._templates:
            self.list_widget.setCurrentRow(min(select, len(self._templates) - 1))
        elif not self._templates:
            self._current = None
            self._load_form(None)

    def _on_selection_changed(self, row: int) -> None:
        if self._loading or not (0 <= row < len(self._templates)):
            return
        if self._current is not None:
            self._collect()
        self._current = self._templates[row]
        self._load_form(self._current)

    # -- Formulaire --------------------------------------------------------------------

    def _ensure_combo_value(self, combo: QComboBox, dn: str) -> None:
        index = combo.findData(dn)
        if index < 0 and dn:
            combo.addItem(dn, dn)
            index = combo.findData(dn)
        if index < 0 and not dn and combo.count() > 0:
            index = 0  # valeur absente → racine du domaine proposée
        combo.setCurrentIndex(index)

    def _load_form(self, template: GroupTemplate | None) -> None:
        self._loading = True
        try:
            self.tabs.setEnabled(template is not None)
            self.id_edit.setText(template.id if template else "")
            self.name_edit.setText(template.name if template else "")
            self.desc_edit.setText(template.description if template else "")
            if template:
                index = self.kind_combo.findData(template.kind)
                self.kind_combo.setCurrentIndex(index if index >= 0 else self.kind_combo.count() - 1)
                self._ensure_combo_value(self.parent_combo, template.ou_parent_dn)
                self._ensure_combo_value(self.inst_parent_combo, template.ou_parent_dn)
                self.ou_pattern_edit.setText(template.ou_rdn_pattern)
            else:
                self.kind_combo.setCurrentIndex(self.kind_combo.count() - 1)
                self.parent_combo.setCurrentIndex(-1)
                self.inst_parent_combo.setCurrentIndex(-1)
                self.ou_pattern_edit.setText("")

            # Groupes
            self.groups_table.setRowCount(0)
            for spec in (template.groups if template else []):
                self._add_group_row(spec.name_pattern, spec.scope, spec.description)

            # Politiques
            quota = template.quota if template else None
            self.quota_check.setChecked(quota is not None)
            if quota:
                self.quota_size.setValue(quota.size_gb)
                self.quota_warn.setValue(quota.warning_percent)
                self.quota_hard.setChecked(quota.hard_limit)

            profile = template.profile if template else None
            self.profile_check.setChecked(profile is not None)
            if profile:
                value = profile.profile_type.value
                index = self.profile_type.findData(value)
                self.profile_type.setCurrentIndex(index if index >= 0 else 0)
                paths = {
                    "roaming": profile.roaming_path,
                    "mandatory": profile.mandatory_path,
                    "local": profile.local_path,
                }
                self.profile_path.setText(paths.get(value, ""))
            else:
                self.profile_path.setText("")

            home = template.home if template else None
            self.home_check.setChecked(home is not None)
            if home:
                self.home_root.setText(home.share_root)
                self.home_drive.setText(home.drive_letter)
                self.home_folder.setText(home.folder_template)

            space = template.space if template else None
            self.space_check.setChecked(space is not None)
            if space:
                self.space_root.setText(space.share_root)
                self.space_folder.setText(space.folder_template)

            preset = template.hours_preset if template else ""
            index = self.hours_combo.findData(preset)
            self.hours_combo.setCurrentIndex(index if index >= 0 else 0)

            policy = template.password_policy if template else None
            self.pwd_check.setChecked(policy is not None)
            if policy:
                self.pwd_len.setValue(policy.longueur)
                self.pwd_upper.setChecked(policy.majuscules)
                self.pwd_digits.setChecked(policy.chiffres)
                self.pwd_special.setChecked(policy.caracteres_speciaux)

            licenses = template.licenses if template else []
            self.licenses_edit.setText(", ".join(licenses))

            script = template.logon_script if template else None
            self.script_check.setChecked(script is not None)
            self.script_combo.setCurrentIndex(0)
            if script:
                self.script_name.setText(script.name)
                kind_index = self.script_kind.findData(script.kind)
                self.script_kind.setCurrentIndex(kind_index if kind_index >= 0 else 0)
                timing_index = self.script_timing.findData(script.timing)
                self.script_timing.setCurrentIndex(timing_index if timing_index >= 0 else 0)
                self.script_content.setPlainText(script.content)
                for pos, saved in enumerate(self._script_catalog, start=1):
                    if saved.to_dict() == script.to_dict():
                        self.script_combo.setCurrentIndex(pos)
                        break
            else:
                self.script_name.setText("")
                self.script_kind.setCurrentIndex(0)
                self.script_timing.setCurrentIndex(0)
                self.script_content.clear()

            # Champs d'instanciation dynamiques
            self._rebuild_values(template)
            self.result_text.clear()
            if template:
                fields = ", ".join(f"{{{name}}}" for name in template.placeholders())
                self.placeholders_label.setText(
                    f"Champs saisis à l'instanciation : {fields}" if fields
                    else "Aucun champ : motifs fixes (instanciation directe)."
                )
            else:
                self.placeholders_label.setText("")
        finally:
            self._loading = False

    def _rebuild_values(self, template: GroupTemplate | None) -> None:
        while self.values_form.rowCount():
            self.values_form.removeRow(0)
        self._value_edits = {}
        if template is None:
            return
        names = template.placeholders()
        if not names:
            self.values_form.addRow(QLabel("Ce modèle ne requiert aucune saisie."))
            return
        saved = self._values_by_id.get(template.id, {})
        for name in names:
            edit = QLineEdit(saved.get(name, ""))
            edit.setPlaceholderText(f"ex. : {name}")
            self.values_form.addRow(f"{name} :", edit)
            self._value_edits[name] = edit

    def _add_group_row(self, pattern: str, scope: str, description: str) -> None:
        row = self.groups_table.rowCount()
        self.groups_table.insertRow(row)
        self.groups_table.setCellWidget(row, 0, QLineEdit(pattern))
        scope_combo = QComboBox()
        for key, label in GROUP_SCOPES.items():
            scope_combo.addItem(label, key)
        index = scope_combo.findData(scope)
        scope_combo.setCurrentIndex(index if index >= 0 else 0)
        self.groups_table.setCellWidget(row, 1, scope_combo)
        self.groups_table.setCellWidget(row, 2, QLineEdit(description))

    def _on_add_group(self, checked: bool = False) -> None:
        self._add_group_row("{classe}", "global", "")

    def _on_remove_group(self, checked: bool = False) -> None:
        row = self.groups_table.currentRow()
        if row >= 0:
            self.groups_table.removeRow(row)

    def _table_groups(self) -> list[GroupSpec]:
        specs: list[GroupSpec] = []
        for row in range(self.groups_table.rowCount()):
            pattern = self.groups_table.cellWidget(row, 0)
            scope = self.groups_table.cellWidget(row, 1)
            desc = self.groups_table.cellWidget(row, 2)
            specs.append(
                GroupSpec(
                    pattern.text().strip() if pattern else "",
                    str(scope.currentData() or "global") if scope else "global",
                    desc.text().strip() if desc else "",
                )
            )
        return specs

    def _collect(self) -> GroupTemplate | None:
        template = self._current
        if template is None or self._loading:
            return template
        template.id = self.id_edit.text().strip()
        template.name = self.name_edit.text().strip()
        template.kind = str(self.kind_combo.currentData() or "custom")
        template.description = self.desc_edit.text().strip()
        template.ou_parent_dn = str(
            self.parent_combo.currentData() or self.parent_combo.currentText()
        ).strip()
        template.ou_rdn_pattern = self.ou_pattern_edit.text().strip()
        template.groups = self._table_groups()

        template.quota = (
            QuotaSettings(
                size_gb=float(self.quota_size.value()),
                warning_percent=int(self.quota_warn.value()),
                hard_limit=self.quota_hard.isChecked(),
            )
            if self.quota_check.isChecked()
            else None
        )

        if self.profile_check.isChecked():
            ptype = ProfileType(str(self.profile_type.currentData() or "local"))
            path = self.profile_path.text().strip()
            profile = ProfileConfig(profile_type=ptype)
            if ptype is ProfileType.ROAMING:
                profile.roaming_path = path
            elif ptype is ProfileType.MANDATORY:
                profile.mandatory_path = path
            else:
                profile.local_path = path
            template.profile = profile
        else:
            template.profile = None

        template.home = (
            HomeDirConfig(
                share_root=self.home_root.text().strip(),
                drive_letter=self.home_drive.text().strip(),
                folder_template=self.home_folder.text().strip(),
            )
            if self.home_check.isChecked()
            else None
        )
        template.space = (
            ClassSpaceConfig(
                share_root=self.space_root.text().strip(),
                folder_template=self.space_folder.text().strip(),
            )
            if self.space_check.isChecked()
            else None
        )
        template.hours_preset = str(self.hours_combo.currentData() or "")

        template.password_policy = (
            PasswordPolicy(
                longueur=int(self.pwd_len.value()),
                majuscules=self.pwd_upper.isChecked(),
                chiffres=self.pwd_digits.isChecked(),
                caracteres_speciaux=self.pwd_special.isChecked(),
            )
            if self.pwd_check.isChecked()
            else None
        )

        raw_licenses = self.licenses_edit.text().replace(";", ",")
        template.licenses = [s.strip() for s in raw_licenses.split(",") if s.strip()]

        script = None
        if self.script_check.isChecked() and self.script_name.text().strip():
            script = LogonScript(
                name=self.script_name.text().strip(),
                kind=str(self.script_kind.currentData() or "bat"),
                timing=str(self.script_timing.currentData() or "logon"),
                scope_type="default",
                content=self.script_content.toPlainText(),
            )
        template.logon_script = script
        return template

    # -- OU ----------------------------------------------------------------------------

    def _load_ous(self) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        for combo in (self.parent_combo, self.inst_parent_combo):
            previous = combo.currentData() or combo.currentText()
            combo.clear()
            combo.addItem(f"(racine du domaine) {self.ad_connection.domain}", base_dn)
            for dn, name in sorted(ous, key=lambda item: item[0]):
                combo.addItem(f"{name}  ({dn})", dn)
            index = combo.findData(previous)
            if index < 0 and previous and previous != base_dn:
                combo.addItem(previous, previous)
                index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)

    # -- Opérations sur la liste --------------------------------------------------------

    def _save_quiet(self) -> None:
        try:
            save_templates(self._templates)
        except OSError as exc:
            QMessageBox.critical(self, "Sauvegarde impossible", str(exc))

    def _on_new(self, checked: bool = False) -> None:
        self._collect()
        template = GroupTemplate(
            id=unique_template_id(self._templates, "modele"),
            name="Nouveau modèle",
            kind="custom",
            groups=[GroupSpec("{classe}", "global", "Groupe principal")],
        )
        self._templates.append(template)
        self._save_quiet()
        self._refresh_list(select=len(self._templates) - 1)

    def _on_duplicate(self, checked: bool = False) -> None:
        if self._current is None:
            return
        self._collect()
        clone = duplicate_template(self._templates, self._current, "")
        self._templates.append(clone)
        self._save_quiet()
        self._refresh_list(select=len(self._templates) - 1)
        self.audit_log.record(
            "duplication_modele", clone.id, "succes", self.session_id,
            detail=f"copie de {self._current.id}",
        )

    def _on_delete(self, checked: bool = False) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le modèle",
            f"Supprimer le modèle « {self._current.name} » ?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        index = self.list_widget.currentRow()
        removed = self._current
        self._templates.remove(removed)
        self._current = None
        self._save_quiet()
        self._refresh_list(
            select=min(index, len(self._templates) - 1) if self._templates else None
        )
        self.audit_log.record(
            "suppression_modele", removed.id, "succes", self.session_id
        )

    def _on_save(self, checked: bool = False) -> None:
        if self._current is None:
            return
        self._collect()
        errors = self._current.validate()
        if errors:
            QMessageBox.warning(self, "Modèle invalide", "\n".join(errors))
            return
        self._save_quiet()
        row = self.list_widget.currentRow()
        self._refresh_list(select=row if row >= 0 else None)
        self.audit_log.record(
            "enregistrement_modele", self._current.id, "succes", self.session_id
        )

    def _on_export(self, fmt: str, checked: bool = False) -> None:
        if self._current is None:
            return
        self._collect()
        errors = self._current.validate()
        if errors:
            QMessageBox.warning(self, "Modèle invalide", "\n".join(errors))
            return
        name_filter = "JSON (*.json)" if fmt == "json" else "XML (*.xml)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le modèle", f"{self._current.id}.{fmt}", name_filter
        )
        if not path:
            return
        if not path.lower().endswith(f".{fmt}"):
            path = f"{path}.{fmt}"
        try:
            if fmt == "xml":
                export_template_xml(self._current, Path(path))
            else:
                export_template(self._current, Path(path))
        except OSError as exc:
            QMessageBox.critical(self, "Export impossible", str(exc))
            return
        self.audit_log.record(
            "export_modele", self._current.id, "succes", self.session_id,
            detail=f"{fmt} → {path}",
        )
        QMessageBox.information(self, "Export terminé", f"Modèle exporté :\n{path}")

    def _on_import(self, checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer un modèle", "",
            "Modèles (*.json *.xml);;JSON (*.json);;XML (*.xml)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".xml"):
                template = import_template_xml(Path(path))
            else:
                template = import_template(Path(path))
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Import impossible", str(exc))
            return
        if any(existing.id == template.id for existing in self._templates):
            template.id = unique_template_id(self._templates, template.id)
        self._templates.append(template)
        self._save_quiet()
        self._refresh_list(select=len(self._templates) - 1)
        self.audit_log.record(
            "import_modele", template.id, "succes", self.session_id, detail=path
        )

    # -- Instanciation -------------------------------------------------------------------

    def _on_browse_out(self, checked: bool = False) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Dossier des artefacts")
        if directory:
            self.out_edit.setText(directory)

    def _on_script_picked(self, index: int) -> None:
        if self._loading or index <= 0:
            return
        saved = self._script_catalog[index - 1]
        self.script_name.setText(saved.name)
        kind_index = self.script_kind.findData(saved.kind)
        self.script_kind.setCurrentIndex(kind_index if kind_index >= 0 else 0)
        timing_index = self.script_timing.findData(saved.timing)
        self.script_timing.setCurrentIndex(timing_index if timing_index >= 0 else 0)
        self.script_content.setPlainText(saved.content)

    def _on_instantiate(self, checked: bool = False) -> None:
        if self._current is None:
            return
        self._collect()
        template = self._current
        errors = template.validate()
        if errors:
            QMessageBox.warning(self, "Modèle invalide", "\n".join(errors))
            return
        values = {
            name: edit.text().strip() for name, edit in self._value_edits.items()
        }
        missing = validate_values(template, values)
        if missing:
            QMessageBox.warning(self, "Valeurs manquantes", "\n".join(missing))
            return
        self._values_by_id[template.id] = dict(values)

        parent = str(
            self.inst_parent_combo.currentData() or self.inst_parent_combo.currentText()
        ).strip()
        out = self.out_edit.text().strip()
        try:
            result = instantiate(
                self.ad_connection,
                template,
                values,
                ou_parent_dn=parent or None,
                artifacts_dir=Path(out) if out else None,
                register_scoped=self.register_check.isChecked(),
                apply_home_globally=self.home_global_check.isChecked(),
            )
        except ValueError as exc:
            self.result_text.setPlainText(f"Validation impossible :\n{exc}")
            self.audit_log.record(
                "instanciation_modele", template.id, "echec", self.session_id,
                detail=str(exc),
            )
            QMessageBox.warning(self, "Instanciation impossible", str(exc))
            return
        except ADError as exc:
            self.result_text.setPlainText(f"Erreur AD :\n{exc}")
            self.audit_log.record(
                "instanciation_modele", template.id, "echec", self.session_id,
                detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur Active Directory", str(exc))
            return

        lines = [result.summary()]
        if result.notes:
            lines.append("")
            lines.append("Notes :")
            lines.extend(f"• {note}" for note in result.notes)
        if result.artifacts:
            lines.append("")
            lines.append("Artefacts :")
            lines.extend(f"• {path}" for path in result.artifacts)
        self.result_text.setPlainText("\n".join(lines))
        self.audit_log.record(
            "instanciation_modele", template.id, "succes", self.session_id,
            detail=(
                f"valeurs={values} ou={result.ou_dn or '-'} "
                f"groupes={len(result.groups)} artefacts={len(result.artifacts)}"
            ),
        )
        QMessageBox.information(self, "Instanciation terminée", result.summary())
