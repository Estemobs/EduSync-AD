"""Module 13 — Profils utilisateurs (itinérant / local / obligatoire).

Configuration par OU ou groupe : chemin profil itinérant (``\\srv\\profil\\%USERNAME%``),
local, obligatoire (``.man``). Application en masse lors de la création ou
migration des comptes. Variables supportées : %USERNAME%, %SAM%, %OU%, %DOMAIN%.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.profiles import (
    ProfileConfig,
    ProfileManager,
    ProfileTemplate,
    ProfileType,
    dict_to_profile_config,
    load_all_profile_configs,
    profile_config_to_dict,
    save_all_profile_configs,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Type profil", "Chemin résolu", "État"]
COL_SAM, COL_NOM, COL_TYPE, COL_PATH, COL_ETAT = range(5)


class ProfileConfigDialog(QWidget):
    """Formulaire de configuration d'un profil (type + chemins)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        self.type_group = QButtonGroup(self)
        self.radio_local = QRadioButton("Local (défaut AD)")
        self.radio_roaming = QRadioButton("Itinérant (roaming)")
        self.radio_mandatory = QRadioButton("Obligatoire (.man)")
        self.type_group.addButton(self.radio_local, 0)
        self.type_group.addButton(self.radio_roaming, 1)
        self.type_group.addButton(self.radio_mandatory, 2)
        self.radio_local.setChecked(True)

        type_row = QHBoxLayout()
        type_row.addWidget(self.radio_local)
        type_row.addWidget(self.radio_roaming)
        type_row.addWidget(self.radio_mandatory)
        type_row.addStretch()

        self.roaming_edit = QLineEdit(r"\\srv\profil\%USERNAME%")
        self.roaming_edit.setPlaceholderText(r"ex : \\srv\profil\%USERNAME%")
        self.local_edit = QLineEdit(r"C:\Users\%USERNAME%")
        self.mandatory_edit = QLineEdit(r"\\srv\mandatory\%OU%.man")

        form.addRow("Type de profil :", type_row)
        form.addRow("Chemin itinérant :", self.roaming_edit)
        form.addRow("Chemin local :", self.local_edit)
        form.addRow("Chemin obligatoire :", self.mandatory_edit)
        layout.addLayout(form)

        help_lbl = QLabel(
            "Variables : %USERNAME% (nom complet), %SAM% (identifiant), "
            "%OU% (nom de l'OU), %DOMAIN% (domaine)"
        )
        help_lbl.setStyleSheet("color: #888; font-size: 11px;")
        help_lbl.setWordWrap(True)
        layout.addWidget(help_lbl)

        self.radio_local.toggled.connect(self._update_enabled)
        self.radio_roaming.toggled.connect(self._update_enabled)
        self.radio_mandatory.toggled.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        self.roaming_edit.setEnabled(self.radio_roaming.isChecked())
        self.local_edit.setEnabled(self.radio_local.isChecked())
        self.mandatory_edit.setEnabled(self.radio_mandatory.isChecked())

    def load_config(self, config: ProfileConfig) -> None:
        radios = {ProfileType.LOCAL: self.radio_local,
                  ProfileType.ROAMING: self.radio_roaming,
                  ProfileType.MANDATORY: self.radio_mandatory}
        radios[config.profile_type].setChecked(True)
        self.roaming_edit.setText(config.roaming_path)
        self.local_edit.setText(config.local_path)
        self.mandatory_edit.setText(config.mandatory_path)

    def get_config(self) -> ProfileConfig:
        if self.radio_roaming.isChecked():
            ptype = ProfileType.ROAMING
        elif self.radio_mandatory.isChecked():
            ptype = ProfileType.MANDATORY
        else:
            ptype = ProfileType.LOCAL
        return ProfileConfig(
            profile_type=ptype,
            roaming_path=self.roaming_edit.text().strip(),
            local_path=self.local_edit.text().strip(),
            mandatory_path=self.mandatory_edit.text().strip(),
        )


class ProfilesPage(QWidget):
    """Page M13 — configuration des profils + application en masse."""

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

        self._ou_configs: dict[str, ProfileConfig] = {}
        self._group_configs: dict[str, ProfileConfig] = {}
        self._default_config = ProfileConfig()
        self._preview_users: list[dict] = []

        self._load_configs()
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def _load_configs(self) -> None:
        try:
            self._ou_configs, self._group_configs, self._default_config = load_all_profile_configs()
        except Exception:
            self._ou_configs, self._group_configs, self._default_config = {}, {}, ProfileConfig()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Configuration
        config_group = QGroupBox("1. Configuration du profil")
        config_layout = QVBoxLayout(config_group)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Appliquer à :"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Valeur par défaut", "default")
        self.scope_combo.addItem("Une OU…", "ou")
        self.scope_combo.addItem("Un groupe…", "group")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(self.scope_combo)

        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(320)
        self.ou_combo.setVisible(False)
        scope_row.addWidget(self.ou_combo)

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(320)
        self.group_combo.setVisible(False)
        scope_row.addWidget(self.group_combo)

        self.template_combo = QComboBox()
        self.template_combo.addItem("(sans modèle)", "")
        for tpl in ProfileTemplate.builtin_templates():
            self.template_combo.addItem(tpl.name, tpl.name)
        self.template_combo.currentIndexChanged.connect(self._on_template_selected)
        scope_row.addWidget(QLabel("Modèle :"))
        scope_row.addWidget(self.template_combo)

        save_btn = QPushButton("Enregistrer cette configuration")
        save_btn.clicked.connect(self._on_save_config)
        scope_row.addWidget(save_btn)
        scope_row.addStretch()
        config_layout.addLayout(scope_row)

        self.profile_form = ProfileConfigDialog()
        config_layout.addWidget(self.profile_form)

        # Liste des configurations enregistrées
        config_layout.addWidget(QLabel("Configurations enregistrées :"))
        self.config_list = QListWidget()
        self.config_list.setMaximumHeight(120)
        config_layout.addWidget(self.config_list)

        # 2. Prévisualisation
        preview_group = QGroupBox("2. Prévisualisation (utilisateurs d'une OU)")
        preview_layout = QVBoxLayout(preview_group)

        preview_row = QHBoxLayout()
        self.preview_ou_combo = QComboBox()
        self.preview_ou_combo.setMinimumWidth(320)
        preview_row.addWidget(QLabel("OU :"))
        preview_row.addWidget(self.preview_ou_combo)
        preview_btn = QPushButton("Charger les comptes")
        preview_btn.clicked.connect(self._on_preview_clicked)
        preview_row.addWidget(preview_btn)
        preview_row.addStretch()
        preview_layout.addLayout(preview_row)

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        preview_layout.addWidget(self.preview_table)

        self.apply_btn = QPushButton("3. Appliquer les profils aux comptes listés")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        preview_layout.addWidget(self.apply_btn)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(preview_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

    def _on_scope_changed(self) -> None:
        scope = self.scope_combo.currentData()
        self.ou_combo.setVisible(scope == "ou")
        self.group_combo.setVisible(scope == "group")

    def _load_ous(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        for combo in (self.ou_combo, self.preview_ou_combo):
            previous = combo.currentData()
            combo.clear()
            for dn, name in sorted(ous, key=lambda x: x[0]):
                combo.addItem(f"{name}  ({dn})", dn)
            idx = combo.findData(previous)
            combo.setCurrentIndex(idx if idx >= 0 else 0)

        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError:
            groups = []
        from edusync_ad.core.ad.connection import is_builtin_group_dn
        self.group_combo.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            if not is_builtin_group_dn(dn):
                self.group_combo.addItem(f"{name}  ({dn})", dn)

    def _on_template_selected(self) -> None:
        tpl_name = self.template_combo.currentData()
        if not tpl_name:
            return
        for tpl in ProfileTemplate.builtin_templates():
            if tpl.name == tpl_name:
                self.profile_form.load_config(tpl.config)
                break

    # -- Enregistrement des configurations ------------------------------------

    def _current_scope_key(self) -> tuple[str, str] | None:
        scope = self.scope_combo.currentData()
        if scope == "default":
            return ("default", "")
        if scope == "ou":
            dn = self.ou_combo.currentData()
            return ("ou", dn) if dn else None
        dn = self.group_combo.currentData()
        return ("group", dn) if dn else None

    def _on_save_config(self) -> None:
        scope = self._current_scope_key()
        if scope is None:
            QMessageBox.warning(self, "Portée manquante", "Sélectionnez une OU ou un groupe.")
            return
        cfg = self.profile_form.get_config()

        if scope[0] == "default":
            self._default_config = cfg
            label = "Défaut"
        elif scope[0] == "ou":
            self._ou_configs[scope[1]] = cfg
            label = f"OU {scope[1].split(',')[0]}"
        else:
            self._group_configs[scope[1]] = cfg
            label = f"Groupe {scope[1].split(',')[0]}"

        save_all_profile_configs(self._ou_configs, self._group_configs, self._default_config)
        self._refresh_config_list()

        self.audit_log.record(
            "configuration_profil", label, "succes", self.session_id,
            detail=f"type={cfg.profile_type.value}",
        )
        QMessageBox.information(self, "Enregistré", f"Configuration enregistrée pour : {label}")

    def _refresh_config_list(self) -> None:
        self.config_list.clear()
        self.config_list.addItem(f"[Défaut] {self._default_config.profile_type.value}")
        for dn, cfg in self._ou_configs.items():
            self.config_list.addItem(f"[OU] {dn.split(',')[0]} → {cfg.profile_type.value}")
        for dn, cfg in self._group_configs.items():
            self.config_list.addItem(f"[Groupe] {dn.split(',')[0]} → {cfg.profile_type.value}")

    # -- Prévisualisation --------------------------------------------------------

    def _build_manager(self) -> ProfileManager:
        pm = ProfileManager(self.ad_connection)
        pm.set_default_config(self._default_config)
        for dn, cfg in self._ou_configs.items():
            pm.set_ou_config(dn, cfg)
        for dn, cfg in self._group_configs.items():
            pm.set_group_config(dn, cfg)
        return pm

    def _on_preview_clicked(self) -> None:
        ou_dn = self.preview_ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        domain = self.ad_connection.domain or ""
        pm = self._build_manager()

        self._preview_users = []
        self.preview_table.setRowCount(len(users))
        for row, user in enumerate(users):
            # Groupes de l'utilisateur (pour priorité groupe > OU)
            try:
                groups = self.ad_connection.search_user_groups(user["dn"], ADConnection.domain_to_base_dn(domain))
            except ADError:
                groups = []

            cfg = pm.get_config_for_user(user["dn"], user["cn"], user["sam"], ou_dn, domain, groups)
            path = cfg.get_profile_path(user["cn"], user["sam"], ou_dn, domain)

            entry = {"user": user, "config": cfg, "path": path, "ou_dn": ou_dn}
            self._preview_users.append(entry)

            self.preview_table.setItem(row, COL_SAM, QTableWidgetItem(user["sam"]))
            self.preview_table.setItem(row, COL_NOM, QTableWidgetItem(user["cn"]))
            self.preview_table.setItem(row, COL_TYPE, QTableWidgetItem(cfg.profile_type.value))
            self.preview_table.setItem(row, COL_PATH, QTableWidgetItem(path or "—"))
            etat = "OK" if path or cfg.profile_type == ProfileType.LOCAL else "Chemin manquant"
            item = QTableWidgetItem(etat)
            if etat != "OK":
                item.setForeground(Qt.GlobalColor.red)
            self.preview_table.setItem(row, COL_ETAT, item)

        self.apply_btn.setEnabled(bool(self._preview_users))

    # -- Application en masse ----------------------------------------------------

    def _on_apply_clicked(self) -> None:
        to_process = [e for e in self._preview_users if e["path"] or e["config"].profile_type == ProfileType.LOCAL]
        if not to_process:
            QMessageBox.warning(self, "Rien à faire", "Aucun compte applicable.")
            return

        labels = [e["user"]["sam"] for e in to_process]
        self.apply_btn.setEnabled(False)

        def run_one(entry: dict) -> None:
            user = entry["user"]
            changes: dict[str, str] = {}
            cfg: ProfileConfig = entry["config"]
            path: str = entry["path"]

            if cfg.profile_type == ProfileType.ROAMING and path:
                changes["profilePath"] = path
                changes["homeDrive"] = "H:"
                # Le dossier personnel partage généralement le partage profils
                home_dir = path.replace("\\profils\\", "\\homes\\").replace("\\profil\\", "\\homes\\")
                changes["homeDirectory"] = home_dir
            elif cfg.profile_type == ProfileType.MANDATORY and path:
                changes["profilePath"] = path
            elif cfg.profile_type == ProfileType.LOCAL and cfg.local_path:
                changes["homeDirectory"] = cfg.local_path
                changes["homeDrive"] = "H:"

            for attr, value in changes.items():
                self.ad_connection.update_user_attribute(user["dn"], attr, value)

        def on_result(position: int, success: bool, message: str) -> None:
            entry = to_process[position]
            user = entry["user"]
            if success:
                self.audit_log.record(
                    "application_profil", user["sam"], "succes", self.session_id,
                    ou_source=entry["ou_dn"],
                    detail=f"type={entry['config'].profile_type.value} chemin={entry['path']}",
                )
            else:
                self.audit_log.record(
                    "application_profil", user["sam"], "echec", self.session_id,
                    ou_source=entry["ou_dn"], detail=message,
                )
            row = self._preview_users.index(entry)
            item = self.preview_table.item(row, COL_ETAT)
            if item:
                item.setText("✓" if success else f"✗ {message}")
                item.setForeground(Qt.GlobalColor.darkGreen if success else Qt.GlobalColor.red)

        def on_finished() -> None:
            self.apply_btn.setEnabled(True)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(to_process)} profil(s) appliqué(s).",
            )

        self.progress_panel.finished.connect(on_finished, type=Qt.ConnectionType.SingleShotConnection)
        self.progress_panel.start(
            "Application des profils en cours…", to_process, labels, run_one, on_item_result=on_result,
        )
