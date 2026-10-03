"""Module 14 — Quotas de disque (FSRM).

Définition d'un quota par OU ou groupe : taille, seuil d'alerte, blocage dur.
Prévisualisation du quota résolu par dossier personnel (M17), application via
génération de script PowerShell FSRM, et rapport de quotas exportable en CSV.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.homedirs import HomeDirManager, load_home_dir_config
from edusync_ad.core.quotas import (
    QuotaManager,
    QuotaPlan,
    QuotaSettings,
    load_all_quota_configs,
    save_all_quota_configs,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

PREVIEW_COLUMNS = ["Identifiant", "Dossier personnel", "Quota (Go)", "Alerte (%)", "Type", "État"]
COL_SAM, COL_PATH, COL_SIZE, COL_WARN, COL_TYPE, COL_ETAT = range(6)


class QuotasPage(QWidget):
    """Page M14 — quotas FSRM des dossiers personnels."""

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

        self._ou_configs: dict[str, QuotaSettings] = {}
        self._group_configs: dict[str, QuotaSettings] = {}
        self._default_config = QuotaSettings()
        self._plans: list[QuotaPlan] = []

        self._load_configs()
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    def _load_configs(self) -> None:
        try:
            self._ou_configs, self._group_configs, self._default_config = (
                load_all_quota_configs()
            )
        except Exception:  # noqa: BLE001
            self._ou_configs, self._group_configs = {}, {}
            self._default_config = QuotaSettings()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Définition
        config_group = QGroupBox("1. Définition du quota")
        form = QFormLayout(config_group)

        scope_row = QHBoxLayout()
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Valeur par défaut", "default")
        self.scope_combo.addItem("Une OU…", "ou")
        self.scope_combo.addItem("Un groupe…", "group")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(QLabel("Appliquer à :"))
        scope_row.addWidget(self.scope_combo)

        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(320)
        self.ou_combo.setVisible(False)
        scope_row.addWidget(self.ou_combo)

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(320)
        self.group_combo.setVisible(False)
        scope_row.addWidget(self.group_combo)
        scope_row.addStretch()
        form.addRow("", scope_row)

        size_row = QHBoxLayout()
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.1, 4096.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setSuffix(" Go")
        self.size_spin.setValue(10.0)
        size_row.addWidget(self.size_spin)

        self.warn_spin = QSpinBox()
        self.warn_spin.setRange(1, 99)
        self.warn_spin.setSuffix(" %")
        self.warn_spin.setValue(80)
        size_row.addWidget(QLabel("Seuil d'alerte :"))
        size_row.addWidget(self.warn_spin)

        self.hard_check = QCheckBox("Blocage dur (sinon quota souple)")
        self.hard_check.setChecked(True)
        size_row.addWidget(self.hard_check)
        size_row.addStretch()
        form.addRow("Taille :", size_row)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Enregistrer cette configuration")
        save_btn.clicked.connect(self._on_save_config)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        form.addRow("", save_row)

        form.addRow(QLabel("Configurations enregistrées :"))
        self.config_list = QListWidget()
        self.config_list.setMaximumHeight(110)
        form.addRow(self.config_list)

        # 2. Prévisualisation sur les dossiers personnels d'une OU
        preview_group = QGroupBox("2. Prévisualisation (dossiers personnels d'une OU)")
        preview_layout = QVBoxLayout(preview_group)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("OU :"))
        self.preview_ou_combo = QComboBox()
        self.preview_ou_combo.setMinimumWidth(340)
        preview_row.addWidget(self.preview_ou_combo)
        load_btn = QPushButton("Charger les comptes")
        load_btn.clicked.connect(self._on_preview_clicked)
        preview_row.addWidget(load_btn)
        preview_row.addStretch()
        preview_layout.addLayout(preview_row)

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        preview_layout.addWidget(self.preview_table)

        # 3. Actions
        actions_row = QHBoxLayout()
        self.script_btn = QPushButton("3a. Générer le script PowerShell (FSRM)")
        self.script_btn.setEnabled(False)
        self.script_btn.clicked.connect(self._on_generate_script)
        actions_row.addWidget(self.script_btn)

        self.report_btn = QPushButton("3b. Exporter le rapport de quotas (CSV)")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_export_report)
        actions_row.addWidget(self.report_btn)
        actions_row.addStretch()
        preview_layout.addLayout(actions_row)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(config_group)
        layout.addWidget(preview_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

        self._refresh_config_list()

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

    # -- Configuration ----------------------------------------------------------

    def _current_scope(self) -> tuple[str, str] | None:
        scope = self.scope_combo.currentData()
        if scope == "default":
            return ("default", "")
        if scope == "ou":
            dn = self.ou_combo.currentData()
            return ("ou", dn) if dn else None
        dn = self.group_combo.currentData()
        return ("group", dn) if dn else None

    def _read_settings(self) -> QuotaSettings:
        return QuotaSettings(
            size_gb=float(self.size_spin.value()),
            warning_percent=int(self.warn_spin.value()),
            hard_limit=self.hard_check.isChecked(),
        )

    def _on_save_config(self) -> None:
        scope = self._current_scope()
        if scope is None:
            QMessageBox.warning(self, "Portée manquante", "Sélectionnez une OU ou un groupe.")
            return
        settings = self._read_settings()
        errors = settings.validate()
        if errors:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return

        if scope[0] == "default":
            self._default_config = settings
            label = "Défaut"
        elif scope[0] == "ou":
            self._ou_configs[scope[1]] = settings
            label = f"OU {scope[1].split(',')[0]}"
        else:
            self._group_configs[scope[1]] = settings
            label = f"Groupe {scope[1].split(',')[0]}"

        save_all_quota_configs(self._ou_configs, self._group_configs, self._default_config)
        self._refresh_config_list()
        self.audit_log.record(
            "configuration_quota", label, "succes", self.session_id,
            detail=f"taille={settings.size_gb}Go alerte={settings.warning_percent}% "
                   f"type={'dur' if settings.hard_limit else 'souple'}",
        )
        QMessageBox.information(self, "Enregistré", f"Quota enregistré pour : {label}")

    def _refresh_config_list(self) -> None:
        self.config_list.clear()
        d = self._default_config
        self.config_list.addItem(
            f"[Défaut] {d.size_gb:g} Go — alerte {d.warning_percent} % — "
            f"{'dur' if d.hard_limit else 'souple'}"
        )
        for dn, s in self._ou_configs.items():
            self.config_list.addItem(
                f"[OU] {dn.split(',')[0]} → {s.size_gb:g} Go ({s.warning_percent} %)"
            )
        for dn, s in self._group_configs.items():
            self.config_list.addItem(
                f"[Groupe] {dn.split(',')[0]} → {s.size_gb:g} Go ({s.warning_percent} %)"
            )

    def _build_manager(self) -> QuotaManager:
        return QuotaManager(self._ou_configs, self._group_configs, self._default_config)

    # -- Prévisualisation ---------------------------------------------------------

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

        home_cfg = load_home_dir_config()
        if home_cfg.validate():
            QMessageBox.warning(
                self, "Dossier personnel non configuré",
                "Configurez d'abord les dossiers personnels (M17) — le quota "
                "s'applique à ces dossiers.\n\nLa prévisualisation utilise le "
                f"partage par défaut « {home_cfg.share_root} ».",
            )
        domain = self.ad_connection.domain or ""
        home_mgr = HomeDirManager(home_cfg)
        home_plans = home_mgr.plan_batch(users, ou_dn, domain)

        base_dn = ADConnection.domain_to_base_dn(domain)
        qmgr = self._build_manager()
        groups_by_sam: dict[str, list[str]] = {}
        for user in users:
            try:
                groups_by_sam[user["sam"]] = self.ad_connection.search_user_groups(
                    user["dn"], base_dn
                )
            except ADError:
                groups_by_sam[user["sam"]] = []
        self._plans = qmgr.plans_for_homes(home_plans, ou_dn, groups_by_sam)

        self.preview_table.setRowCount(len(self._plans))
        for row, plan in enumerate(self._plans):
            s = plan.settings
            self.preview_table.setItem(row, COL_SAM, QTableWidgetItem(plan.sam))
            self.preview_table.setItem(row, COL_PATH, QTableWidgetItem(plan.home_path))
            self.preview_table.setItem(row, COL_SIZE, QTableWidgetItem(f"{s.size_gb:g}"))
            self.preview_table.setItem(row, COL_WARN, QTableWidgetItem(str(s.warning_percent)))
            self.preview_table.setItem(row, COL_TYPE, QTableWidgetItem("Dur" if s.hard_limit else "Souple"))
            item = QTableWidgetItem("Prêt")
            item.setForeground(Qt.GlobalColor.darkGreen)
            self.preview_table.setItem(row, COL_ETAT, item)

        self.script_btn.setEnabled(bool(self._plans))
        self.report_btn.setEnabled(bool(self._plans))

    # -- Actions --------------------------------------------------------------------

    def _on_generate_script(self) -> None:
        if not self._plans:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le script FSRM", "quotas_m14.ps1",
            "Scripts PowerShell (*.ps1);;Tous les fichiers (*)",
        )
        if not dest:
            return
        mgr = self._build_manager()
        try:
            path = mgr.save_powershell_script(self._plans, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Impossible d'écrire le script : {exc}")
            return
        self.audit_log.record(
            "generation_script_quotas", "-", "succes", self.session_id,
            detail=f"{len(self._plans)} quota(s) → {path}",
        )
        QMessageBox.information(
            self, "Script généré",
            f"{len(self._plans)} quota(s) dans « {path} ».\n\n"
            "Exécutez-le en admin sur le serveur de fichiers (rôle FSRM) — les "
            "templates sont créés automatiquement si absents.",
        )

    def _on_export_report(self) -> None:
        if not self._plans:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport de quotas", "rapport_quotas.csv",
            "Fichiers CSV (*.csv);;Tous les fichiers (*)",
        )
        if not dest:
            return
        rows = QuotaManager.report_rows(self._plans)
        try:
            path = QuotaManager.export_report_csv(rows, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Impossible d'écrire le rapport : {exc}")
            return
        self.audit_log.record(
            "export_rapport_quotas", "-", "succes", self.session_id,
            detail=f"{len(rows)} ligne(s) → {path}",
        )
        QMessageBox.information(
            self, "Rapport exporté",
            f"{len(rows)} ligne(s) dans « {path} ».\n\n"
            "L'occupation (octets utilisés) est relevée côté serveur de fichiers "
            "— colonnes « utilise_go / occupation_pct » renseignées à l'import "
            "depuis les rapports de stockage FSRM.",
        )
