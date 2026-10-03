"""Module 18 — Espaces partagés par classe / groupe.

Configuration du partage (``\\\\srv\\\\classes\\\\%GROUP%``) et du classement
profs/élèves (groupe AD ou OU des profs), aperçu des membres avec rôle,
génération du script de création (dossier + partage SMB + droits NTFS) et de
synchronisation (membres AD ↔ droits partage), suivi des espaces en attente
créés automatiquement à la naissance d'un groupe de classe.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection, is_builtin_group_dn
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.class_spaces import (
    ClassSpaceConfig,
    ClassSpaceManager,
    ClassSpacePlan,
    creation_script,
    load_class_space_config,
    load_class_space_state,
    mark_synchronized,
    plan_sync,
    save_class_space_config,
    save_script,
    sync_script,
)
from edusync_ad.core.config import AppConfig

MEMBER_COLUMNS = ["Identifiant", "Nom complet", "Rôle"]
COL_SAM, COL_NOM, COL_ROLE = range(3)


class ClassSpacesPage(QWidget):
    """Page M18 — espaces partagés de classe."""

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

        self._cfg: ClassSpaceConfig = load_class_space_config()
        self._plan: ClassSpacePlan | None = None
        self._members: list[dict] = []

        self._build_ui()
        self._load_config_into_form()
        self._refresh_pending()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.group_combo.count() == 0:
            self._load_groups()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Configuration
        config_group = QGroupBox("1. Configuration")
        form = QFormLayout(config_group)

        self.root_edit = QLineEdit()
        self.root_edit.setPlaceholderText(r"ex : \\srv\classes")
        form.addRow("Partage racine :", self.root_edit)

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText(r"ex : %GROUP% ou Classes\%GROUP%")
        form.addRow("Modèle de dossier :", self.template_edit)

        self.teachers_group_combo = QComboBox()
        self.teachers_group_combo.addItem("(aucun)", "")
        self.teachers_group_combo.setMinimumWidth(340)
        form.addRow("Groupe AD des profs :", self.teachers_group_combo)

        self.teachers_ou_combo = QComboBox()
        self.teachers_ou_combo.addItem("(aucune)", "")
        self.teachers_ou_combo.setMinimumWidth(340)
        form.addRow("OU des profs :", self.teachers_ou_combo)

        self.auto_check = QCheckBox(
            "Créer l'espace automatiquement à la création d'un groupe de classe"
        )
        form.addRow("", self.auto_check)

        save_btn = QPushButton("Enregistrer la configuration")
        save_btn.clicked.connect(self._on_save_config)
        form.addRow("", save_btn)

        # 2. Espaces / membres
        spaces_group = QGroupBox("2. Groupe de classe & membres")
        sv = QVBoxLayout(spaces_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Groupe :"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(340)
        row.addWidget(self.group_combo)
        load_btn = QPushButton("Charger les membres")
        load_btn.clicked.connect(self._on_load_members)
        row.addWidget(load_btn)
        refresh_btn = QPushButton("Actualiser les groupes")
        refresh_btn.clicked.connect(self._load_groups)
        row.addWidget(refresh_btn)
        row.addStretch()
        sv.addLayout(row)

        self.path_label = QLabel("Chemin de l'espace : —")
        self.path_label.setStyleSheet("color: #888;")
        sv.addWidget(self.path_label)

        self.members_table = QTableWidget(0, len(MEMBER_COLUMNS))
        self.members_table.setHorizontalHeaderLabels(MEMBER_COLUMNS)
        self.members_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.members_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.members_table.setMaximumHeight(220)
        sv.addWidget(self.members_table)

        # 3. Actions
        actions_group = QGroupBox("3. Création & synchronisation")
        av = QVBoxLayout(actions_group)

        create_btn = QPushButton("Générer le script de création (.ps1)…")
        create_btn.clicked.connect(self._on_generate_creation)
        av.addWidget(create_btn)

        sync_row = QHBoxLayout()
        analyze_btn = QPushButton("Analyser la synchro (AD ↔ état enregistré)")
        analyze_btn.clicked.connect(self._on_analyze)
        sync_row.addWidget(analyze_btn)

        sync_btn = QPushButton("Générer le script de synchro (.ps1)…")
        sync_btn.clicked.connect(self._on_generate_sync)
        sync_row.addWidget(sync_btn)

        mark_btn = QPushButton("Marquer synchronisé")
        mark_btn.clicked.connect(self._on_mark_sync)
        sync_row.addWidget(mark_btn)
        sync_row.addStretch()
        av.addLayout(sync_row)

        self.diff_label = QLabel("")
        self.diff_label.setWordWrap(True)
        av.addWidget(self.diff_label)

        # Espaces en attente (création auto)
        av.addWidget(QLabel("Espaces en attente (création auto) :"))
        self.pending_list = QListWidget()
        self.pending_list.setMaximumHeight(110)
        av.addWidget(self.pending_list)

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(spaces_group)
        layout.addWidget(actions_group)
        layout.addStretch()

    def _load_config_into_form(self) -> None:
        self.root_edit.setText(self._cfg.share_root)
        self.template_edit.setText(self._cfg.folder_template)
        self.auto_check.setChecked(self._cfg.auto_on_group_create)

    def _load_groups(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        previous = self.group_combo.currentData()
        self.group_combo.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            if not is_builtin_group_dn(dn):
                self.group_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.group_combo.findData(previous)
        self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # Renseigne les combos « profs » (groupe + OU) depuis la config
        current_group = self._cfg.teachers_group
        current_ou = self._cfg.teachers_ou
        self.teachers_group_combo.clear()
        self.teachers_group_combo.addItem("(aucun)", "")
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            if not is_builtin_group_dn(dn):
                self.teachers_group_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.teachers_group_combo.findData(current_group)
        self.teachers_group_combo.setCurrentIndex(max(idx, 0))

        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError:
            ous = []
        self.teachers_ou_combo.clear()
        self.teachers_ou_combo.addItem("(aucune)", "")
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.teachers_ou_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.teachers_ou_combo.findData(current_ou)
        self.teachers_ou_combo.setCurrentIndex(max(idx, 0))

    def _refresh_pending(self) -> None:
        self.pending_list.clear()
        state = load_class_space_state()
        for entry in state.values():
            if not isinstance(entry, dict) or not entry.get("pending"):
                continue
            status = "dossier créé" if entry.get("folder_created") else "à déployer"
            self.pending_list.addItem(
                QListWidgetItem(f"{entry.get('group_name', '?')} → {entry.get('path', '')}  [{status}]")
            )
        if self.pending_list.count() == 0:
            self.pending_list.addItem(QListWidgetItem("(aucun espace en attente)"))

    # -- Configuration ----------------------------------------------------------

    def _read_form_config(self) -> ClassSpaceConfig | None:
        cfg = ClassSpaceConfig(
            share_root=self.root_edit.text().strip(),
            folder_template=self.template_edit.text().strip(),
            teachers_group=self.teachers_group_combo.currentData() or "",
            teachers_ou=self.teachers_ou_combo.currentData() or "",
            auto_on_group_create=self.auto_check.isChecked(),
        )
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return None
        return cfg

    def _on_save_config(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        self._cfg = cfg
        save_class_space_config(cfg)
        self.audit_log.record(
            "configuration_espace_classe", "-", "succes", self.session_id,
            detail=f"racine={cfg.share_root} modele={cfg.folder_template} "
                   f"profs_groupe={bool(cfg.teachers_group)} auto={cfg.auto_on_group_create}",
        )
        QMessageBox.information(self, "Enregistré", "Configuration des espaces enregistrée.")

    # -- Membres ------------------------------------------------------------------

    def _on_load_members(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        self._cfg = cfg

        group_dn = self.group_combo.currentData()
        if not group_dn:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        group_name = self.group_combo.currentText().split("  (")[0]
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain or "")
        try:
            members = self.ad_connection.list_users_in_group(group_dn, base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        manager = ClassSpaceManager(cfg, self.ad_connection)
        self._plan = manager.plan_for_group(group_dn, group_name, members)
        self._members = members
        self.path_label.setText(f"Chemin de l'espace : {self._plan.path}")

        self.members_table.setRowCount(len(members))
        for row, member in enumerate(members):
            role = manager.classify(member["dn"])
            self.members_table.setItem(row, COL_SAM, QTableWidgetItem(member["sam"]))
            self.members_table.setItem(row, COL_NOM, QTableWidgetItem(member["cn"]))
            role_item = QTableWidgetItem("Prof" if role == "prof" else "Élève")
            role_item.setForeground(
                Qt.GlobalColor.darkBlue if role == "prof" else Qt.GlobalColor.darkGreen
            )
            self.members_table.setItem(row, COL_ROLE, role_item)

        self.diff_label.setText("")

    # -- Actions --------------------------------------------------------------------

    def _require_plan(self) -> ClassSpacePlan | None:
        if self._plan is None:
            QMessageBox.warning(
                self, "Aucun groupe", "Chargez les membres d'un groupe de classe d'abord."
            )
        return self._plan

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
            QMessageBox.critical(self, "Erreur", f"Impossible d'écrire le script : {exc}")
            return
        self.audit_log.record(
            action, "-", "succes", self.session_id, detail=str(path),
        )
        QMessageBox.information(
            self, "Script généré",
            f"« {path} » — exécutez-le en admin sur le serveur de fichiers.",
        )

    def _on_generate_creation(self) -> None:
        if self._plan is None:
            # Pas de groupe sélectionné : propose les espaces en attente
            state = load_class_space_state()
            pending = [
                ClassSpacePlan(
                    group_dn=dn,
                    group_name=e.get("group_name", ""),
                    path=e.get("path", ""),
                )
                for dn, e in state.items()
                if isinstance(e, dict) and e.get("pending")
            ]
            if not pending:
                QMessageBox.warning(
                    self, "Rien à faire",
                    "Aucun groupe chargé ni espace en attente.",
                )
                return
            content = creation_script(
                pending, self.ad_connection.domain or "",
                teachers_group=self._cfg.teachers_group,
            )
            self._save_generated(content, "creation_espaces_m18.ps1",
                                 "generation_script_creation_espaces")
            return

        plan = self._require_plan()
        if plan is None:
            return
        content = creation_script(
            [plan], self.ad_connection.domain or "",
            teachers_group=self._cfg.teachers_group,
        )
        self._save_generated(content, f"creation_{plan.share_name}.ps1",
                             "generation_script_creation_espaces")

    def _on_analyze(self) -> None:
        plan = self._require_plan()
        if plan is None:
            return
        state = load_class_space_state()
        entry = state.get(plan.group_dn) or {}
        previous = entry.get("members", [])
        current = plan.members
        diff = plan_sync(previous, current)
        if not previous:
            self.diff_label.setText(
                f"Première synchronisation — {len(current)} membre(s) à accorder. "
                "Générez le script de création puis « Marquer synchronisé »."
            )
            return
        parts = []
        if diff["added"]:
            parts.append(f"+ {len(diff['added'])} : {', '.join(diff['added'])}")
        if diff["removed"]:
            parts.append(f"− {len(diff['removed'])} : {', '.join(diff['removed'])}")
        self.diff_label.setText(
            " ; ".join(parts) or f"Aucun écart — {len(current)} membre(s) inchangé(s)."
        )

    def _on_generate_sync(self) -> None:
        plan = self._require_plan()
        if plan is None:
            return
        content = sync_script(
            plan, self.ad_connection.domain or "",
            teachers_group=self._cfg.teachers_group,
            teachers_ou=self._cfg.teachers_ou,
        )
        self._save_generated(content, f"sync_{plan.share_name}.ps1",
                             "generation_script_synchro_espaces")

    def _on_mark_sync(self) -> None:
        plan = self._require_plan()
        if plan is None:
            return
        mark_synchronized(plan.group_dn, plan.members)
        self.audit_log.record(
            "synchronisation_espace_classe", plan.group_name, "succes", self.session_id,
            detail=f"{len(plan.members)} membre(s) → {plan.path}",
        )
        self.diff_label.setText(
            f"État enregistré : {len(plan.members)} membre(s) pour {plan.group_name}."
        )
        self._refresh_pending()
