"""Module 15 — Scripts logon / logoff (GPO-like).

Éditeur de scripts (bat / ps1 / vbs) par OU ou groupe, variables
``%USERNAME%``, ``%FULLNAME%``, ``%OU%``, ``%GROUP%``, ``%EMAIL%``,
``%HOMEDIR%``. Déploiement : écriture sur NETLOGON + attribut AD ``scriptPath``
(logon) ; note de liaison GPO générée pour les scripts logoff.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
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
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.logon_scripts import (
    SCRIPT_VARIABLES,
    SUPPORTED_KINDS,
    LogonScript,
    LogonScriptManager,
    LogonScriptTemplate,
    build_context,
    deploy_scripts,
    gpo_logoff_note,
    load_logon_scripts,
    netlogon_path,
    render_script,
    save_logon_scripts,
    unknown_variables,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

PREVIEW_COLUMNS = ["Identifiant", "Script applicable", "Fichier scriptPath"]
COL_SAM, COL_SCRIPT, COL_FILE = range(3)


class LogonScriptsPage(QWidget):
    """Page M15 — éditeur et déploiement des scripts de session."""

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

        self._scripts: list[LogonScript] = load_logon_scripts()
        self._plans: list = []

        self._build_ui()
        self._refresh_script_list()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Éditeur
        editor_group = QGroupBox("1. Éditeur de script")
        form = QFormLayout(editor_group)

        head_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("nom du script")
        self.name_edit.setMaximumWidth(220)
        head_row.addWidget(self.name_edit)

        self.kind_combo = QComboBox()
        for kind in SUPPORTED_KINDS:
            self.kind_combo.addItem(kind.upper(), kind)
        head_row.addWidget(self.kind_combo)

        self.timing_combo = QComboBox()
        self.timing_combo.addItem("Connexion (logon)", "logon")
        self.timing_combo.addItem("Déconnexion (logoff)", "logoff")
        head_row.addWidget(self.timing_combo)

        head_row.addWidget(QLabel("Modèle :"))
        self.template_combo = QComboBox()
        self.template_combo.addItem("(vierge)", "")
        for tpl in LogonScriptTemplate.builtin():
            self.template_combo.addItem(tpl.name, tpl.name)
        head_row.addWidget(self.template_combo)
        template_btn = QPushButton("Charger")
        template_btn.clicked.connect(self._on_load_template)
        head_row.addWidget(template_btn)
        head_row.addStretch()
        form.addRow("", head_row)

        scope_row = QHBoxLayout()
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Tous les comptes (défaut)", "default")
        self.scope_combo.addItem("Une OU…", "ou")
        self.scope_combo.addItem("Un groupe…", "group")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(QLabel("Portée :"))
        scope_row.addWidget(self.scope_combo)

        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(300)
        self.ou_combo.setVisible(False)
        scope_row.addWidget(self.ou_combo)

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(300)
        self.group_combo.setVisible(False)
        scope_row.addWidget(self.group_combo)
        scope_row.addStretch()
        form.addRow("", scope_row)

        palette_row = QHBoxLayout()
        palette_row.addWidget(QLabel("Variables :"))
        for var, label in SCRIPT_VARIABLES.items():
            btn = QPushButton(var)
            btn.setToolTip(label)
            btn.clicked.connect(lambda _c, v=var: self._insert_variable(v))
            palette_row.addWidget(btn)
        palette_row.addStretch()
        form.addRow("", palette_row)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("@echo off\r\necho Bonjour %FULLNAME%")
        self.editor.setMinimumHeight(150)
        form.addRow(self.editor)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #c90;")
        self.warning_label.setWordWrap(True)
        form.addRow("", self.warning_label)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Enregistrer le script")
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        delete_btn = QPushButton("Supprimer")
        delete_btn.clicked.connect(self._on_delete)
        save_row.addWidget(delete_btn)
        save_row.addStretch()
        form.addRow("", save_row)

        # 2. Aperçu + déploiement
        preview_group = QGroupBox("2. Aperçu du rendu & déploiement")
        pv = QVBoxLayout(preview_group)

        pv_row = QHBoxLayout()
        pv_row.addWidget(QLabel("Compte de test :"))
        self.preview_user_combo = QComboBox()
        self.preview_user_combo.setMinimumWidth(260)
        pv_row.addWidget(self.preview_user_combo)
        preview_btn = QPushButton("Prévisualiser")
        preview_btn.clicked.connect(self._on_preview)
        pv_row.addWidget(preview_btn)

        deploy_btn = QPushButton("Déployer vers un dossier (NETLOGON)…")
        deploy_btn.clicked.connect(self._on_deploy)
        pv_row.addWidget(deploy_btn)

        self.gpo_btn = QPushButton("Instructions GPO (logoff)…")
        self.gpo_btn.clicked.connect(self._on_gpo_note)
        pv_row.addWidget(self.gpo_btn)
        pv_row.addStretch()
        pv.addLayout(pv_row)

        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setMaximumHeight(140)
        pv.addWidget(self.preview_edit)

        # 3. Application scriptPath
        apply_group = QGroupBox("3. Application aux comptes (attribut scriptPath)")
        av = QVBoxLayout(apply_group)

        av_row = QHBoxLayout()
        av_row.addWidget(QLabel("OU cible :"))
        self.apply_ou_combo = QComboBox()
        self.apply_ou_combo.setMinimumWidth(300)
        av_row.addWidget(self.apply_ou_combo)
        load_btn = QPushButton("Charger les comptes")
        load_btn.clicked.connect(self._on_load_accounts)
        av_row.addWidget(load_btn)
        av_row.addStretch()
        av.addLayout(av_row)

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setMaximumHeight(220)
        av.addWidget(self.preview_table)

        self.apply_btn = QPushButton("Appliquer scriptPath aux comptes listés")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply)
        av.addWidget(self.apply_btn)

        self.script_list = QListWidget()
        self.script_list.setMaximumHeight(110)
        self.script_list.currentRowChanged.connect(self._on_select_script)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(editor_group)
        layout.addWidget(preview_group)
        layout.addWidget(apply_group)
        layout.addWidget(QLabel("Scripts enregistrés :"))
        layout.addWidget(self.script_list)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

    # -- Portée / éditeur ---------------------------------------------------------

    def _on_scope_changed(self) -> None:
        scope = self.scope_combo.currentData()
        self.ou_combo.setVisible(scope == "ou")
        self.group_combo.setVisible(scope == "group")
        self.timing_combo.setEnabled(True)

    def _load_ous(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        for combo in (self.ou_combo, self.apply_ou_combo):
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
        self._load_preview_users()

    def _insert_variable(self, var: str) -> None:
        cursor = self.editor.textCursor()
        cursor.insertText(var)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _on_load_template(self) -> None:
        name = self.template_combo.currentData()
        if not name:
            self.editor.clear()
            return
        for tpl in LogonScriptTemplate.builtin():
            if tpl.name == name:
                self.kind_combo.setCurrentIndex(
                    self.kind_combo.findData(tpl.kind)
                )
                self.editor.setPlainText(tpl.content)
                if not self.name_edit.text().strip():
                    self.name_edit.setText(tpl.name)
                return

    def _current_script(self) -> LogonScript:
        scope = self.scope_combo.currentData()
        return LogonScript(
            name=self.name_edit.text().strip(),
            kind=self.kind_combo.currentData(),
            timing=self.timing_combo.currentData(),
            scope_type=scope,
            scope_dn=(
                self.ou_combo.currentData() if scope == "ou"
                else self.group_combo.currentData() if scope == "group"
                else ""
            ),
            content=self.editor.toPlainText(),
        )

    def _load_script_into_form(self, script: LogonScript) -> None:
        self.name_edit.setText(script.name)
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(script.kind))
        self.timing_combo.setCurrentIndex(self.timing_combo.findData(script.timing))
        self.scope_combo.setCurrentIndex(self.scope_combo.findData(script.scope_type))
        if script.scope_type == "ou":
            idx = self.ou_combo.findData(script.scope_dn)
            if idx >= 0:
                self.ou_combo.setCurrentIndex(idx)
        elif script.scope_type == "group":
            idx = self.group_combo.findData(script.scope_dn)
            if idx >= 0:
                self.group_combo.setCurrentIndex(idx)
        self.editor.setPlainText(script.content)

    # -- Liste / enregistrement -----------------------------------------------------

    def _refresh_script_list(self) -> None:
        self.script_list.blockSignals(True)
        self.script_list.clear()
        for script in self._scripts:
            scope = (
                "défaut" if script.scope_type == "default"
                else script.scope_dn.split(",")[0]
            )
            item = QListWidgetItem(
                f"{script.filename}  —  {script.timing}  —  portée {scope}"
            )
            self.script_list.addItem(item)
        self.script_list.blockSignals(False)

    def _on_select_script(self, row: int) -> None:
        if 0 <= row < len(self._scripts):
            self._load_script_into_form(self._scripts[row])

    def _on_save(self) -> None:
        script = self._current_script()
        errors = script.validate()
        if errors:
            QMessageBox.warning(self, "Script invalide", "\n".join(errors))
            return
        for i, existing in enumerate(self._scripts):
            if existing.filename == script.filename:
                self._scripts[i] = script
                break
        else:
            self._scripts.append(script)
        save_logon_scripts(self._scripts)
        self._refresh_script_list()
        self.audit_log.record(
            "enregistrement_script_session", script.name, "succes", self.session_id,
            detail=f"{script.timing} {script.kind} portee={script.scope_type}",
        )
        QMessageBox.information(self, "Enregistré", f"Script « {script.filename} » enregistré.")

    def _on_delete(self) -> None:
        script = self._current_script()
        if not script.name:
            return
        before = len(self._scripts)
        self._scripts = [s for s in self._scripts if s.filename != script.filename]
        if len(self._scripts) == before:
            QMessageBox.information(self, "Introuvable", "Ce script n'est pas enregistré.")
            return
        save_logon_scripts(self._scripts)
        self._refresh_script_list()
        self.audit_log.record(
            "suppression_script_session", script.name, "succes", self.session_id, detail=script.filename,
        )

    # -- Aperçu / déploiement -------------------------------------------------------

    def _load_preview_users(self) -> None:
        ou_dn = self.apply_ou_combo.currentData()
        if not ou_dn:
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError:
            users = []
        previous = self.preview_user_combo.currentData()
        self.preview_user_combo.clear()
        for user in sorted(users, key=lambda u: u.get("sam", "").lower()):
            self.preview_user_combo.addItem(
                f"{user.get('cn', '')}  ({user.get('sam', '')})", user["dn"]
            )
        idx = self.preview_user_combo.findData(previous)
        self.preview_user_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_preview(self) -> None:
        user_dn = self.preview_user_combo.currentData()
        if not user_dn:
            QMessageBox.warning(self, "Compte requis", "Sélectionnez un compte de test.")
            return
        script = self._current_script()
        try:
            attrs = self.ad_connection.get_user_attributes(user_dn)
            base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain or "")
            groups = self.ad_connection.search_user_groups(user_dn, base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        # get_user_attributes renvoie sAMAccountName : on normalise pour le contexte
        attrs["sam"] = attrs.get("sAMAccountName") or attrs.get("sam", "")
        context = build_context(attrs, attrs.get("dn", user_dn), groups,
                                self.ad_connection.domain or "")
        rendered = render_script(script.content, context)
        header = (
            f"--- {script.filename} (rendu pour {context['%USERNAME%']}) ---\n"
            f"Variables absentes du compte : "
            f"{', '.join(unknown_variables(script.content)) or 'aucune'}\n\n"
        )
        self.preview_edit.setPlainText(header + rendered)

    def _on_deploy(self) -> None:
        logon_scripts = [s for s in self._scripts if s.timing == "logon"]
        if not logon_scripts:
            QMessageBox.warning(self, "Aucun script", "Enregistrez d'abord un script.")
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de déploiement (NETLOGON)",
            str(Path.home()),
        )
        if not dest:
            return
        try:
            written = deploy_scripts(logon_scripts, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Échec de l'écriture : {exc}")
            return
        domain = self.ad_connection.domain or ""
        share = netlogon_path(domain) or "(domaine non connecté)"
        self.audit_log.record(
            "deploiement_scripts_session", "-", "succes", self.session_id,
            detail=f"{len(written)} fichier(s) → {dest}",
        )
        QMessageBox.information(
            self, "Déploiement effectué",
            f"{len(written)} script(s) écrit(s) dans « {dest} ».\n\n"
            f"Partage NETLOGON du domaine : {share}\n"
            "L'attribut scriptPath doit être relatif à ce partage.",
        )

    def _on_gpo_note(self) -> None:
        logoff = [s for s in self._scripts if s.timing == "logoff"]
        if not logoff:
            QMessageBox.information(
                self, "Aucun script logoff",
                "Enregistrez un script avec le moment « Déconnexion (logoff) ».",
            )
            return
        note = "\n\n".join(gpo_logoff_note(s) for s in logoff)
        self.preview_edit.setPlainText(note)
        QMessageBox.information(
            self, "Instructions GPO",
            "Instructions affichées dans l'aperçu — suivez la liaison GPO dans la GPMC.",
        )

    # -- Application scriptPath -------------------------------------------------------

    def _on_load_accounts(self) -> None:
        ou_dn = self.apply_ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain or "")
        manager = LogonScriptManager(self._scripts)
        groups_by_sam: dict[str, list[str]] = {}
        for user in users:
            try:
                groups_by_sam[user["sam"]] = self.ad_connection.search_user_groups(
                    user["dn"], base_dn
                )
            except ADError:
                groups_by_sam[user["sam"]] = []
        self._plans = manager.plans_for_users(users, ou_dn, groups_by_sam)

        self.preview_table.setRowCount(len(self._plans))
        for row, plan in enumerate(self._plans):
            self.preview_table.setItem(row, COL_SAM, QTableWidgetItem(plan.sam))
            self.preview_table.setItem(
                row, COL_SCRIPT,
                QTableWidgetItem(plan.script.name if plan.script else "— aucun —"),
            )
            self.preview_table.setItem(
                row, COL_FILE,
                QTableWidgetItem(plan.script_path_value or "(retrait)"),
            )
        self.apply_btn.setEnabled(bool(self._plans))
        self._load_preview_users()

    def _on_apply(self) -> None:
        if not self._plans:
            return
        plans = list(self._plans)
        ou_dn = self.apply_ou_combo.currentData() or ""
        labels = [p.sam for p in plans]
        self.apply_btn.setEnabled(False)

        def run_one(plan) -> None:
            if plan.script is None:
                return  # aucun script applicable : aucun attribut à écrire
            if plan.script_path_value:
                self.ad_connection.update_user_attribute(
                    plan.user_dn, "scriptPath", plan.script_path_value
                )
            else:
                self.ad_connection.clear_script_path(plan.user_dn)

        def on_result(position: int, success: bool, message: str) -> None:
            plan = plans[position]
            if plan.script is None:
                self.audit_log.record(
                    "application_script_session", plan.sam, "succes", self.session_id,
                    ou_source=ou_dn, detail="aucun script applicable — inchangé",
                )
                return
            self.audit_log.record(
                "application_script_session", plan.sam,
                "succes" if success else "echec",
                self.session_id, ou_source=ou_dn,
                detail=plan.script_path_value if success else message,
            )

        def on_finished() -> None:
            self.apply_btn.setEnabled(True)
            unchanged = sum(1 for p in plans if p.script is None)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(plans)} compte(s) traité(s)"
                f" ({unchanged} sans script applicable).",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Définition de scriptPath en cours…", plans, labels, run_one,
            on_item_result=on_result,
        )
