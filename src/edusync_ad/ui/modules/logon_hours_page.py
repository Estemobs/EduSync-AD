"""Module 16 — Heures de connexion (``logonHours``).

Grille visuelle 24h × 7j (lundi → dimanche) par utilisateur ou en masse
(OU / groupe). Préréglages : « Heures cours », « Heures admin »,
« Personnalisé ». Application via l'attribut AD ``logonHours`` avec
conversion heure locale ↔ UTC (l'attribut est stocké en UTC).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
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
from edusync_ad.core.logon_hours import (
    DAYS,
    DISPLAY_DAYS,
    HOURS_PER_DAY,
    LogonHoursManager,
    LogonHoursPreset,
    current_utc_offset_hours,
    grid_summary,
    new_grid,
)
from edusync_ad.ui.progress_panel import BatchProgressPanel

PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Heures autorisées"]
COL_SAM, COL_NOM, COL_SUMMARY = range(3)


class HoursGridWidget(QTableWidget):
    """Grille 7 jours × 24 heures avec cases à cocher (1 = connexion autorisée)."""

    def __init__(self, parent=None) -> None:
        super().__init__(DAYS, HOURS_PER_DAY, parent)
        self.setHorizontalHeaderLabels([f"{h:02d}" for h in range(HOURS_PER_DAY)])
        self.setVerticalHeaderLabels(DISPLAY_DAYS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.horizontalHeader().setDefaultSectionSize(24)
        self.horizontalHeader().setMinimumSectionSize(24)
        self.verticalHeader().setDefaultSectionSize(24)
        self.setFixedSize(self.horizontalHeader().length() + 100,
                          self.verticalHeader().length() + 40)

        for d in range(DAYS):
            for h in range(HOURS_PER_DAY):
                item = QTableWidgetItem()
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setToolTip(f"{DISPLAY_DAYS[d]} {h:02d}:00–{h + 1:02d}:00")
                self.setItem(d, h, item)

    def set_grid(self, grid) -> None:
        self.blockSignals(True)
        for d in range(DAYS):
            for h in range(HOURS_PER_DAY):
                state = Qt.CheckState.Checked if grid[d][h] else Qt.CheckState.Unchecked
                self.item(d, h).setCheckState(state)
        self.blockSignals(False)

    def get_grid(self):
        return [
            [
                self.item(d, h).checkState() == Qt.CheckState.Checked
                for h in range(HOURS_PER_DAY)
            ]
            for d in range(DAYS)
        ]

    def fill_all(self, value: bool) -> None:
        self.set_grid(new_grid(value))


class LogonHoursPage(QWidget):
    """Page M16 — heures de connexion."""

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

        self._preview_users: list[dict] = []
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 1. Portée
        scope_group = QGroupBox("1. Portée")
        scope_layout = QHBoxLayout(scope_group)

        scope_layout.addWidget(QLabel("Appliquer à :"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Un utilisateur…", "user")
        self.scope_combo.addItem("Une OU…", "ou")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(self.scope_combo)

        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(300)
        self.ou_combo.currentIndexChanged.connect(self._on_ou_changed)
        scope_layout.addWidget(self.ou_combo)

        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(240)
        scope_layout.addWidget(self.user_combo)

        self.load_btn = QPushButton("Charger depuis AD")
        self.load_btn.clicked.connect(self._on_load_clicked)
        scope_layout.addWidget(self.load_btn)
        scope_layout.addStretch()

        # 2. Grille
        grid_group = QGroupBox("2. Grille des heures autorisées")
        grid_layout = QVBoxLayout(grid_group)

        tools_row = QHBoxLayout()
        tools_row.addWidget(QLabel("Modèle :"))
        self.preset_combo = QComboBox()
        for preset in LogonHoursPreset.builtin():
            self.preset_combo.addItem(preset.name, preset.name)
            self.preset_combo.setItemData(
                self.preset_combo.count() - 1, preset.description,
                Qt.ItemDataRole.ToolTipRole,
            )
        tools_row.addWidget(self.preset_combo)

        preset_btn = QPushButton("Appliquer le modèle")
        preset_btn.clicked.connect(self._on_apply_preset)
        tools_row.addWidget(preset_btn)

        all_btn = QPushButton("Tout cocher")
        all_btn.clicked.connect(lambda: self.grid.fill_all(True))
        tools_row.addWidget(all_btn)

        none_btn = QPushButton("Tout décocher")
        none_btn.clicked.connect(lambda: self.grid.fill_all(False))
        tools_row.addWidget(none_btn)
        tools_row.addStretch()
        grid_layout.addLayout(tools_row)

        self.grid = HoursGridWidget()
        self.grid.cellChanged.connect(self._on_grid_changed)
        grid_layout.addWidget(self.grid, alignment=Qt.AlignmentFlag.AlignLeft)

        self.summary_label = QLabel(grid_summary(new_grid(False)))
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color: #888;")
        grid_layout.addWidget(self.summary_label)

        offset = current_utc_offset_hours()
        offset_label = QLabel(
            f"Heure locale UTC{offset:+d} — l'attribut AD est stocké en UTC, "
            "EduSync applique la conversion automatiquement."
        )
        offset_label.setStyleSheet("color: #888; font-size: 11px;")
        offset_label.setWordWrap(True)
        grid_layout.addWidget(offset_label)

        # 3. Aperçu (mode OU) + application
        action_group = QGroupBox("3. Application")
        action_layout = QVBoxLayout(action_group)

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setMaximumHeight(200)
        self.preview_table.setVisible(False)
        action_layout.addWidget(self.preview_table)

        apply_row = QHBoxLayout()
        self.apply_btn = QPushButton("Appliquer les heures de connexion")
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        apply_row.addWidget(self.apply_btn)
        apply_row.addStretch()
        action_layout.addLayout(apply_row)

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(scope_group)
        layout.addWidget(grid_group)
        layout.addWidget(action_group)
        layout.addWidget(self.progress_panel)
        layout.addStretch()

        self._on_scope_changed()

    # -- Portée / chargement ------------------------------------------------------

    def _on_scope_changed(self) -> None:
        is_user = self.scope_combo.currentData() == "user"
        self.user_combo.setVisible(is_user)
        self.preview_table.setVisible(not is_user)
        if is_user and self.user_combo.count() == 0 and self.ou_combo.count():
            self._load_users()

    def _on_ou_changed(self) -> None:
        if self.scope_combo.currentData() == "user":
            self._load_users()

    def _load_ous(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        previous = self.ou_combo.currentData()
        self.ou_combo.clear()
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._load_users()

    def _load_users(self) -> None:
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        previous = self.user_combo.currentData()
        self.user_combo.clear()
        for user in sorted(users, key=lambda u: u.get("sam", "").lower()):
            self.user_combo.addItem(f"{user.get('cn', '')}  ({user.get('sam', '')})", user["dn"])
        idx = self.user_combo.findData(previous)
        self.user_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_load_clicked(self) -> None:
        if self.scope_combo.currentData() == "user":
            self._on_load_user()
        else:
            self._on_load_preview()

    def _on_load_user(self) -> None:
        user_dn = self.user_combo.currentData()
        if not user_dn:
            QMessageBox.warning(self, "Compte requis", "Sélectionnez un compte à charger.")
            return
        mgr = LogonHoursManager(self.ad_connection)
        try:
            grid = mgr.get_grid(user_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        self.grid.set_grid(grid)
        self.summary_label.setText(grid_summary(grid))

    def _on_load_preview(self) -> None:
        """Mode masse : liste les comptes de l'OU et leurs heures actuelles."""
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        mgr = LogonHoursManager(self.ad_connection)
        self._preview_users = users
        self.preview_table.setRowCount(len(users))
        for row, user in enumerate(users):
            try:
                summary = grid_summary(mgr.get_grid(user["dn"]))
            except ADError:
                summary = "— erreur de lecture —"
            self.preview_table.setItem(row, COL_SAM, QTableWidgetItem(user.get("sam", "")))
            self.preview_table.setItem(row, COL_NOM, QTableWidgetItem(user.get("cn", "")))
            self.preview_table.setItem(row, COL_SUMMARY, QTableWidgetItem(summary))

    def _on_grid_changed(self, _row: int, _col: int) -> None:
        self.summary_label.setText(grid_summary(self.grid.get_grid()))

    def _on_apply_preset(self) -> None:
        name = self.preset_combo.currentData()
        for preset in LogonHoursPreset.builtin():
            if preset.name == name:
                if preset.ranges:
                    self.grid.set_grid(preset.build())
                    self._on_grid_changed(0, 0)
                else:
                    QMessageBox.information(
                        self, "Modèle personnalisé",
                        "« Personnalisé » conserve la grille éditée manuellement — "
                        "cochez/décochez les heures souhaitées puis appliquez.",
                    )
                return

    # -- Application ----------------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        grid = self.grid.get_grid()
        summary = grid_summary(grid)
        ou_dn = self.ou_combo.currentData() or ""

        if self.scope_combo.currentData() == "user":
            user_dn = self.user_combo.currentData()
            if not user_dn:
                QMessageBox.warning(self, "Compte requis", "Sélectionnez un compte.")
                return
            mgr = LogonHoursManager(self.ad_connection)
            try:
                result = mgr.apply(user_dn, grid)
            except ADError as exc:
                QMessageBox.critical(self, "Erreur AD", str(exc))
                self.audit_log.record(
                    "application_heures_connexion", "-", "echec", self.session_id,
                    ou_source=ou_dn, detail=str(exc),
                )
                return
            self.audit_log.record(
                "application_heures_connexion", "-", "succes", self.session_id,
                ou_source=ou_dn, detail=f"{user_dn} : {summary} (action={result})",
            )
            QMessageBox.information(
                self, "Appliqué",
                f"Heures de connexion mises à jour pour le compte sélectionné.\n{summary}",
            )
            return

        # Mode masse (OU)
        users = self._users_of_current_ou()
        if not users:
            QMessageBox.warning(self, "Rien à faire", "Aucun compte dans l'OU sélectionnée.")
            return

        labels = [u.get("sam", "") for u in users]
        self.apply_btn.setEnabled(False)
        mgr = LogonHoursManager(self.ad_connection)

        def run_one(user: dict) -> None:
            mgr.apply(user["dn"], grid)

        def on_result(position: int, success: bool, message: str) -> None:
            user = users[position]
            self.audit_log.record(
                "application_heures_connexion", user.get("sam", ""), 
                "succes" if success else "echec",
                self.session_id, ou_source=ou_dn,
                detail=summary if success else message,
            )

        def on_finished() -> None:
            self.apply_btn.setEnabled(True)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(users)} compte(s) mis à jour.\n{summary}",
            )

        self.progress_panel.finished.connect(
            on_finished, type=Qt.ConnectionType.SingleShotConnection
        )
        self.progress_panel.start(
            "Application des heures de connexion…", users, labels, run_one,
            on_item_result=on_result,
        )

    def _users_of_current_ou(self) -> list[dict]:
        ou_dn = self.ou_combo.currentData()
        if not ou_dn:
            return []
        if self._preview_users:
            return self._preview_users
        try:
            return self.ad_connection.list_users_in_ou(ou_dn)
        except ADError:
            return []
