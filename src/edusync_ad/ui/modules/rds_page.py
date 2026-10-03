"""Module 21 — RDS / Bureau à distance.

- Collections de sessions (crées/configurées côté Connection Broker, scripts
  idempotents) avec groupes AD autorisés (`-UserGroup`)
- Applications RemoteApp publiées par groupe AD (`-UserGroups`)
- Profils utilisateurs : UPD (disque par collection, inclus dans le script
  des collections) ou FSLogix (clés de registre sur chaque hôte)

Plans (collections + RemoteApp) persistés dans ``rds_plans.json``.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.exchange import save_script
from edusync_ad.core.rds import (
    PROFILE_MODES,
    CollectionPlan,
    RDSConfig,
    RemoteAppPlan,
    build_collection_plan,
    collection_script,
    fslogix_script,
    load_rds_config,
    load_rds_plans,
    make_alias,
    normalize_group_ref,
    remoteapp_script,
    save_rds_config,
    save_rds_plans,
    split_multi,
    validate_collection,
    validate_remoteapp,
)

COLL_COLUMNS = ["Nom", "Hébergeurs", "Groupes", "Description"]
APP_COLUMNS = ["Alias", "Nom affiché", "Fichier", "Collection", "Groupes"]


class RDSPage(QWidget):
    """Page M21 — RDS / Bureau à distance."""

    def __init__(
        self,
        config: AppConfig,
        audit_log: AuditLog,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.audit_log = audit_log
        self.session_id = session_id

        self._cfg: RDSConfig = load_rds_config()
        self._collections: list[CollectionPlan] = []
        self._remoteapps: list[RemoteAppPlan] = []

        self._build_ui()
        self._load_config_into_form()
        self._collections, self._remoteapps = load_rds_plans()
        self._populate_tables()
        self._refresh_collections_combo()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    # -- UI --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Configuration
        config_group = QGroupBox("1. Déploiement RDS")
        form = QFormLayout(config_group)

        self.broker_edit = QLineEdit()
        self.broker_edit.setPlaceholderText("ex : rdcb.lycee.local")
        form.addRow("Connection Broker :", self.broker_edit)

        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("NetBIOS, ex : LYCEE (pour LYCEE\\Profs)")
        form.addRow("Domaine :", self.domain_edit)

        self.mode_combo = QComboBox()
        for mode, label in PROFILE_MODES.items():
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Profils utilisateurs :", self.mode_combo)

        # UPD
        self.upd_widget = QWidget()
        upd_form = QFormLayout(self.upd_widget)
        upd_form.setContentsMargins(0, 0, 0, 0)
        self.upd_path_edit = QLineEdit()
        self.upd_path_edit.setPlaceholderText("\\\\sr01\\UPD$")
        upd_form.addRow("Partage UNC :", self.upd_path_edit)
        self.upd_size_edit = QLineEdit()
        self.upd_size_edit.setPlaceholderText("30")
        self.upd_size_edit.setFixedWidth(80)
        upd_form.addRow("Taille (Go) :", self.upd_size_edit)
        self.upd_include_edit = QLineEdit()
        self.upd_include_edit.setPlaceholderText("dossiers à inclure (« a; b »)")
        upd_form.addRow("Inclure :", self.upd_include_edit)
        self.upd_exclude_edit = QLineEdit()
        self.upd_exclude_edit.setPlaceholderText("dossiers à exclure (« a; b »)")
        upd_form.addRow("Exclure :", self.upd_exclude_edit)
        form.addRow("UPD :", self.upd_widget)

        # FSLogix
        self.fslogix_widget = QWidget()
        fslogix_form = QFormLayout(self.fslogix_widget)
        fslogix_form.setContentsMargins(0, 0, 0, 0)
        self.fx_locations_edit = QLineEdit()
        self.fx_locations_edit.setPlaceholderText("\\\\sr01\\FSLogix$; \\\\sr02\\FSLogix$")
        fslogix_form.addRow("VHDLocations :", self.fx_locations_edit)
        self.fx_size_edit = QLineEdit()
        self.fx_size_edit.setPlaceholderText("30000")
        self.fx_size_edit.setFixedWidth(90)
        fslogix_form.addRow("Taille (Mo) :", self.fx_size_edit)
        self.fx_flipflop_check = QCheckBox("Dossier profil = nom d'utilisateur (pas SID)")
        self.fx_flipflop_check.setChecked(True)
        fslogix_form.addRow("", self.fx_flipflop_check)
        form.addRow("FSLogix :", self.fslogix_widget)

        save_cfg_btn = QPushButton("Enregistrer la configuration")
        save_cfg_btn.clicked.connect(self._on_save_config)
        form.addRow("", save_cfg_btn)

        # 2. Collections
        collections_group = QGroupBox("2. Collections de sessions")
        cv = QVBoxLayout(collections_group)
        cform = QFormLayout()
        self.coll_name_edit = QLineEdit()
        self.coll_name_edit.setPlaceholderText("ex : Edu-Sciences")
        cform.addRow("Nom :", self.coll_name_edit)
        self.coll_desc_edit = QLineEdit()
        cform.addRow("Description :", self.coll_desc_edit)
        self.coll_hosts_edit = QLineEdit()
        self.coll_hosts_edit.setPlaceholderText("rdsh01.lycee.local; rdsh02.lycee.local")
        cform.addRow("Hébergeurs :", self.coll_hosts_edit)
        self.coll_groups_edit = QLineEdit()
        self.coll_groups_edit.setPlaceholderText("Profs; Direction (groupes autorisés)")
        cform.addRow("Groupes AD :", self.coll_groups_edit)
        cv.addLayout(cform)

        coll_btns = QHBoxLayout()
        add_coll_btn = QPushButton("Ajouter la collection")
        add_coll_btn.clicked.connect(self._on_add_collection)
        coll_btns.addWidget(add_coll_btn)
        del_coll_btn = QPushButton("Retirer la sélection")
        del_coll_btn.clicked.connect(self._on_remove_collection)
        coll_btns.addWidget(del_coll_btn)
        coll_btns.addStretch()
        cv.addLayout(coll_btns)

        self.coll_table = QTableWidget(0, len(COLL_COLUMNS))
        self.coll_table.setHorizontalHeaderLabels(COLL_COLUMNS)
        self.coll_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.coll_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.coll_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.coll_table.setMaximumHeight(140)
        cv.addWidget(self.coll_table)

        # 3. RemoteApp
        apps_group = QGroupBox("3. Applications RemoteApp")
        av = QVBoxLayout(apps_group)
        aform = QFormLayout()
        self.app_display_edit = QLineEdit()
        self.app_display_edit.setPlaceholderText("ex : Word 2021")
        self.app_display_edit.editingFinished.connect(self._on_display_edited)
        aform.addRow("Nom affiché :", self.app_display_edit)
        self.app_alias_edit = QLineEdit()
        self.app_alias_edit.setPlaceholderText("généré automatiquement")
        aform.addRow("Alias :", self.app_alias_edit)
        self.app_file_edit = QLineEdit()
        self.app_file_edit.setPlaceholderText("C:\\Program Files\\...\\app.exe")
        file_row = QHBoxLayout()
        file_row.addWidget(self.app_file_edit)
        browse_btn = QPushButton("Parcourir…")
        browse_btn.clicked.connect(self._on_browse_app)
        file_row.addWidget(browse_btn)
        aform.addRow("Exécutable :", file_row)
        self.app_collection_combo = QComboBox()
        aform.addRow("Collection :", self.app_collection_combo)
        self.app_groups_edit = QLineEdit()
        self.app_groups_edit.setPlaceholderText("vide = visible par tous ; ex : Profs")
        aform.addRow("Groupes AD :", self.app_groups_edit)
        self.app_folder_edit = QLineEdit()
        self.app_folder_edit.setPlaceholderText("dossier dans RD Web Access (optionnel)")
        aform.addRow("Dossier web :", self.app_folder_edit)
        av.addLayout(aform)

        app_btns = QHBoxLayout()
        add_app_btn = QPushButton("Ajouter la RemoteApp")
        add_app_btn.clicked.connect(self._on_add_remoteapp)
        app_btns.addWidget(add_app_btn)
        del_app_btn = QPushButton("Retirer la sélection")
        del_app_btn.clicked.connect(self._on_remove_remoteapp)
        app_btns.addWidget(del_app_btn)
        app_btns.addStretch()
        av.addLayout(app_btns)

        self.app_table = QTableWidget(0, len(APP_COLUMNS))
        self.app_table.setHorizontalHeaderLabels(APP_COLUMNS)
        self.app_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.app_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.app_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.app_table.setMaximumHeight(140)
        av.addWidget(self.app_table)

        # 4. Scripts
        scripts_group = QGroupBox("4. Scripts à exécuter sur Windows (module RemoteDesktop)")
        sv = QVBoxLayout(scripts_group)
        script_btns = QHBoxLayout()
        self.coll_script_btn = QPushButton("Script collections (.ps1)…")
        self.coll_script_btn.clicked.connect(self._on_generate_collections)
        script_btns.addWidget(self.coll_script_btn)
        app_script_btn = QPushButton("Script RemoteApps (.ps1)…")
        app_script_btn.clicked.connect(self._on_generate_remoteapps)
        script_btns.addWidget(app_script_btn)
        self.profile_script_btn = QPushButton("Script profils FSLogix (.ps1)…")
        self.profile_script_btn.clicked.connect(self._on_generate_profiles)
        script_btns.addWidget(self.profile_script_btn)
        script_btns.addStretch()
        sv.addLayout(script_btns)

        hint = QLabel(
            "Les scripts s'exécutent sur un serveur Windows disposant du module "
            "RemoteDesktop (broker ou admin RDS). L'UPD est un paramètre de "
            "collection : il est inclus dans le script des collections."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        sv.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(collections_group)
        layout.addWidget(apps_group)
        layout.addWidget(scripts_group)
        layout.addStretch()

    # -- Configuration -------------------------------------------------------------

    def _load_config_into_form(self) -> None:
        self.broker_edit.setText(self._cfg.broker)
        self.domain_edit.setText(self._cfg.domain)
        self.mode_combo.setCurrentIndex(max(self.mode_combo.findData(self._cfg.profile_mode), 0))
        self.upd_path_edit.setText(self._cfg.upd_path)
        self.upd_size_edit.setText(str(self._cfg.upd_size_gb))
        self.upd_include_edit.setText("; ".join(self._cfg.upd_include_paths))
        self.upd_exclude_edit.setText("; ".join(self._cfg.upd_exclude_paths))
        self.fx_locations_edit.setText("; ".join(self._cfg.fslogix_locations))
        self.fx_size_edit.setText(str(self._cfg.fslogix_size_mb))
        self.fx_flipflop_check.setChecked(self._cfg.fslogix_flipflop)
        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        mode = str(self.mode_combo.currentData() or "none")
        self.upd_widget.setVisible(mode == "upd")
        self.fslogix_widget.setVisible(mode == "fslogix")
        self.profile_script_btn.setEnabled(mode == "fslogix")
        self.profile_script_btn.setToolTip(
            "" if mode == "fslogix"
            else "UPD : inclus dans le script des collections · aucun mode : choisir un mode"
        )

    def _read_form_config(self, *, quiet: bool = False) -> RDSConfig | None:
        try:
            upd_size = int(self.upd_size_edit.text().strip() or "30")
            fx_size = int(self.fx_size_edit.text().strip() or "30000")
        except ValueError:
            if not quiet:
                QMessageBox.warning(self, "Configuration invalide", "Les tailles doivent être des nombres.")
            return None
        cfg = RDSConfig(
            broker=self.broker_edit.text().strip(),
            domain=self.domain_edit.text().strip(),
            profile_mode=str(self.mode_combo.currentData() or "none"),
            upd_path=self.upd_path_edit.text().strip(),
            upd_size_gb=upd_size,
            upd_include_paths=split_multi(self.upd_include_edit.text()),
            upd_exclude_paths=split_multi(self.upd_exclude_edit.text()),
            fslogix_locations=split_multi(self.fx_locations_edit.text()),
            fslogix_size_mb=fx_size,
            fslogix_flipflop=self.fx_flipflop_check.isChecked(),
        )
        errors = cfg.validate()
        if errors and not quiet:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return None
        return cfg

    def _current_config(self) -> RDSConfig:
        cfg = self._read_form_config(quiet=True) or self._cfg
        self._cfg = cfg
        return cfg

    def _on_save_config(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        try:
            save_rds_config(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self._cfg = cfg
        self.audit_log.record(
            "configuration_rds", "-", "succes", self.session_id,
            detail=f"broker={cfg.broker} profils={cfg.profile_mode}",
        )
        QMessageBox.information(self, "Enregistré", "Configuration RDS enregistrée.")

    # -- Collections ----------------------------------------------------------------

    def _on_add_collection(self) -> None:
        cfg = self._current_config()
        name = self.coll_name_edit.text().strip()
        hosts = split_multi(self.coll_hosts_edit.text())
        if not name:
            QMessageBox.warning(self, "Nom requis", "Indiquez le nom de la collection.")
            return
        if not hosts:
            QMessageBox.warning(
                self, "Hébergeurs requis",
                "Indiquez au moins un RD Session Host (séparateur « ; »).",
            )
            return
        if any(c.name.lower() == name.lower() for c in self._collections):
            QMessageBox.warning(self, "Doublon", f"La collection « {name} » existe déjà.")
            return
        plan = build_collection_plan(
            name, self.coll_desc_edit.text(), hosts,
            split_multi(self.coll_groups_edit.text()), cfg,
        )
        self._collections.append(plan)
        self._persist_plans()
        self._populate_tables()
        self._refresh_collections_combo()
        self.coll_name_edit.clear()
        self.coll_desc_edit.clear()
        self.coll_hosts_edit.clear()
        self.coll_groups_edit.clear()
        self.audit_log.record(
            "planification_collection_rds", plan.name, "succes", self.session_id,
            detail=plan.label,
        )

    def _on_remove_collection(self) -> None:
        rows = sorted({index.row() for index in self.coll_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        removed = []
        for row in rows:
            removed.append(self._collections[row].name)
            del self._collections[row]
        # les RemoteApp d'une collection retirée le sont aussi
        self._remoteapps = [
            a for a in self._remoteapps if a.collection not in removed
        ]
        self._persist_plans()
        self._populate_tables()
        self._refresh_collections_combo()
        self.audit_log.record(
            "retrait_collection_rds", ",".join(removed), "succes", self.session_id,
        )

    def _populate_tables(self) -> None:
        self.coll_table.setRowCount(len(self._collections))
        for row, plan in enumerate(self._collections):
            self.coll_table.setItem(row, 0, QTableWidgetItem(plan.name))
            self.coll_table.setItem(row, 1, QTableWidgetItem("; ".join(plan.session_hosts)))
            self.coll_table.setItem(row, 2, QTableWidgetItem("; ".join(plan.user_groups)))
            self.coll_table.setItem(row, 3, QTableWidgetItem(plan.description))

        self.app_table.setRowCount(len(self._remoteapps))
        for row, app in enumerate(self._remoteapps):
            self.app_table.setItem(row, 0, QTableWidgetItem(app.alias))
            self.app_table.setItem(row, 1, QTableWidgetItem(app.display_name))
            self.app_table.setItem(row, 2, QTableWidgetItem(app.file_path))
            self.app_table.setItem(row, 3, QTableWidgetItem(app.collection))
            self.app_table.setItem(row, 4, QTableWidgetItem("; ".join(app.user_groups)))

    def _refresh_collections_combo(self) -> None:
        previous = self.app_collection_combo.currentText()
        self.app_collection_combo.clear()
        self.app_collection_combo.addItems([c.name for c in self._collections])
        idx = self.app_collection_combo.findText(previous)
        self.app_collection_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _persist_plans(self) -> None:
        try:
            save_rds_plans(self._collections, self._remoteapps)
        except OSError:
            pass  # la persistance est un confort, pas bloquant

    # -- RemoteApp ------------------------------------------------------------------

    def _on_display_edited(self) -> None:
        display = self.app_display_edit.text()
        suggestion = make_alias(display)
        current = self.app_alias_edit.text().strip()
        if not current or current == make_alias(self._last_display):
            self.app_alias_edit.setText(suggestion)
        self._last_display = display

    def _on_browse_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir l'exécutable", "",
            "Programmes Windows (*.exe *.msi);;Tous les fichiers (*)",
        )
        if path:
            self.app_file_edit.setText(path)

    def _on_add_remoteapp(self) -> None:
        if not self._collections:
            QMessageBox.warning(
                self, "Aucune collection",
                "Ajoutez d'abord une collection (section 2).",
            )
            return
        alias = self.app_alias_edit.text().strip() or make_alias(self.app_display_edit.text())
        plan = RemoteAppPlan(
            alias=alias,
            display_name=self.app_display_edit.text().strip(),
            file_path=self.app_file_edit.text().strip(),
            collection=self.app_collection_combo.currentText(),
            user_groups=split_multi(self.app_groups_edit.text()),
            folder_name=self.app_folder_edit.text().strip(),
        )
        # groupes normalisés avec le domaine
        cfg = self._current_config()
        plan.user_groups = [normalize_group_ref(g, cfg.domain) for g in plan.user_groups]
        errors = validate_remoteapp(plan)
        if errors:
            QMessageBox.warning(self, "RemoteApp invalide", "\n".join(errors))
            return
        if any(a.alias.lower() == plan.alias.lower() for a in self._remoteapps):
            QMessageBox.warning(self, "Doublon", f"L'alias « {plan.alias} » existe déjà.")
            return
        self._remoteapps.append(plan)
        self._persist_plans()
        self._populate_tables()
        self.app_display_edit.clear()
        self.app_alias_edit.clear()
        self.app_file_edit.clear()
        self.app_groups_edit.clear()
        self.app_folder_edit.clear()
        self.audit_log.record(
            "planification_remoteapp", plan.alias, "succes", self.session_id,
            detail=plan.label,
        )

    def _on_remove_remoteapp(self) -> None:
        rows = sorted({index.row() for index in self.app_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            del self._remoteapps[row]
        self._persist_plans()
        self._populate_tables()
        self.audit_log.record("retrait_remoteapp", "-", "succes", self.session_id)

    # -- Génération des scripts ------------------------------------------------------

    def _save_generated(self, content: str, default_name: str, action: str) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le script", default_name,
            "Scripts PowerShell (*.ps1);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            path = save_script(content, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record(action, "-", "succes", self.session_id, detail=str(path))
        QMessageBox.information(self, "Script généré", f"« {path} »")

    def _on_generate_collections(self) -> None:
        if not self._collections:
            QMessageBox.warning(self, "Aucune collection", "Ajoutez une collection d'abord (section 2).")
            return
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        try:
            content = collection_script(self._collections, cfg)
        except ValueError as exc:
            QMessageBox.warning(self, "Collections invalides", str(exc))
            return
        self._save_generated(content, "rds_collections_m21.ps1", "generation_script_collections_rds")

    def _on_generate_remoteapps(self) -> None:
        if not self._remoteapps:
            QMessageBox.warning(self, "Aucune RemoteApp", "Ajoutez une application d'abord (section 3).")
            return
        cfg = self._current_config()
        if not cfg.broker:
            QMessageBox.warning(self, "Configuration invalide", "Le Connection Broker est vide.")
            return
        try:
            content = remoteapp_script(self._remoteapps, cfg)
        except ValueError as exc:
            QMessageBox.warning(self, "RemoteApps invalides", str(exc))
            return
        self._save_generated(content, "rds_remoteapps_m21.ps1", "generation_script_remoteapp_rds")

    def _on_generate_profiles(self) -> None:
        cfg = self._current_config()
        mode = cfg.profile_mode
        if mode == "upd":
            QMessageBox.information(
                self, "UPD",
                "L'UPD est un paramètre de collection : il est inclus dans le "
                "script des collections (bouton « Script collections »).",
            )
            return
        if mode != "fslogix":
            QMessageBox.warning(
                self, "Aucun mode", "Choisissez un mode de profil (UPD ou FSLogix) d'abord.",
            )
            return
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        try:
            content = fslogix_script(cfg)
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration invalide", str(exc))
            return
        self._save_generated(content, "rds_fslogix_m21.ps1", "generation_script_fslogix")

    # attributs d'état pour l'auto-alias
    _last_display: str = ""
