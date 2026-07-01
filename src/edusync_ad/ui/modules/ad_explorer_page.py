"""Module 6 — Explorateur AD (§9 du cahier des charges).

Arborescence des OUs (panneau gauche) → liste des utilisateurs de l'OU
sélectionnée (panneau central) → attributs détaillés + actions sur le compte
sélectionné (panneau droit).

Actions disponibles :
- Modifier un attribut (displayName, description, telephoneNumber, department, title, mail)
- Réinitialiser le mot de passe individuel
- Activer / désactiver le compte
- Gérer l'appartenance aux groupes
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_random_password

EDITABLE_ATTRS = [
    ("displayName", "Nom d'affichage"),
    ("description", "Description"),
    ("telephoneNumber", "Téléphone"),
    ("department", "Département"),
    ("title", "Titre"),
    ("mail", "Adresse mail"),
]

USER_COLS = ["Identifiant", "Nom complet", "État"]
COL_SAM, COL_CN, COL_ETAT = range(3)


class ADExplorerPage(QWidget):
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

        self._current_user: dict | None = None
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.ou_tree.topLevelItemCount():
            self._load_ou_tree()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Panneau gauche : arborescence OUs
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        refresh_btn = QPushButton("Actualiser l'arborescence")
        refresh_btn.clicked.connect(self._load_ou_tree)
        left_layout.addWidget(refresh_btn)
        self.ou_tree = QTreeWidget()
        self.ou_tree.setHeaderLabel("Unités Organisationnelles")
        self.ou_tree.itemClicked.connect(self._on_ou_selected)
        left_layout.addWidget(self.ou_tree)
        left.setMinimumWidth(240)

        # Panneau central : liste utilisateurs
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.user_count_label = QLabel("Sélectionnez une OU.")
        center_layout.addWidget(self.user_count_label)
        self.user_table = QTableWidget(0, len(USER_COLS))
        self.user_table.setHorizontalHeaderLabels(USER_COLS)
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.user_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.user_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.user_table.itemSelectionChanged.connect(self._on_user_selected)
        center_layout.addWidget(self.user_table)
        center.setMinimumWidth(300)

        # Panneau droit : détails + actions
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        attrs_group = QGroupBox("Attributs du compte")
        self.attrs_form = QFormLayout(attrs_group)
        self._attr_labels: dict[str, QLabel] = {}
        for attr_key, attr_label in EDITABLE_ATTRS:
            lbl = QLabel("—")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._attr_labels[attr_key] = lbl
            self.attrs_form.addRow(f"{attr_label} :", lbl)

        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)
        self.btn_edit_attr = QPushButton("Modifier un attribut…")
        self.btn_edit_attr.clicked.connect(self._on_edit_attr)
        self.btn_reset_pwd = QPushButton("Réinitialiser le mot de passe…")
        self.btn_reset_pwd.clicked.connect(self._on_reset_password)
        self.btn_toggle_account = QPushButton("Activer / Désactiver le compte")
        self.btn_toggle_account.clicked.connect(self._on_toggle_account)
        self.btn_manage_groups = QPushButton("Gérer les groupes…")
        self.btn_manage_groups.clicked.connect(self._on_manage_groups)
        for btn in (self.btn_edit_attr, self.btn_reset_pwd, self.btn_toggle_account, self.btn_manage_groups):
            btn.setEnabled(False)
            actions_layout.addWidget(btn)
        actions_layout.addStretch()

        right_layout.addWidget(attrs_group)
        right_layout.addWidget(actions_group)
        right.setMinimumWidth(260)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setSizes([240, 360, 280])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # -- Arborescence OUs ------------------------------------------------------

    def _load_ou_tree(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        self.ou_tree.clear()
        root_item = QTreeWidgetItem([self.ad_connection.domain])
        root_item.setData(0, Qt.ItemDataRole.UserRole, base_dn)
        self.ou_tree.addTopLevelItem(root_item)

        dn_to_item: dict[str, QTreeWidgetItem] = {base_dn: root_item}
        ous_sorted = sorted(ous, key=lambda x: x[0].count(","))

        for dn, name in ous_sorted:
            parent_dn = dn.split(",", 1)[1] if "," in dn else base_dn
            parent_item = dn_to_item.get(parent_dn, root_item)
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.ItemDataRole.UserRole, dn)
            parent_item.addChild(item)
            dn_to_item[dn] = item

        root_item.setExpanded(True)

    def _on_ou_selected(self, item: QTreeWidgetItem, _column: int) -> None:
        ou_dn = item.data(0, Qt.ItemDataRole.UserRole)
        if not ou_dn:
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._users = users
        self._current_user = None
        self._populate_user_table(users)
        self.user_count_label.setText(f"{len(users)} utilisateur(s) dans « {item.text(0)} »")
        self._clear_detail_panel()

    def _populate_user_table(self, users: list[dict]) -> None:
        self.user_table.setRowCount(len(users))
        for i, u in enumerate(users):
            self.user_table.setItem(i, COL_SAM, QTableWidgetItem(u["sam"]))
            self.user_table.setItem(i, COL_CN, QTableWidgetItem(u["cn"]))
            etat = "Désactivé" if u.get("disabled") else "Actif"
            self.user_table.setItem(i, COL_ETAT, QTableWidgetItem(etat))

    # -- Sélection utilisateur -------------------------------------------------

    def _on_user_selected(self) -> None:
        rows = self.user_table.selectionModel().selectedRows()
        if not rows or not hasattr(self, "_users"):
            return
        idx = rows[0].row()
        if idx >= len(self._users):
            return
        user_info = self._users[idx]
        try:
            attrs = self.ad_connection.get_user_attributes(user_info["dn"])
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._current_user = attrs
        self._refresh_detail_panel(attrs)
        for btn in (self.btn_edit_attr, self.btn_reset_pwd, self.btn_toggle_account, self.btn_manage_groups):
            btn.setEnabled(True)

    def _refresh_detail_panel(self, attrs: dict) -> None:
        for attr_key, _ in EDITABLE_ATTRS:
            val = attrs.get(attr_key) or "—"
            self._attr_labels[attr_key].setText(val)

    def _clear_detail_panel(self) -> None:
        for lbl in self._attr_labels.values():
            lbl.setText("—")
        for btn in (self.btn_edit_attr, self.btn_reset_pwd, self.btn_toggle_account, self.btn_manage_groups):
            btn.setEnabled(False)

    # -- Actions ---------------------------------------------------------------

    def _on_edit_attr(self) -> None:
        if not self._current_user:
            return
        dialog = EditAttributeDialog(self._current_user, EDITABLE_ATTRS, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        attr_key, new_value = dialog.result_attr, dialog.result_value
        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.update_user_attribute(self._current_user["dn"], attr_key, new_value)
            self._current_user[attr_key] = new_value
            self._refresh_detail_panel(self._current_user)
            self.audit_log.record(
                "modification_attribut",
                self._current_user.get("sAMAccountName", ""),
                "succes",
                self.session_id,
                simulation=simulation,
                detail=f"{attr_key}={new_value}",
            )
        except ADError as exc:
            self.audit_log.record(
                "modification_attribut",
                self._current_user.get("sAMAccountName", ""),
                "echec",
                self.session_id,
                simulation=simulation,
                detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_reset_password(self) -> None:
        if not self._current_user:
            return
        sam = self._current_user.get("sAMAccountName", "")
        policy = PasswordPolicy(longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True)
        new_pwd = generate_random_password(policy)

        reply = QMessageBox.question(
            self,
            "Réinitialiser le mot de passe",
            f"Nouveau mot de passe généré pour {sam} :\n\n{new_pwd}\n\nConfirmer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.set_password(self._current_user["dn"], new_pwd)
            self.audit_log.record("reinitialisation_mdp", sam, "succes", self.session_id, simulation=simulation)
            QMessageBox.information(self, "Succès", f"Mot de passe réinitialisé{'  (simulé)' if simulation else ''}.")
        except ADError as exc:
            self.audit_log.record("reinitialisation_mdp", sam, "echec", self.session_id, simulation=simulation, detail=str(exc))
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_toggle_account(self) -> None:
        if not self._current_user:
            return
        sam = self._current_user.get("sAMAccountName", "")
        is_disabled = self._current_user.get("disabled", False)
        action_label = "activer" if is_disabled else "désactiver"
        reply = QMessageBox.question(
            self,
            "Confirmer",
            f"Voulez-vous {action_label} le compte {sam} ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        simulation = self.ad_connection.dry_run
        try:
            if is_disabled:
                self.ad_connection.enable_account(self._current_user["dn"])
                action_type = "activation_compte"
            else:
                self.ad_connection.disable_account(self._current_user["dn"])
                action_type = "desactivation_compte"
            self._current_user["disabled"] = not is_disabled
            self.audit_log.record(action_type, sam, "succes", self.session_id, simulation=simulation)
            QMessageBox.information(self, "Succès", f"Compte {action_label}{'  (simulé)' if simulation else ''}.")
            self._reload_current_row()
        except ADError as exc:
            self.audit_log.record(
                "activation_compte" if is_disabled else "desactivation_compte",
                sam, "echec", self.session_id, simulation=simulation, detail=str(exc)
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_manage_groups(self) -> None:
        if not self._current_user or self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            all_groups = self.ad_connection.list_groups(base_dn)
            member_of = self._current_user.get("memberOf") or []
            member_dns = {str(g).lower() for g in member_of}
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        dialog = ManageGroupsDialog(
            all_groups=all_groups,
            member_dns=member_dns,
            user_cn=self._current_user.get("cn", ""),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        sam = self._current_user.get("sAMAccountName", "")
        simulation = self.ad_connection.dry_run
        user_dn = self._current_user["dn"]

        for dn in dialog.to_add:
            try:
                self.ad_connection.add_user_to_group(user_dn, dn)
                self.audit_log.record("ajout_groupe", sam, "succes", self.session_id, simulation=simulation, detail=dn)
            except ADError as exc:
                self.audit_log.record("ajout_groupe", sam, "echec", self.session_id, simulation=simulation, detail=str(exc))
                QMessageBox.warning(self, "Erreur", f"Ajout au groupe échoué : {exc}")

        for dn in dialog.to_remove:
            try:
                self.ad_connection.remove_user_from_group(user_dn, dn)
                self.audit_log.record("retrait_groupe", sam, "succes", self.session_id, simulation=simulation, detail=dn)
            except ADError as exc:
                self.audit_log.record("retrait_groupe", sam, "echec", self.session_id, simulation=simulation, detail=str(exc))
                QMessageBox.warning(self, "Erreur", f"Retrait du groupe échoué : {exc}")

        try:
            updated = self.ad_connection.get_user_attributes(user_dn)
            self._current_user = updated
        except ADError:
            pass

    def _reload_current_row(self) -> None:
        rows = self.user_table.selectionModel().selectedRows()
        if not rows or not hasattr(self, "_users") or not self._current_user:
            return
        idx = rows[0].row()
        if idx >= len(self._users):
            return
        self._users[idx]["disabled"] = self._current_user.get("disabled", False)
        etat = "Désactivé" if self._users[idx]["disabled"] else "Actif"
        self.user_table.setItem(idx, COL_ETAT, QTableWidgetItem(etat))


# -- Dialogues -----------------------------------------------------------------

class EditAttributeDialog(QDialog):
    def __init__(self, user_attrs: dict, editable_attrs: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier un attribut")
        self.result_attr: str = ""
        self.result_value: str = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()

        from PyQt6.QtWidgets import QComboBox
        self.attr_combo = QComboBox()
        for key, label in editable_attrs:
            self.attr_combo.addItem(label, key)
        form.addRow("Attribut :", self.attr_combo)

        self.value_edit = QLineEdit()
        self.attr_combo.currentIndexChanged.connect(self._on_attr_changed)
        self._user_attrs = user_attrs
        self._editable_attrs = editable_attrs
        self._on_attr_changed()
        form.addRow("Valeur :", self.value_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_attr_changed(self) -> None:
        key = self.attr_combo.currentData()
        if key:
            self.value_edit.setText(self._user_attrs.get(key) or "")

    def _on_accept(self) -> None:
        self.result_attr = self.attr_combo.currentData()
        self.result_value = self.value_edit.text().strip()
        self.accept()


class ManageGroupsDialog(QDialog):
    def __init__(
        self,
        all_groups: list[tuple[str, str]],
        member_dns: set[str],
        user_cn: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Groupes de {user_cn}")
        self.setMinimumSize(460, 400)
        self.to_add: list[str] = []
        self.to_remove: list[str] = []
        self._all_groups = {dn: name for dn, name in all_groups}
        self._initial_member_dns = set(member_dns)
        self._current_member_dns: set[str] = set(member_dns)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Groupes dont l'utilisateur est membre (cochez/décochez) :"))

        self.list_widget = QListWidget()
        for dn, name in sorted(all_groups, key=lambda x: x[1].lower()):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, dn)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if dn.lower() in member_dns else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        new_member_dns: set[str] = set()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            dn = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked:
                new_member_dns.add(dn.lower())

        self.to_add = [
            dn for dn, _ in self._all_groups.items()
            if dn.lower() in new_member_dns and dn.lower() not in self._initial_member_dns
        ]
        self.to_remove = [
            dn for dn, _ in self._all_groups.items()
            if dn.lower() not in new_member_dns and dn.lower() in self._initial_member_dns
        ]
        self.accept()
