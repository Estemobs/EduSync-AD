"""Module 6 — Explorateur AD (§9 du cahier des charges).

Panneau gauche : arborescence OUs + liste des groupes (onglets).
Panneau central : liste des utilisateurs avec barre de recherche.
Panneau droit : attributs + actions sur le compte sélectionné.

Actions disponibles :
- Modifier un attribut (displayName, description, telephoneNumber, department, title, mail)
- Changer l'OU (déplacer le compte vers une autre OU)
- Réinitialiser le mot de passe individuel
- Activer / désactiver le compte
- Gérer l'appartenance aux groupes
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
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
        self._all_users: list[dict] = []  # liste complète avant filtre recherche
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.ou_tree.topLevelItemCount():
            self._load_left_panel()

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Panneau gauche : onglets OUs / Groupes
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        refresh_btn = QPushButton("Actualiser")
        refresh_btn.clicked.connect(self._load_left_panel)
        left_layout.addWidget(refresh_btn)

        self.left_tabs = QTabWidget()
        left_layout.addWidget(self.left_tabs)

        # Onglet OUs
        ou_widget = QWidget()
        ou_layout = QVBoxLayout(ou_widget)
        ou_layout.setContentsMargins(0, 0, 0, 0)
        self.ou_tree = QTreeWidget()
        self.ou_tree.setHeaderLabel("Unités Organisationnelles")
        self.ou_tree.itemClicked.connect(self._on_ou_selected)
        self.ou_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ou_tree.customContextMenuRequested.connect(self._on_ou_context_menu)
        ou_layout.addWidget(self.ou_tree)
        self.left_tabs.addTab(ou_widget, "OUs")

        # Onglet Groupes
        group_widget = QWidget()
        group_layout = QVBoxLayout(group_widget)
        group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_list = QListWidget()
        self.group_list.itemClicked.connect(self._on_group_selected)
        self.group_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.group_list.customContextMenuRequested.connect(self._on_group_context_menu)
        group_layout.addWidget(self.group_list)
        self.left_tabs.addTab(group_widget, "Groupes")

        left.setMinimumWidth(220)

        # Panneau central : recherche + liste utilisateurs
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher par nom ou identifiant…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_edit)
        center_layout.addLayout(search_row)

        self.user_count_label = QLabel("Sélectionnez une OU ou un groupe.")
        center_layout.addWidget(self.user_count_label)

        self.user_table = QTableWidget(0, len(USER_COLS))
        self.user_table.setHorizontalHeaderLabels(USER_COLS)
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.user_table.horizontalHeader().setStretchLastSection(True)
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
        # OU courante
        self.lbl_ou = QLabel("—")
        self.lbl_ou.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_ou.setWordWrap(True)
        self.attrs_form.addRow("OU :", self.lbl_ou)

        # Dernier changement de mot de passe (lecture seule — AD ne stocke
        # jamais le mot de passe en clair, impossible de l'afficher).
        self.lbl_pwd_last_set = QLabel("—")
        self.lbl_pwd_last_set.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.attrs_form.addRow("Dernier changement mdp :", self.lbl_pwd_last_set)

        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)
        self.btn_edit_attr = QPushButton("Modifier un attribut…")
        self.btn_edit_attr.clicked.connect(self._on_edit_attr)
        self.btn_change_ou = QPushButton("Changer d'OU…")
        self.btn_change_ou.clicked.connect(self._on_change_ou)
        self.btn_reset_pwd = QPushButton("Réinitialiser le mot de passe…")
        self.btn_reset_pwd.clicked.connect(self._on_reset_password)
        self.btn_toggle_account = QPushButton("Activer / Désactiver le compte")
        self.btn_toggle_account.clicked.connect(self._on_toggle_account)
        self.btn_manage_groups = QPushButton("Gérer les groupes…")
        self.btn_manage_groups.clicked.connect(self._on_manage_groups)
        for btn in (self.btn_edit_attr, self.btn_change_ou, self.btn_reset_pwd,
                    self.btn_toggle_account, self.btn_manage_groups):
            btn.setEnabled(False)
            actions_layout.addWidget(btn)
        actions_layout.addStretch()

        right_layout.addWidget(attrs_group)
        right_layout.addWidget(actions_group)
        right.setMinimumWidth(260)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setSizes([220, 360, 280])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # -- Chargement panneau gauche --------------------------------------------

    def _load_left_panel(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        self._load_ou_tree()
        self._load_group_list()

    def _load_ou_tree(self) -> None:
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
        for dn, name in sorted(ous, key=lambda x: x[0].count(",")):
            parent_dn = dn.split(",", 1)[1] if "," in dn else base_dn
            parent_item = dn_to_item.get(parent_dn, root_item)
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.ItemDataRole.UserRole, dn)
            parent_item.addChild(item)
            dn_to_item[dn] = item

        root_item.setExpanded(True)

    def _load_group_list(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        self.group_list.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, dn)
            self.group_list.addItem(item)

    # -- Sélection OU / Groupe ------------------------------------------------

    def _on_ou_selected(self, item: QTreeWidgetItem, _column: int) -> None:
        ou_dn = item.data(0, Qt.ItemDataRole.UserRole)
        if not ou_dn:
            return
        try:
            users = self.ad_connection.list_users_in_ou(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._all_users = users
        self._current_user = None
        self.search_edit.clear()
        self._populate_user_table(users)
        self.user_count_label.setText(f"{len(users)} utilisateur(s) dans « {item.text(0)} »")
        self._clear_detail_panel()

    def _on_group_selected(self, item: QListWidgetItem) -> None:
        group_dn = item.data(Qt.ItemDataRole.UserRole)
        if not group_dn or self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            users = self.ad_connection.list_users_in_group(group_dn, base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._all_users = users
        self._current_user = None
        self.search_edit.clear()
        self._populate_user_table(users)
        self.user_count_label.setText(f"{len(users)} membre(s) dans « {item.text()} »")
        self._clear_detail_panel()

    # -- Menus contextuels (clic droit) ----------------------------------------

    def _on_ou_context_menu(self, pos) -> None:
        if self.ad_connection.domain is None:
            return
        item = self.ou_tree.itemAt(pos)
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        parent_dn = item.data(0, Qt.ItemDataRole.UserRole) if item else base_dn
        parent_label = item.text(0) if item else self.ad_connection.domain

        menu = QMenu(self)
        action = menu.addAction(f"Créer une sous-OU dans « {parent_label} »…")
        chosen = menu.exec(self.ou_tree.viewport().mapToGlobal(pos))
        if chosen != action:
            return

        name, ok = QInputDialog.getText(self, "Créer une OU", "Nom de la nouvelle OU :")
        name = name.strip()
        if not ok or not name:
            return

        new_dn = f"OU={name},{parent_dn}"
        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.create_ou(new_dn, name)
            self.audit_log.record(
                "creation_ou", name, "succes", self.session_id,
                ou_destination=new_dn, simulation=simulation,
            )
            QMessageBox.information(self, "Succès", f"OU créée : {new_dn}{'  (simulé)' if simulation else ''}.")
            self._load_ou_tree()
        except ADError as exc:
            self.audit_log.record(
                "creation_ou", name, "echec", self.session_id,
                ou_destination=new_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_group_context_menu(self, pos) -> None:
        if self.ad_connection.domain is None:
            return
        item = self.group_list.itemAt(pos)

        menu = QMenu(self)
        create_action = menu.addAction("Créer un groupe…")
        manage_action = None
        if item is not None:
            manage_action = menu.addAction(f"Gérer les membres de « {item.text()} »…")
        chosen = menu.exec(self.group_list.viewport().mapToGlobal(pos))

        if chosen == create_action:
            self._create_group_dialog()
        elif chosen is not None and chosen == manage_action:
            self._manage_group_members_dialog(item.data(Qt.ItemDataRole.UserRole), item.text())

    def _create_group_dialog(self) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        name, ok = QInputDialog.getText(self, "Créer un groupe", "Nom du groupe :")
        name = name.strip()
        if not ok or not name:
            return

        dialog = ChooseOUDialog(ous, parent=self)
        dialog.setWindowTitle("Choisir l'OU où créer le groupe")
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_dn:
            return

        dn = f"CN={name},{dialog.selected_dn}"
        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.create_group(dn, name)
            self.audit_log.record(
                "creation_groupe", name, "succes", self.session_id,
                ou_destination=dialog.selected_dn, simulation=simulation,
            )
            QMessageBox.information(self, "Succès", f"Groupe créé : {dn}{'  (simulé)' if simulation else ''}.")
            self._load_group_list()
        except ADError as exc:
            self.audit_log.record(
                "creation_groupe", name, "echec", self.session_id,
                ou_destination=dialog.selected_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _manage_group_members_dialog(self, group_dn: str, group_name: str) -> None:
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            all_users = self.ad_connection.list_users_in_ou(base_dn)
            members = self.ad_connection.list_users_in_group(group_dn, base_dn)
            member_dns = {m["dn"].lower() for m in members}
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        dialog = ManageGroupMembersDialog(all_users, member_dns, group_name, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        simulation = self.ad_connection.dry_run
        for user_dn in dialog.to_add:
            try:
                self.ad_connection.add_user_to_group(user_dn, group_dn)
                self.audit_log.record(
                    "ajout_groupe", user_dn, "succes", self.session_id,
                    ou_destination=group_dn, simulation=simulation,
                )
            except ADError as exc:
                self.audit_log.record(
                    "ajout_groupe", user_dn, "echec", self.session_id,
                    ou_destination=group_dn, simulation=simulation, detail=str(exc),
                )
                QMessageBox.warning(self, "Erreur", f"Ajout échoué pour {user_dn} : {exc}")

        for user_dn in dialog.to_remove:
            try:
                self.ad_connection.remove_user_from_group(user_dn, group_dn)
                self.audit_log.record(
                    "retrait_groupe", user_dn, "succes", self.session_id,
                    ou_destination=group_dn, simulation=simulation,
                )
            except ADError as exc:
                self.audit_log.record(
                    "retrait_groupe", user_dn, "echec", self.session_id,
                    ou_destination=group_dn, simulation=simulation, detail=str(exc),
                )
                QMessageBox.warning(self, "Erreur", f"Retrait échoué pour {user_dn} : {exc}")

    # -- Recherche ------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            filtered = self._all_users
        else:
            filtered = [
                u for u in self._all_users
                if needle in u["sam"].lower() or needle in u["cn"].lower()
            ]
        self._populate_user_table(filtered)
        self.user_count_label.setText(
            f"{len(filtered)} utilisateur(s) affiché(s)"
            + (f" (filtre : « {text.strip()} »)" if needle else "")
        )

    # -- Table utilisateurs ---------------------------------------------------

    def _populate_user_table(self, users: list[dict]) -> None:
        self.user_table.setRowCount(len(users))
        for i, u in enumerate(users):
            self.user_table.setItem(i, COL_SAM, QTableWidgetItem(u["sam"]))
            self.user_table.setItem(i, COL_CN, QTableWidgetItem(u["cn"]))
            self.user_table.setItem(i, COL_ETAT, QTableWidgetItem("Désactivé" if u.get("disabled") else "Actif"))
        self._displayed_users = users  # référence aux users affichés après filtre

    # -- Sélection utilisateur ------------------------------------------------

    def _on_user_selected(self) -> None:
        rows = self.user_table.selectionModel().selectedRows()
        if not rows or not hasattr(self, "_displayed_users"):
            return
        idx = rows[0].row()
        if idx >= len(self._displayed_users):
            return
        user_info = self._displayed_users[idx]
        try:
            attrs = self.ad_connection.get_user_attributes(user_info["dn"])
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._current_user = attrs
        self._refresh_detail_panel(attrs)
        for btn in (self.btn_edit_attr, self.btn_change_ou, self.btn_reset_pwd,
                    self.btn_toggle_account, self.btn_manage_groups):
            btn.setEnabled(True)

    def _refresh_detail_panel(self, attrs: dict) -> None:
        for attr_key, _ in EDITABLE_ATTRS:
            self._attr_labels[attr_key].setText(attrs.get(attr_key) or "—")
        # Extraire l'OU depuis le DN : tout après le premier composant
        dn = attrs.get("dn", "")
        ou_part = dn.split(",", 1)[1] if "," in dn else dn
        self.lbl_ou.setText(ou_part or "—")
        self.lbl_pwd_last_set.setText(attrs.get("dernier_changement_mdp") or "—")

    def _clear_detail_panel(self) -> None:
        for lbl in self._attr_labels.values():
            lbl.setText("—")
        self.lbl_ou.setText("—")
        self.lbl_pwd_last_set.setText("—")
        for btn in (self.btn_edit_attr, self.btn_change_ou, self.btn_reset_pwd,
                    self.btn_toggle_account, self.btn_manage_groups):
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
                "modification_attribut", self._current_user.get("sAMAccountName", ""),
                "succes", self.session_id, simulation=simulation, detail=f"{attr_key}={new_value}",
            )
        except ADError as exc:
            self.audit_log.record(
                "modification_attribut", self._current_user.get("sAMAccountName", ""),
                "echec", self.session_id, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_change_ou(self) -> None:
        if not self._current_user or self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        dialog = ChooseOUDialog(ous, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_dn:
            return

        sam = self._current_user.get("sAMAccountName", "")
        new_ou_dn = dialog.selected_dn
        simulation = self.ad_connection.dry_run
        old_ou = self._current_user["dn"].split(",", 1)[1] if "," in self._current_user["dn"] else ""
        try:
            self.ad_connection.move_user(self._current_user["dn"], new_ou_dn)
            self.audit_log.record(
                "deplacement_compte", sam, "succes", self.session_id,
                ou_source=old_ou, ou_destination=new_ou_dn, simulation=simulation,
            )
            # Mettre à jour le DN en mémoire
            rdn = self._current_user["dn"].split(",")[0]
            self._current_user["dn"] = f"{rdn},{new_ou_dn}"
            self._refresh_detail_panel(self._current_user)
            QMessageBox.information(self, "Succès", f"Compte déplacé vers {new_ou_dn}{'  (simulé)' if simulation else ''}.")
        except ADError as exc:
            self.audit_log.record(
                "deplacement_compte", sam, "echec", self.session_id,
                ou_source=old_ou, ou_destination=new_ou_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_reset_password(self) -> None:
        if not self._current_user:
            return
        sam = self._current_user.get("sAMAccountName", "")
        policy = PasswordPolicy(longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True)
        new_pwd = generate_random_password(policy)

        reply = QMessageBox.question(
            self, "Réinitialiser le mot de passe",
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
            self, "Confirmer",
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
            action_type = "activation_compte" if is_disabled else "desactivation_compte"
            self.audit_log.record(action_type, sam, "echec", self.session_id, simulation=simulation, detail=str(exc))
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

        dialog = ManageGroupsDialog(all_groups, member_dns, self._current_user.get("cn", ""), parent=self)
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
            self._current_user = self.ad_connection.get_user_attributes(user_dn)
        except ADError:
            pass

    def _reload_current_row(self) -> None:
        rows = self.user_table.selectionModel().selectedRows()
        if not rows or not hasattr(self, "_displayed_users") or not self._current_user:
            return
        idx = rows[0].row()
        if idx >= len(self._displayed_users):
            return
        self._displayed_users[idx]["disabled"] = self._current_user.get("disabled", False)
        etat = "Désactivé" if self._displayed_users[idx]["disabled"] else "Actif"
        self.user_table.setItem(idx, COL_ETAT, QTableWidgetItem(etat))


# -- Dialogues -----------------------------------------------------------------

class EditAttributeDialog(QDialog):
    def __init__(self, user_attrs: dict, editable_attrs: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier un attribut")
        self.result_attr: str = ""
        self.result_value: str = ""
        self._user_attrs = user_attrs

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.attr_combo = QComboBox()
        for key, label in editable_attrs:
            self.attr_combo.addItem(label, key)
        form.addRow("Attribut :", self.attr_combo)
        self.value_edit = QLineEdit()
        self.attr_combo.currentIndexChanged.connect(self._on_attr_changed)
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


class ChooseOUDialog(QDialog):
    """Dialogue de sélection d'une OU cible pour déplacer un compte."""

    def __init__(self, ous: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choisir l'OU de destination")
        self.setMinimumSize(420, 360)
        self.selected_dn: str = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sélectionnez l'OU de destination :"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Unités Organisationnelles")
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

        # Construire l'arbre
        dn_to_item: dict[str, QTreeWidgetItem] = {}
        for dn, name in sorted(ous, key=lambda x: x[0].count(",")):
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.ItemDataRole.UserRole, dn)
            parent_dn = dn.split(",", 1)[1] if "," in dn else ""
            parent_item = dn_to_item.get(parent_dn)
            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            dn_to_item[dn] = item
        self.tree.expandAll()

        self.selected_label = QLabel("Aucune OU sélectionnée.")
        layout.addWidget(self.selected_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        self.selected_dn = item.data(0, Qt.ItemDataRole.UserRole) or ""
        self.selected_label.setText(f"Sélection : {self.selected_dn}")


class ManageGroupsDialog(QDialog):
    def __init__(self, all_groups: list[tuple[str, str]], member_dns: set[str], user_cn: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Groupes de {user_cn}")
        self.setMinimumSize(460, 400)
        self.to_add: list[str] = []
        self.to_remove: list[str] = []
        self._all_groups = {dn: name for dn, name in all_groups}
        self._initial_member_dns = set(member_dns)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cochez les groupes dont l'utilisateur doit être membre :"))

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
        new_dns: set[str] = set()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            dn = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked:
                new_dns.add(dn.lower())

        self.to_add = [dn for dn in self._all_groups if dn.lower() in new_dns and dn.lower() not in self._initial_member_dns]
        self.to_remove = [dn for dn in self._all_groups if dn.lower() not in new_dns and dn.lower() in self._initial_member_dns]
        self.accept()
