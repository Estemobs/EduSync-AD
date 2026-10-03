"""Module 20 — Microsoft Exchange (on-prem / Online).

- Boîtes aux lettres utilisateurs **et groupes de messagerie** : plans SMTP
  principal + aliases selon un motif (prenom.nom…)
- Écriture directe des adresses dans l'AD (`mail` + `proxyAddresses`) —
  exécutable partout, utilisable en scénario hybride
- Scripts PowerShell Exchange : création/activation de boîtes, aliases,
  quotas, archivage, rétention (mode sur site ou Exchange Online app-only)
- Politique d'adresses (EAP, on-prem) + rapport CSV des adresses
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
from edusync_ad.core.config import AppConfig
from edusync_ad.core.exchange import (
    ALIAS_PATTERNS,
    MODES,
    ExchangeConfig,
    ExchangeManager,
    MailboxPlan,
    address_report_csv,
    build_group_address_plans,
    email_policy_script,
    load_exchange_config,
    onprem_script,
    online_script,
    save_exchange_config,
    save_script,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

BOX_COLUMNS = ["Identifiant", "Type", "SMTP principal", "Aliases", "État"]
COL_ID, COL_KIND, COL_SMTP, COL_ALIAS, COL_STATE = range(5)


class ExchangePage(QWidget):
    """Page M20 — Microsoft Exchange."""

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

        self._cfg: ExchangeConfig = load_exchange_config()
        self._plans: list[MailboxPlan] = []

        self._build_ui()
        self._load_config_into_form()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Configuration
        config_group = QGroupBox("1. Configuration Exchange")
        form = QFormLayout(config_group)

        self.mode_combo = QComboBox()
        for mode, label in MODES.items():
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Mode :", self.mode_combo)

        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("ex : lycee.fr")
        form.addRow("Domaine SMTP :", self.domain_edit)

        self.pattern_combo = QComboBox()
        for pattern, label in ALIAS_PATTERNS.items():
            self.pattern_combo.addItem(f"{label}  [{pattern}]", pattern)
        form.addRow("Motif d'alias :", self.pattern_combo)

        quota_row = QHBoxLayout()
        self.quota_edit = QLineEdit()
        self.quota_edit.setPlaceholderText("10GB")
        self.quota_edit.setFixedWidth(90)
        quota_row.addWidget(self.quota_edit)
        self.archive_check = QCheckBox("Archivage")
        quota_row.addWidget(self.archive_check)
        quota_row.addWidget(QLabel("Rétention :"))
        self.retention_edit = QLineEdit()
        self.retention_edit.setPlaceholderText("ex : EduSync - 3 ans (vide = aucune)")
        self.retention_edit.setMinimumWidth(220)
        quota_row.addWidget(self.retention_edit)
        quota_row.addStretch()
        form.addRow("Quota boîte :", quota_row)

        self.onprem_widget = QWidget()
        onprem_row = QHBoxLayout(self.onprem_widget)
        onprem_row.setContentsMargins(0, 0, 0, 0)
        onprem_row.addWidget(QLabel("Serveur :"))
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("exch01.lycee.local")
        onprem_row.addWidget(self.server_edit)
        onprem_row.addWidget(QLabel("Base :"))
        self.database_edit = QLineEdit()
        self.database_edit.setPlaceholderText("(défaut)")
        self.database_edit.setFixedWidth(140)
        onprem_row.addWidget(self.database_edit)
        onprem_row.addStretch()
        form.addRow("Exchange sur site :", self.onprem_widget)

        self.online_widget = QWidget()
        online_form = QFormLayout(self.online_widget)
        online_form.setContentsMargins(0, 0, 0, 0)
        self.tenant_edit = QLineEdit()
        online_form.addRow("Tenant ID :", self.tenant_edit)
        self.client_edit = QLineEdit()
        online_form.addRow("Client ID :", self.client_edit)
        self.thumbprint_edit = QLineEdit()
        online_form.addRow("Empreinte certificat :", self.thumbprint_edit)
        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText("ex : lycee.onmicrosoft.com")
        online_form.addRow("Organisation :", self.org_edit)
        form.addRow("Exchange Online :", self.online_widget)

        save_btn = QPushButton("Enregistrer la configuration")
        save_btn.clicked.connect(self._on_save_config)
        form.addRow("", save_btn)

        # 2. Source
        source_group = QGroupBox("2. Objets à traiter")
        sv = QVBoxLayout(source_group)

        ou_row = QHBoxLayout()
        ou_row.addWidget(QLabel("OU :"))
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(280)
        ou_row.addWidget(self.ou_combo)
        refresh_ous = QPushButton("Actualiser")
        refresh_ous.clicked.connect(self._load_ous)
        ou_row.addWidget(refresh_ous)
        self.sub_ous_check = QCheckBox("Sous-OU")
        self.sub_ous_check.setChecked(True)
        ou_row.addWidget(self.sub_ous_check)
        users_btn = QPushButton("Charger les utilisateurs")
        users_btn.clicked.connect(self._on_load_users)
        ou_row.addWidget(users_btn)
        groups_btn = QPushButton("Charger les groupes")
        groups_btn.clicked.connect(self._on_load_groups)
        ou_row.addWidget(groups_btn)
        ou_row.addStretch()
        sv.addLayout(ou_row)

        self.table = QTableWidget(0, len(BOX_COLUMNS))
        self.table.setHorizontalHeaderLabels(BOX_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMaximumHeight(230)
        sv.addWidget(self.table)

        self.info_label = QLabel("Aucun objet chargé.")
        self.info_label.setStyleSheet("color: #888;")
        sv.addWidget(self.info_label)

        # 3. Actions
        actions_group = QGroupBox("3. Adresses & scripts")
        av = QVBoxLayout(actions_group)

        apply_btn = QPushButton("Appliquer les adresses dans l'AD (mail + proxyAddresses)")
        apply_btn.clicked.connect(self._on_apply_ad)
        av.addWidget(apply_btn)

        scripts_row = QHBoxLayout()
        self.script_btn = QPushButton("Générer le script Exchange (.ps1)…")
        self.script_btn.clicked.connect(self._on_generate_script)
        scripts_row.addWidget(self.script_btn)
        policy_btn = QPushButton("Politique d'adresses (.ps1)…")
        policy_btn.clicked.connect(self._on_generate_policy)
        scripts_row.addWidget(policy_btn)
        report_btn = QPushButton("Rapport adresses (.csv)…")
        report_btn.clicked.connect(self._on_export_report)
        scripts_row.addWidget(report_btn)
        scripts_row.addStretch()
        av.addLayout(scripts_row)

        hint = QLabel(
            "Les scripts s'exécutent côté Exchange (sur site : Remote PowerShell ; "
            "online : module ExchangeOnlineManagement, app-only). L'écriture LDAP "
            "fonctionne depuis n'importe quelle machine connectée à l'AD."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        av.addWidget(hint)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(source_group)
        layout.addWidget(actions_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

    # -- Configuration ------------------------------------------------------------

    def _load_config_into_form(self) -> None:
        self.mode_combo.setCurrentIndex(max(self.mode_combo.findData(self._cfg.mode), 0))
        self.domain_edit.setText(self._cfg.smtp_domain)
        idx = self.pattern_combo.findData(self._cfg.alias_pattern)
        self.pattern_combo.setCurrentIndex(max(idx, 0))
        self.quota_edit.setText(self._cfg.mailbox_quota)
        self.archive_check.setChecked(self._cfg.archive_enabled)
        self.retention_edit.setText(self._cfg.retention_policy)
        self.server_edit.setText(self._cfg.exchange_server)
        self.database_edit.setText(self._cfg.database)
        self.tenant_edit.setText(self._cfg.tenant_id)
        self.client_edit.setText(self._cfg.client_id)
        self.thumbprint_edit.setText(self._cfg.certificate_thumbprint)
        self.org_edit.setText(self._cfg.organization)
        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        onprem = self.mode_combo.currentData() == "onprem"
        self.onprem_widget.setVisible(onprem)
        self.online_widget.setVisible(not onprem)
        self.script_btn.setText(
            "Générer le script Exchange on-prem (.ps1)…" if onprem
            else "Générer le script Exchange Online (.ps1)…"
        )

    def _read_form_config(self, *, quiet: bool = False) -> ExchangeConfig | None:
        cfg = ExchangeConfig(
            mode=str(self.mode_combo.currentData() or "onprem"),
            smtp_domain=self.domain_edit.text().strip(),
            alias_pattern=str(self.pattern_combo.currentData() or "{prenom}.{nom}"),
            mailbox_quota=self.quota_edit.text().strip() or "10GB",
            archive_enabled=self.archive_check.isChecked(),
            retention_policy=self.retention_edit.text().strip(),
            exchange_server=self.server_edit.text().strip(),
            database=self.database_edit.text().strip(),
            tenant_id=self.tenant_edit.text().strip(),
            client_id=self.client_edit.text().strip(),
            certificate_thumbprint=self.thumbprint_edit.text().strip(),
            organization=self.org_edit.text().strip(),
        )
        errors = cfg.validate()
        if errors and not quiet:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return None
        return cfg

    def _on_save_config(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        try:
            save_exchange_config(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self._cfg = cfg
        self.audit_log.record(
            "configuration_exchange", "-", "succes", self.session_id,
            detail=f"mode={cfg.mode} domaine={cfg.smtp_domain} quota={cfg.mailbox_quota}",
        )
        QMessageBox.information(self, "Enregistré", "Configuration Exchange enregistrée.")

    # -- Source AD ------------------------------------------------------------------

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
        idx = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _current_config(self) -> ExchangeConfig:
        cfg = self._read_form_config(quiet=True) or self._cfg
        self._cfg = cfg
        return cfg

    def _on_load_users(self) -> None:
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Aucune OU", "Sélectionnez une OU d'abord.")
            return
        try:
            if self.sub_ous_check.isChecked():
                users = self.ad_connection.list_users_in_ou(ou_dn)
            else:
                users = [
                    u for u in self.ad_connection.list_ou_contents(ou_dn)
                    if u.get("kind", "user") == "user"
                ]
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        domain = self.ad_connection.domain or ""
        self._plans = ExchangeManager(cfg).build_plans(users, domain)
        self._populate_table()
        self.info_label.setText(f"{len(self._plans)} boîte(s) planifiée(s).")
        self.audit_log.record(
            "analyse_boites_exchange", "-", "succes", self.session_id,
            detail=f"{len(self._plans)} utilisateur(s)",
        )

    def _on_load_groups(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous à l'AD d'abord.")
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = [
                (dn, name) for dn, name in self.ad_connection.list_groups(base_dn)
                if not is_builtin_group_dn(dn)
            ]
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        self._plans = build_group_address_plans(groups, cfg)
        self._populate_table()
        self.info_label.setText(f"{len(self._plans)} groupe(s) de messagerie planifié(s).")
        self.audit_log.record(
            "analyse_groupes_messagerie", "-", "succes", self.session_id,
            detail=f"{len(self._plans)} groupe(s)",
        )

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._plans))
        for row, plan in enumerate(self._plans):
            kind = "Groupe" if plan.kind == "group" else "Boîte"
            self.table.setItem(row, COL_ID, QTableWidgetItem(plan.sam or plan.cn))
            self.table.setItem(row, COL_KIND, QTableWidgetItem(kind))
            self.table.setItem(row, COL_SMTP, QTableWidgetItem(plan.primary))
            self.table.setItem(
                row, COL_ALIAS, QTableWidgetItem(" | ".join(plan.aliases))
            )
            self.table.setItem(row, COL_STATE, QTableWidgetItem(""))

    def _require_plans(self) -> bool:
        if not self._plans:
            QMessageBox.warning(
                self, "Aucun objet",
                "Chargez les utilisateurs ou les groupes (section 2) d'abord.",
            )
            return False
        return True

    # -- Actions ---------------------------------------------------------------------

    def _on_apply_ad(self) -> None:
        if not self._require_plans():
            return
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        manager = ExchangeManager(cfg, self.ad_connection)
        plans = list(self._plans)
        states: dict[str, object] = {}

        def run_one(plan) -> None:
            result = manager.apply_ad_addresses([plan])[0]
            states[plan.sam or plan.cn] = result
            if not result.ok:
                raise ADError(result.detail)

        def on_result(index: int, success: bool, message: str) -> None:
            plan = plans[index]
            result = states.get(plan.sam or plan.cn)
            detail = (result.detail if result else message) or ""
            self.table.setItem(
                index, COL_STATE,
                QTableWidgetItem(f"✓ {detail}" if success else f"✗ {detail}"),
            )
            self.audit_log.record(
                "adresses_messagerie_ad",
                plan.sam or plan.cn,
                "succes" if success else "echec",
                self.session_id, detail=detail,
            )

        def on_finished() -> None:
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(plans)} objet(s) traité(s).",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Écriture des adresses dans l'AD…", plans,
            [p.label for p in plans], run_one, on_item_result=on_result,
        )

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

    def _on_generate_script(self) -> None:
        if not self._require_plans():
            return
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        if cfg.mode == "onprem":
            content = onprem_script(self._plans, cfg)
            name = "exchange_onprem_m20.ps1"
        else:
            content = online_script(self._plans, cfg)
            name = "exchange_online_m20.ps1"
        self._save_generated(content, name, "generation_script_exchange")

    def _on_generate_policy(self) -> None:
        cfg = self._current_config()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return
        content = email_policy_script(cfg)
        self._save_generated(
            content, "exchange_politique_adresses_m20.ps1",
            "generation_politique_adresses",
        )

    def _on_export_report(self) -> None:
        if not self._require_plans():
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport des adresses", "adresses_messagerie.csv",
            "CSV (*.csv);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            path = address_report_csv(self._plans, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record(
            "export_adresses_messagerie", "-", "succes", self.session_id, detail=str(path)
        )
        QMessageBox.information(self, "Exporté", f"Rapport écrit dans « {path} ».")
