"""Module 19 — Office 365 / Microsoft Entra ID (hybride).

- Connexion via app registration (secret client ou certificat), test + sauvegarde
- Création des comptes cloud depuis l'AD avec licences éducation (A1/A3/A5)
- Groupes AD → groupes Entra (distribution / sécurité / Microsoft 365 + Teams)
- Photos d'identité → photo de profil cloud
- Check-list d'identité hybride (PHS / ADFS / PTA) — Entra Connect se
  configure manuellement, le module documente au lieu de le simuler
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
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection, is_builtin_group_dn
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.graph import GraphConfig, GraphError, load_m365_config, save_m365_config
from edusync_ad.core.m365 import (
    DEFAULT_LICENSE,
    EDUCATION_LICENSES,
    GROUP_KINDS,
    M365Manager,
    build_group_plans,
    build_photo_mapping,
    build_user_plans,
    hybrid_checklist_markdown,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

USER_COLUMNS = ["Identifiant", "UPN cloud", "Nom complet", "État"]
COL_SAM, COL_UPN, COL_NOM, COL_ETAT = range(4)


class M365Page(QWidget):
    """Page M19 — Microsoft 365 / Entra ID."""

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

        self._cfg: GraphConfig = load_m365_config()
        self._ad_users: list[dict] = []
        self._plans = []
        self._results: dict[str, object] = {}

        self._build_ui()
        self._load_config_into_form()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- UI --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Connexion
        conn_group = QGroupBox("1. Connexion Microsoft Entra ID (app registration)")
        form = QFormLayout(conn_group)

        self.tenant_edit = QLineEdit()
        self.tenant_edit.setPlaceholderText("GUID du locataire (tenant ID)")
        form.addRow("Locataire :", self.tenant_edit)

        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("GUID de l'application (client ID)")
        form.addRow("Application :", self.client_edit)

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("Secret client", "secret")
        self.auth_combo.addItem("Certificat (JWT RS256)", "certificate")
        self.auth_combo.currentIndexChanged.connect(self._on_auth_mode_changed)
        form.addRow("Authentification :", self.auth_combo)

        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Secret client :", self.secret_edit)

        cert_row = QHBoxLayout()
        self.cert_edit = QLineEdit()
        self.cert_edit.setPlaceholderText("chemin vers la clé + certificat PEM")
        self.cert_edit.setEnabled(False)
        cert_row.addWidget(self.cert_edit)
        browse_cert = QPushButton("…")
        browse_cert.setFixedWidth(32)
        browse_cert.clicked.connect(self._on_browse_cert)
        cert_row.addWidget(browse_cert)
        form.addRow("Fichier PEM :", cert_row)

        self.location_edit = QLineEdit()
        self.location_edit.setFixedWidth(60)
        form.addRow("Localisation d'usage :", self.location_edit)

        action_row = QHBoxLayout()
        self.test_btn = QPushButton("Tester la connexion")
        self.test_btn.clicked.connect(self._on_test_connection)
        action_row.addWidget(self.test_btn)
        save_btn = QPushButton("Enregistrer")
        save_btn.clicked.connect(self._on_save_config)
        action_row.addWidget(save_btn)
        self.conn_status = QLabel("Non testé.")
        self.conn_status.setStyleSheet("color: #888;")
        action_row.addWidget(self.conn_status)
        action_row.addStretch()
        form.addRow("", action_row)

        # 2. Comptes cloud
        users_group = QGroupBox("2. Comptes cloud — création depuis l'AD + licences")
        uv = QVBoxLayout(users_group)

        ou_row = QHBoxLayout()
        ou_row.addWidget(QLabel("OU :"))
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(300)
        ou_row.addWidget(self.ou_combo)
        refresh_ous = QPushButton("Actualiser")
        refresh_ous.clicked.connect(self._load_ous)
        ou_row.addWidget(refresh_ous)
        self.sub_ous_check = QCheckBox("Inclure les sous-OU")
        self.sub_ous_check.setChecked(True)
        ou_row.addWidget(self.sub_ous_check)
        load_btn = QPushButton("Charger les comptes AD")
        load_btn.clicked.connect(self._on_load_users)
        ou_row.addWidget(load_btn)
        ou_row.addStretch()
        uv.addLayout(ou_row)

        license_row = QHBoxLayout()
        license_row.addWidget(QLabel("Licence :"))
        self.license_combo = QComboBox()
        self.license_combo.setMinimumWidth(320)
        self.license_combo.addItem("(aucune licence)", "")
        for part, label in EDUCATION_LICENSES.items():
            self.license_combo.addItem(f"{label}  [{part}]", part)
        idx = self.license_combo.findData(DEFAULT_LICENSE)
        self.license_combo.setCurrentIndex(max(idx, 0))
        license_row.addWidget(self.license_combo)
        self.create_btn = QPushButton("Créer les comptes absents + licences")
        self.create_btn.clicked.connect(self._on_create_users)
        license_row.addWidget(self.create_btn)
        license_row.addStretch()
        uv.addLayout(license_row)

        self.users_table = QTableWidget(0, len(USER_COLUMNS))
        self.users_table.setHorizontalHeaderLabels(USER_COLUMNS)
        self.users_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.users_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.users_table.setMaximumHeight(220)
        uv.addWidget(self.users_table)

        self.load_info = QLabel("Aucun compte chargé.")
        self.load_info.setStyleSheet("color: #888;")
        uv.addWidget(self.load_info)

        # 3. Groupes
        groups_group = QGroupBox("3. Groupes AD → Entra (distribution / sécurité / Teams)")
        gv = QVBoxLayout(groups_group)
        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("Type de groupe :"))
        self.kind_combo = QComboBox()
        for kind, label in GROUP_KINDS.items():
            self.kind_combo.addItem(label, kind)
        group_row.addWidget(self.kind_combo)
        self.teams_check = QCheckBox("Créer un Teams pour les nouveaux groupes Microsoft 365")
        self.teams_check.setChecked(True)
        group_row.addWidget(self.teams_check)
        sync_groups_btn = QPushButton("Synchroniser les groupes")
        sync_groups_btn.clicked.connect(self._on_sync_groups)
        group_row.addWidget(sync_groups_btn)
        group_row.addStretch()
        gv.addLayout(group_row)

        # 4. Photos
        photos_group = QGroupBox("4. Photos d'identité → photo de profil cloud")
        pv = QVBoxLayout(photos_group)
        photo_row = QHBoxLayout()
        photo_row.addWidget(QLabel("Dossier des photos :"))
        self.photo_dir_edit = QLineEdit()
        photo_row.addWidget(self.photo_dir_edit)
        browse_photos = QPushButton("…")
        browse_photos.setFixedWidth(32)
        browse_photos.clicked.connect(self._on_browse_photos)
        photo_row.addWidget(browse_photos)
        sync_photos_btn = QPushButton("Synchroniser les photos")
        sync_photos_btn.clicked.connect(self._on_sync_photos)
        photo_row.addWidget(sync_photos_btn)
        photo_row.addStretch()
        pv.addLayout(photo_row)
        photo_hint = QLabel(
            "Conventions de nommage M12 : identifiant.jpg, prenom_nom.jpg, "
            "prenom.nom.jpg, nom_prenom.jpg"
        )
        photo_hint.setStyleSheet("color: #888;")
        pv.addWidget(photo_hint)

        # 5. Hybride
        hybrid_group = QGroupBox("5. Identité hybride — PHS / ADFS / PTA (Entra Connect)")
        hv = QVBoxLayout(hybrid_group)
        self.hybrid_view = QTextBrowser()
        self.hybrid_view.setOpenExternalLinks(True)
        self.hybrid_view.setMaximumHeight(190)
        self.hybrid_view.setMarkdown(hybrid_checklist_markdown())
        hv.addWidget(self.hybrid_view)
        export_hybrid = QPushButton("Exporter la check-list (.md)…")
        export_hybrid.clicked.connect(self._on_export_checklist)
        hv.addWidget(export_hybrid)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(conn_group)
        layout.addWidget(users_group)
        layout.addWidget(groups_group)
        layout.addWidget(photos_group)
        layout.addWidget(hybrid_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

    # -- Configuration -------------------------------------------------------------

    def _load_config_into_form(self) -> None:
        self.tenant_edit.setText(self._cfg.tenant_id)
        self.client_edit.setText(self._cfg.client_id)
        self.secret_edit.setText(self._cfg.client_secret)
        self.cert_edit.setText(self._cfg.certificate_path)
        self.location_edit.setText(self._cfg.usage_location)
        idx = self.auth_combo.findData(self._cfg.auth_mode)
        self.auth_combo.setCurrentIndex(max(idx, 0))
        self._on_auth_mode_changed()

    def _on_auth_mode_changed(self) -> None:
        is_cert = self.auth_combo.currentData() == "certificate"
        self.secret_edit.setEnabled(not is_cert)
        self.cert_edit.setEnabled(is_cert)

    def _read_form_config(self, *, quiet: bool = False) -> GraphConfig | None:
        cfg = GraphConfig(
            tenant_id=self.tenant_edit.text().strip(),
            client_id=self.client_edit.text().strip(),
            client_secret=self.secret_edit.text().strip(),
            auth_mode=str(self.auth_combo.currentData() or "secret"),
            certificate_path=self.cert_edit.text().strip(),
            usage_location=self.location_edit.text().strip() or "FR",
        )
        errors = cfg.validate()
        if errors and not quiet:
            QMessageBox.warning(self, "Configuration invalide", "\n".join(errors))
            return None
        return cfg

    def _on_browse_cert(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner la clé + le certificat PEM", "",
            "Fichiers PEM (*.pem *.key *.crt);;Tous les fichiers (*)",
        )
        if path:
            self.cert_edit.setText(path)

    def _on_browse_photos(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Sélectionner le dossier des photos",
            self.photo_dir_edit.text().strip(),
        )
        if directory:
            self.photo_dir_edit.setText(directory)

    def _manager(self, cfg: GraphConfig) -> M365Manager:
        return M365Manager(cfg)

    def _on_test_connection(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        try:
            tenant_name = self._manager(cfg).test_connection()
        except GraphError as exc:
            self.conn_status.setText("Échec.")
            self.conn_status.setStyleSheet("color: #d64545;")
            self.audit_log.record(
                "connexion_m365", "-", "echec", self.session_id, detail=str(exc)
            )
            QMessageBox.critical(self, "Connexion impossible", str(exc))
            return
        self.conn_status.setText(f"Connecté : {tenant_name}")
        self.conn_status.setStyleSheet("color: #2f9e56;")
        self._cfg = cfg
        self.audit_log.record(
            "connexion_m365", "-", "succes", self.session_id, detail=tenant_name
        )
        self._refresh_licenses(cfg)

    def _refresh_licenses(self, cfg: GraphConfig) -> None:
        """Restreint la combo aux licences réellement souscrites."""
        try:
            available = self._manager(cfg).available_licenses()
        except GraphError:
            return
        current = self.license_combo.currentData()
        self.license_combo.clear()
        self.license_combo.addItem("(aucune licence)", "")
        for part in EDUCATION_LICENSES:
            if part in available:
                self.license_combo.addItem(
                    f"{available[part]['label']}  [{part}]", part
                )
        idx = self.license_combo.findData(current)
        self.license_combo.setCurrentIndex(max(idx, 0))

    def _on_save_config(self) -> None:
        cfg = self._read_form_config()
        if cfg is None:
            return
        try:
            save_m365_config(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self._cfg = cfg
        self.audit_log.record(
            "configuration_m365", "-", "succes", self.session_id,
            detail=f"locataire={cfg.tenant_id} mode={cfg.auth_mode}",
        )
        QMessageBox.information(self, "Enregistré", "Connexion Entra ID enregistrée (secret chiffré).")

    # -- Source AD -------------------------------------------------------------------

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

        # Comptes déjà présents côté Entra (best effort : la comparaison est
        # silencieusement omise si la connexion cloud échoue).
        existing: set[str] = set()
        cloud_ok = True
        cfg = self._read_form_config(quiet=True) or self._cfg
        if cfg.tenant_id and cfg.client_id:
            try:
                cloud_users = M365Manager(cfg).client.list_users()
                existing = {
                    str(u.get("userPrincipalName", ""))
                    for u in cloud_users
                    if u.get("userPrincipalName")
                }
            except GraphError:
                cloud_ok = False

        domain = self.ad_connection.domain or ""
        self._ad_users = users
        self._plans = build_user_plans(users, domain, existing)
        self._results = {}
        self._populate_users_table()
        if cloud_ok:
            self.load_info.setText(
                f"{len(self._plans)} compte(s) — "
                f"{sum(1 for p in self._plans if p.existing)} déjà dans Entra ID."
            )
        else:
            self.load_info.setText(
                f"{len(self._plans)} compte(s) chargé(s) — cloud injoignable, "
                "aucune comparaison d'existence."
            )
        self.audit_log.record(
            "analyse_comptes_cloud", "-", "succes", self.session_id,
            detail=f"{len(self._plans)} compte(s) planifié(s)",
        )

    def _populate_users_table(self) -> None:
        self.users_table.setRowCount(len(self._plans))
        for row, plan in enumerate(self._plans):
            self.users_table.setItem(row, COL_SAM, QTableWidgetItem(plan.sam))
            self.users_table.setItem(row, COL_UPN, QTableWidgetItem(plan.upn))
            self.users_table.setItem(row, COL_NOM, QTableWidgetItem(plan.display_name))
            state = "déjà présent" if plan.existing else "à créer"
            item = QTableWidgetItem(state)
            if plan.existing:
                item.setForeground(Qt.GlobalColor.darkGreen)
            elif plan.disabled:
                item.setForeground(Qt.GlobalColor.darkYellow)
            self.users_table.setItem(row, COL_ETAT, item)

    # -- Lots d'exécution -----------------------------------------------------------------

    def _require_manager(self) -> M365Manager | None:
        cfg = self._read_form_config(quiet=True) or self._cfg
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(
                self, "Connexion requise",
                "Complétez la section 1 :\n- " + "\n- ".join(errors),
            )
            return None
        self._cfg = cfg
        return M365Manager(cfg)

    def _on_create_users(self) -> None:
        if not self._plans:
            QMessageBox.warning(
                self, "Aucun compte", "Chargez les comptes AD (section 2) d'abord."
            )
            return
        manager = self._require_manager()
        if manager is None:
            return
        license_part = str(self.license_combo.currentData() or "")
        plans = [p for p in self._plans if not p.existing]
        if not plans:
            QMessageBox.information(self, "Rien à faire", "Tous les comptes existent déjà dans Entra ID.")
            return

        results = self._results

        def run_one(plan) -> None:
            result = manager.sync_users([plan], license_part)[0]
            results[plan.upn] = result
            if not result.ok:
                raise ADError(result.detail)

        def on_result(index: int, success: bool, message: str) -> None:
            plan = plans[index]
            result = results.get(plan.upn)
            detail = (result.detail if result else message) or ("compte créé" if success else "")
            state = f"✓ {detail}" if success else f"✗ {detail}"
            self.users_table.setItem(
                self._plans.index(plan), COL_ETAT, QTableWidgetItem(state)
            )
            self.audit_log.record(
                "creation_compte_cloud", plan.upn,
                "succes" if success else "echec", self.session_id, detail=detail,
            )

        def on_finished() -> None:
            self.create_btn.setEnabled(True)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(plans)} compte(s) traité(s).",
            )

        self.create_btn.setEnabled(False)
        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Création des comptes cloud…", plans,
            [p.label for p in plans], run_one, on_item_result=on_result,
        )

    def _on_sync_groups(self) -> None:
        manager = self._require_manager()
        if manager is None:
            return
        if self.ad_connection.domain is None:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous à l'AD d'abord.")
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        domain = self.ad_connection.domain or ""

        specs: list[dict] = []
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            if is_builtin_group_dn(dn):
                continue
            try:
                members = self.ad_connection.list_users_in_group(dn, base_dn)
            except ADError:
                members = []
            specs.append({
                "name": name,
                "members": [f"{m['sam']}@{domain}" for m in members if m.get("sam")],
            })
        if not specs:
            QMessageBox.information(self, "Aucun groupe", "Aucun groupe AD hors groupes intégrés.")
            return

        # Index figés au démarrage du lot (1 seule salve Graph)
        try:
            users_idx, groups_idx = manager.cloud_indexes()
        except GraphError as exc:
            QMessageBox.critical(self, "Connexion impossible", str(exc))
            return

        kind = str(self.kind_combo.currentData() or "m365")
        create_team = self.teams_check.isChecked() and kind == "m365"
        plans = build_group_plans(specs, kind=kind, existing_by_nickname=groups_idx)
        self._results = {}

        def run_one(plan) -> None:
            result = manager.sync_groups(
                [plan], create_team=create_team,
                users_by_upn=users_idx, groups_by_nickname=groups_idx,
            )[0]
            self._results[plan.name] = result
            if not result.ok:
                raise ADError(result.detail)

        def on_result(index: int, success: bool, message: str) -> None:
            plan = plans[index]
            result = self._results.get(plan.name)
            detail = (result.detail if result else message) or ""
            self.audit_log.record(
                "synchronisation_groupe_cloud", plan.name,
                "succes" if success else "echec", self.session_id, detail=detail,
            )

        def on_finished() -> None:
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(plans)} groupe(s) traité(s).",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Synchronisation des groupes → Entra ID…", plans,
            [p.label for p in plans], run_one, on_item_result=on_result,
        )

    def _on_sync_photos(self) -> None:
        if not self._ad_users:
            QMessageBox.warning(
                self, "Aucun compte",
                "Chargez les comptes AD (section 2) pour associer les photos.",
            )
            return
        photo_dir = Path(self.photo_dir_edit.text().strip())
        if not photo_dir.is_dir():
            QMessageBox.warning(self, "Dossier invalide", "Sélectionnez le dossier des photos.")
            return
        manager = self._require_manager()
        if manager is None:
            return

        domain = self.ad_connection.domain or ""
        mapping = build_photo_mapping(photo_dir, self._ad_users, domain)
        if not mapping:
            QMessageBox.information(
                self, "Aucune photo",
                "Aucune photo associée aux comptes chargés (voir les conventions de nommage).",
            )
            return
        try:
            users_idx, _ = manager.cloud_indexes()
        except GraphError as exc:
            QMessageBox.critical(self, "Connexion impossible", str(exc))
            return

        items = list(mapping.items())
        self._results = {}

        def run_one(item) -> None:
            upn, path = item
            result = manager.sync_photos(
                {upn: path}, users_by_upn=users_idx
            )[0]
            self._results[upn] = result
            if not result.ok:
                raise ADError(result.detail)

        def on_result(index: int, success: bool, message: str) -> None:
            upn, _ = items[index]
            result = self._results.get(upn)
            detail = (result.detail if result else message) or ""
            self.audit_log.record(
                "photo_profil_cloud", upn,
                "succes" if success else "echec", self.session_id, detail=detail,
            )

        def on_finished() -> None:
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(items)} photo(s) déposée(s).",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Envoi des photos vers Entra ID…", items,
            [upn for upn, _ in items], run_one, on_item_result=on_result,
        )

    # -- Hybride ------------------------------------------------------------------------

    def _on_export_checklist(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter la check-list hybride", "identite_hybride.md",
            "Markdown (*.md);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            Path(dest).write_text(hybrid_checklist_markdown(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record(
            "export_checklist_hybride", "-", "succes", self.session_id, detail=dest
        )
        QMessageBox.information(self, "Exporté", f"Check-list écrite dans « {dest} ».")
