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
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from ldap3.utils.dn import escape_rdn

from edusync_ad.core.ad.connection import ADConnection, is_builtin_group_dn
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.identifiers import clean_token
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

USER_COLS = ["Type", "Identifiant", "Nom complet", "État"]

KIND_LABELS = {"user": "Utilisateur", "group": "Groupe", "ou": "OU", "autre": "Autre"}

# Repères visuels façon RSAT/ADUC — Qt n'a pas d'icône standard "groupe" ou
# "utilisateur" (QStyle.StandardPixmap se limite aux dialogues/fichiers), on
# garde donc un dossier natif pour les OU (vraie correspondance) et un
# glyphe pour groupes/utilisateurs, cohérent avec le reste de l'appli
# (⚠, ✓, ✗… déjà utilisés ailleurs).
ICON_GROUPE = "👥"
ICON_UTILISATEUR = "👤"
ICON_UTILISATEUR_DESACTIVE = "🔒"

_emoji_icon_cache: dict[str, QIcon] = {}


def _emoji_icon(emoji: str, size: int = 20) -> QIcon:
    """Rend un glyphe emoji en QIcon — utilisé là où Qt n'a pas d'icône
    standard adaptée (groupe, utilisateur). Ne modifie jamais item.text(),
    contrairement à un préfixe textuel, qui casserait les comparaisons de
    nom exact (ex. confirmation de suppression par ressaisie)."""
    if emoji not in _emoji_icon_cache:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        font = painter.font()
        font.setPointSize(int(size * 0.7))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
        painter.end()
        _emoji_icon_cache[emoji] = QIcon(pixmap)
    return _emoji_icon_cache[emoji]
COL_TYPE, COL_SAM, COL_CN, COL_ETAT = range(4)


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
        self._current_ou_dn: str | None = None
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
        self.create_user_btn = QPushButton("Créer un utilisateur…")
        self.create_user_btn.clicked.connect(self._on_create_user_clicked)
        search_row.addWidget(self.create_user_btn)
        center_layout.addLayout(search_row)

        self.user_count_label = QLabel("Sélectionnez une OU ou un groupe.")
        center_layout.addWidget(self.user_count_label)

        self.user_table = QTableWidget(0, len(USER_COLS))
        self.user_table.setHorizontalHeaderLabels(USER_COLS)
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.user_table.horizontalHeader().setStretchLastSection(True)
        self.user_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.user_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.user_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.user_table.itemSelectionChanged.connect(self._on_user_selected)
        self.user_table.itemDoubleClicked.connect(self._on_user_double_clicked)
        self.user_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.user_table.customContextMenuRequested.connect(self._on_user_context_menu)
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

        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        domain_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)

        self.ou_tree.clear()
        root_item = QTreeWidgetItem([self.ad_connection.domain])
        root_item.setIcon(0, domain_icon)
        root_item.setData(0, Qt.ItemDataRole.UserRole, base_dn)
        self.ou_tree.addTopLevelItem(root_item)

        dn_to_item: dict[str, QTreeWidgetItem] = {base_dn: root_item}
        for dn, name in sorted(ous, key=lambda x: x[0].count(",")):
            parent_dn = dn.split(",", 1)[1] if "," in dn else base_dn
            parent_item = dn_to_item.get(parent_dn, root_item)
            item = QTreeWidgetItem([name])
            item.setIcon(0, folder_icon)
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
        # Masque les dizaines de groupes système d'AD (Administrateurs,
        # Opérateurs de sauvegarde…) sans intérêt pour la gestion des
        # classes/personnels — ne garde que les groupes créés par l'établissement.
        groups = [(dn, name) for dn, name in groups if not is_builtin_group_dn(dn)]

        group_icon = _emoji_icon(ICON_GROUPE)
        self.group_list.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            item = QListWidgetItem(name)
            item.setIcon(group_icon)
            item.setData(Qt.ItemDataRole.UserRole, dn)
            self.group_list.addItem(item)

    # -- Sélection OU / Groupe ------------------------------------------------

    def _on_ou_selected(self, item: QTreeWidgetItem, _column: int) -> None:
        ou_dn = item.data(0, Qt.ItemDataRole.UserRole)
        if not ou_dn:
            return
        self._load_users_for_ou(ou_dn, item.text(0))

    def _load_users_for_ou(self, ou_dn: str, label: str) -> None:
        # list_ou_contents (pas list_users_in_ou) : montre TOUT ce qui vit
        # dans l'OU (utilisateurs, groupes, sous-OU), pas seulement les
        # comptes — sinon un groupe de classe reste invisible ici alors
        # qu'il bloque la suppression de l'OU (voir list_ou_children).
        try:
            items = self.ad_connection.list_ou_contents(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._current_ou_dn = ou_dn
        self._all_users = items
        self._current_user = None
        self.search_edit.clear()
        self._populate_user_table(items)
        self.user_count_label.setText(f"{len(items)} objet(s) dans « {label} »")
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
        is_real_ou = item is not None and item.parent() is not None  # exclut la racine du domaine

        menu = QMenu(self)
        create_action = menu.addAction(f"Créer une sous-OU dans « {parent_label} »…")
        rename_action = menu.addAction(f"Renommer « {parent_label} »…") if is_real_ou else None
        delete_action = menu.addAction(f"Supprimer « {parent_label} »…") if is_real_ou else None
        chosen = menu.exec(self.ou_tree.viewport().mapToGlobal(pos))

        if chosen == create_action:
            self._create_ou(parent_dn)
        elif chosen is not None and chosen == rename_action:
            self._rename_ou(item, parent_dn)
        elif chosen is not None and chosen == delete_action:
            self._delete_ou(item, parent_dn)

    def _create_ou(self, parent_dn: str) -> None:
        name, ok = QInputDialog.getText(self, "Créer une OU", "Nom de la nouvelle OU :")
        name = name.strip()
        if not ok or not name:
            return

        new_dn = f"OU={escape_rdn(name)},{parent_dn}"
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

    def _rename_ou(self, item: QTreeWidgetItem, ou_dn: str) -> None:
        current_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Renommer l'OU", "Nouveau nom :", text=current_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == current_name:
            return

        simulation = self.ad_connection.dry_run
        parent_dn = ou_dn.split(",", 1)[1] if "," in ou_dn else ""
        new_dn = f"OU={escape_rdn(new_name)},{parent_dn}"
        try:
            self.ad_connection.rename_ou(ou_dn, new_name)
            self.audit_log.record(
                "renommage_ou", current_name, "succes", self.session_id,
                ou_source=ou_dn, ou_destination=new_dn, simulation=simulation,
            )
            QMessageBox.information(self, "Succès", f"OU renommée : {new_dn}{'  (simulé)' if simulation else ''}.")
            self._load_ou_tree()
        except ADError as exc:
            self.audit_log.record(
                "renommage_ou", current_name, "echec", self.session_id,
                ou_source=ou_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _delete_ou(self, item: QTreeWidgetItem, ou_dn: str) -> None:
        name = item.text(0)
        try:
            empty = self.ad_connection.ou_is_empty(ou_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        if not empty:
            try:
                children = self.ad_connection.list_ou_children(ou_dn)
            except ADError:
                children = []
            # Cas fréquent : le groupe de classe auto-créé (Module 1/2) vit
            # dans la même OU que la classe — supprimer les élèves ne suffit
            # pas à vider l'OU, ce message rend visible ce qui reste.
            details = "\n".join(f"  • {c}" for c in children[:15]) or "(détail indisponible)"
            if len(children) > 15:
                details += f"\n  … (+{len(children) - 15})"
            QMessageBox.warning(
                self, "OU non vide",
                f"« {name} » ne peut pas être supprimée tant qu'elle n'est pas vide, "
                f"pour éviter une suppression accidentelle en masse. Objets encore "
                f"présents :\n\n{details}",
            )
            return

        reply = QMessageBox.question(
            self, "Confirmer la suppression",
            f"Supprimer définitivement l'OU « {name} » ? Cette action est irréversible.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.delete_ou(ou_dn)
            self.audit_log.record(
                "suppression_ou", name, "succes", self.session_id,
                ou_source=ou_dn, simulation=simulation,
            )
            QMessageBox.information(self, "Succès", f"OU supprimée{'  (simulé)' if simulation else ''}.")
            self._load_ou_tree()
        except ADError as exc:
            self.audit_log.record(
                "suppression_ou", name, "echec", self.session_id,
                ou_source=ou_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_group_context_menu(self, pos) -> None:
        if self.ad_connection.domain is None:
            return
        item = self.group_list.itemAt(pos)

        menu = QMenu(self)
        create_action = menu.addAction("Créer un groupe…")
        manage_action = None
        delete_action = None
        if item is not None:
            manage_action = menu.addAction(f"Gérer les membres de « {item.text()} »…")
            menu.addSeparator()
            delete_action = menu.addAction(f"Supprimer « {item.text()} »…")
        chosen = menu.exec(self.group_list.viewport().mapToGlobal(pos))

        if chosen == create_action:
            self._create_group_dialog()
        elif chosen is not None and chosen == manage_action:
            self._manage_group_members_dialog(item.data(Qt.ItemDataRole.UserRole), item.text())
        elif chosen is not None and chosen == delete_action:
            self._delete_group(item.data(Qt.ItemDataRole.UserRole), item.text())

    def _delete_group(self, group_dn: str, group_name: str) -> None:
        reply = QMessageBox.question(
            self, "Confirmer la suppression",
            f"Supprimer définitivement le groupe « {group_name} » ? Cette action est "
            f"irréversible (les membres perdent simplement cette appartenance).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.delete_group(group_dn)
            self.audit_log.record(
                "suppression_groupe", group_name, "succes", self.session_id, simulation=simulation,
            )
            QMessageBox.information(self, "Succès", f"Groupe supprimé{'  (simulé)' if simulation else ''}.")
            self._load_group_list()
        except ADError as exc:
            self.audit_log.record(
                "suppression_groupe", group_name, "echec", self.session_id, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

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

        dn = f"CN={escape_rdn(name)},{dialog.selected_dn}"
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
                if needle in u.get("sam", "").lower() or needle in u.get("cn", "").lower()
            ]
        self._populate_user_table(filtered)
        self.user_count_label.setText(
            f"{len(filtered)} objet(s) affiché(s)"
            + (f" (filtre : « {text.strip()} »)" if needle else "")
        )

    # -- Table utilisateurs ---------------------------------------------------

    def _populate_user_table(self, users: list[dict]) -> None:
        icon_actif = _emoji_icon(ICON_UTILISATEUR)
        icon_desactive = _emoji_icon(ICON_UTILISATEUR_DESACTIVE)
        icon_groupe = _emoji_icon(ICON_GROUPE)
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.user_table.setRowCount(len(users))
        for i, u in enumerate(users):
            kind = u.get("kind", "user")  # absent (list_users_in_group) = toujours un utilisateur
            type_item = QTableWidgetItem(KIND_LABELS.get(kind, kind))
            type_item.setIcon({"user": icon_actif, "group": icon_groupe, "ou": folder_icon}.get(kind, icon_actif))
            self.user_table.setItem(i, COL_TYPE, type_item)
            self.user_table.setItem(i, COL_SAM, QTableWidgetItem(u.get("sam", "")))
            cn_item = QTableWidgetItem(u.get("cn", ""))
            if kind == "user":
                cn_item.setIcon(icon_desactive if u.get("disabled") else icon_actif)
            self.user_table.setItem(i, COL_CN, cn_item)
            etat = ("Désactivé" if u.get("disabled") else "Actif") if kind == "user" else "—"
            self.user_table.setItem(i, COL_ETAT, QTableWidgetItem(etat))
        self._displayed_users = users  # référence aux éléments affichés après filtre

    # -- Sélection utilisateur ------------------------------------------------

    def _on_user_selected(self) -> None:
        rows = self.user_table.selectionModel().selectedRows()
        if not rows or not hasattr(self, "_displayed_users"):
            return
        if len(rows) > 1:
            # Plusieurs comptes sélectionnés : seule la suppression en masse
            # (menu clic droit) a un sens ici — les actions unitaires
            # (modifier un attribut, changer d'OU…) restent désactivées.
            self._clear_detail_panel()
            return
        idx = rows[0].row()
        if idx >= len(self._displayed_users):
            return
        user_info = self._displayed_users[idx]
        if user_info.get("kind", "user") != "user":
            # Groupe ou sous-OU sélectionné : le panneau détaillé (attributs,
            # mot de passe…) n'a de sens que pour un compte utilisateur.
            self._clear_detail_panel()
            return
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

    def _on_user_double_clicked(self, item: QTableWidgetItem) -> None:
        """Double-clic façon RSAT : ouvre l'objet plutôt que de se contenter
        de le sélectionner — navigue dans une sous-OU, ouvre la gestion des
        membres d'un groupe, ou l'édition d'attribut d'un utilisateur."""
        row = item.row()
        if not hasattr(self, "_displayed_users") or row >= len(self._displayed_users):
            return
        target = self._displayed_users[row]
        kind = target.get("kind", "user")

        if kind == "ou":
            leaf = target["dn"].split(",", 1)[0]
            label = leaf.split("=", 1)[-1] if "=" in leaf else leaf
            self._load_users_for_ou(target["dn"], label)
            self._select_tree_item_by_dn(target["dn"])
        elif kind == "group":
            self._manage_group_members_dialog(target["dn"], target["cn"])
        else:
            if self._current_user and self._current_user.get("dn", "").lower() == target["dn"].lower():
                self._on_edit_attr()

    def _select_tree_item_by_dn(self, dn: str) -> None:
        """Sélectionne et déplie l'item de l'arborescence OUs correspondant à
        ce DN, pour que la navigation par double-clic dans le tableau reste
        cohérente avec le panneau de gauche."""
        it = QTreeWidgetItemIterator(self.ou_tree)
        while it.value():
            tree_item = it.value()
            if (tree_item.data(0, Qt.ItemDataRole.UserRole) or "").lower() == dn.lower():
                self.ou_tree.setCurrentItem(tree_item)
                self.ou_tree.scrollToItem(tree_item)
                parent = tree_item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                return
            it += 1

    def _on_user_context_menu(self, pos) -> None:
        item = self.user_table.itemAt(pos)
        if item is None or not hasattr(self, "_displayed_users"):
            return
        selected_rows = {idx.row() for idx in self.user_table.selectionModel().selectedRows()}
        if item.row() not in selected_rows:
            self.user_table.selectRow(item.row())
            selected_rows = {item.row()}

        menu = QMenu(self)
        if len(selected_rows) > 1:
            # Sélection multiple : seule la suppression en masse a un sens
            # (utilisateurs et/ou groupes — les sous-OU s'excluent d'elles-
            # mêmes de la sélection ci-dessous, leur suppression passe par
            # l'arborescence de gauche à cause du contrôle "OU non vide").
            action_delete = menu.addAction(f"Supprimer les {len(selected_rows)} éléments sélectionnés…")
            chosen = menu.exec(self.user_table.viewport().mapToGlobal(pos))
            if chosen == action_delete:
                self._on_delete_selected_items(selected_rows)
            return

        target = self._displayed_users[item.row()] if item.row() < len(self._displayed_users) else None
        kind = target.get("kind", "user") if target else "user"

        if kind == "group":
            action_manage = menu.addAction("Gérer les membres…")
            menu.addSeparator()
            action_delete = menu.addAction("Supprimer le groupe…")
            chosen = menu.exec(self.user_table.viewport().mapToGlobal(pos))
            if chosen == action_manage:
                self._manage_group_members_dialog(target["dn"], target["cn"])
            elif chosen == action_delete:
                self._delete_group(target["dn"], target["cn"])
            return

        if kind == "ou":
            action_open = menu.addAction("Ouvrir…")
            chosen = menu.exec(self.user_table.viewport().mapToGlobal(pos))
            if chosen == action_open:
                self._on_user_double_clicked(item)
            return

        if not self._current_user:
            return
        action_edit = menu.addAction("Modifier un attribut…")
        action_ou = menu.addAction("Changer d'OU…")
        action_pwd = menu.addAction("Réinitialiser le mot de passe…")
        toggle_label = "Activer le compte" if self._current_user.get("disabled") else "Désactiver le compte"
        action_toggle = menu.addAction(toggle_label)
        action_groups = menu.addAction("Gérer les groupes…")
        menu.addSeparator()
        action_delete = menu.addAction("Supprimer l'utilisateur…")
        chosen = menu.exec(self.user_table.viewport().mapToGlobal(pos))

        if chosen == action_edit:
            self._on_edit_attr()
        elif chosen == action_ou:
            self._on_change_ou()
        elif chosen == action_pwd:
            self._on_reset_password()
        elif chosen == action_toggle:
            self._on_toggle_account()
        elif chosen == action_groups:
            self._on_manage_groups()
        elif chosen == action_delete:
            self._on_delete_selected_items(selected_rows)

    def _on_delete_selected_items(self, row_indices: set[int]) -> None:
        if not hasattr(self, "_displayed_users"):
            return
        all_targets = [self._displayed_users[i] for i in sorted(row_indices) if i < len(self._displayed_users)]
        # Les sous-OU ne se suppriment que depuis l'arborescence (contrôle
        # "OU non vide" différent) — on les exclut d'une suppression en masse.
        targets = [t for t in all_targets if t.get("kind", "user") != "ou"]
        skipped_ou = len(all_targets) - len(targets)
        if not targets:
            if skipped_ou:
                QMessageBox.information(
                    self, "Sous-OU",
                    "La suppression d'OU se fait depuis l'arborescence à gauche.",
                )
            return

        if len(targets) == 1:
            kind_label = KIND_LABELS.get(targets[0].get("kind", "user"), "élément")
            message = (
                f"Supprimer définitivement {kind_label.lower()} « {targets[0]['cn']} » ? "
                f"Cette action est irréversible."
            )
        else:
            names = ", ".join(f"{t['cn']} ({KIND_LABELS.get(t.get('kind', 'user'), '?')})" for t in targets[:10])
            if len(targets) > 10:
                names += f", … (+{len(targets) - 10})"
            suffix = f" ({skipped_ou} sous-OU ignorée(s), à supprimer depuis l'arborescence)" if skipped_ou else ""
            message = (
                f"Supprimer définitivement ces {len(targets)} éléments{suffix} ? "
                f"Cette action est irréversible.\n\n{names}"
            )

        reply = QMessageBox.question(
            self, "Confirmer la suppression", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        simulation = self.ad_connection.dry_run
        success = 0
        failed: list[str] = []
        for t in targets:
            action_type = "suppression_compte" if t.get("kind", "user") == "user" else "suppression_groupe"
            label = t.get("sam") or t["cn"]
            try:
                if t.get("kind", "user") == "group":
                    self.ad_connection.delete_group(t["dn"])
                else:
                    self.ad_connection.delete_user(t["dn"])
                self.audit_log.record(action_type, label, "succes", self.session_id, simulation=simulation)
                success += 1
            except ADError as exc:
                self.audit_log.record(
                    action_type, label, "echec", self.session_id, simulation=simulation, detail=str(exc),
                )
                failed.append(f"{t['cn']} : {exc}")

        if failed:
            QMessageBox.warning(
                self, "Suppression partielle",
                f"{success}/{len(targets)} élément(s) supprimé(s)"
                f"{'  (simulé)' if simulation else ''}.\n\nÉchecs :\n" + "\n".join(failed[:10]),
            )
        else:
            QMessageBox.information(
                self, "Succès", f"{success} élément(s) supprimé(s){'  (simulé)' if simulation else ''}.",
            )

        if self._current_ou_dn:
            leaf = self._current_ou_dn.split(",", 1)[0]
            label = leaf.split("=", 1)[-1] if "=" in leaf else leaf
            self._load_users_for_ou(self._current_ou_dn, label)
        self._clear_detail_panel()

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
            # Le compte a quitté l'OU actuellement affichée dans le panneau
            # central : sans ce rafraîchissement, il y reste visible (liste
            # en mémoire non resynchronisée avec l'AD) jusqu'au prochain clic.
            if self._current_ou_dn:
                leaf = self._current_ou_dn.split(",", 1)[0]
                label = leaf.split("=", 1)[-1] if "=" in leaf else leaf
                self._load_users_for_ou(self._current_ou_dn, label)
                self._clear_detail_panel()
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

        dialog = ResetPasswordDialog(sam, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_pwd = dialog.result_password
        force_change = dialog.result_force_change

        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.set_password(self._current_user["dn"], new_pwd)
            if force_change:
                self.ad_connection.enable_account(self._current_user["dn"], force_password_change=True)
            self.audit_log.record(
                "reinitialisation_mdp", sam, "succes", self.session_id, simulation=simulation,
                detail="force_change=1" if force_change else "",
            )
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

    def _on_create_user_clicked(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        if not ous:
            QMessageBox.warning(self, "Aucune OU", "Créez d'abord une OU pour pouvoir y placer l'utilisateur.")
            return

        dialog = CreateUserDialog(ous, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        prenom, nom = dialog.result_prenom, dialog.result_nom
        sam = dialog.result_sam
        ou_dn = dialog.result_ou_dn
        dn = f"CN={escape_rdn(f'{prenom} {nom}')},{ou_dn}"
        attributes = {
            "sAMAccountName": sam,
            "givenName": prenom,
            "sn": nom,
            "displayName": f"{prenom} {nom}",
            "userPrincipalName": f"{sam}@{self.ad_connection.domain}",
        }
        if dialog.result_email:
            attributes["mail"] = dialog.result_email

        simulation = self.ad_connection.dry_run
        try:
            self.ad_connection.create_user(
                dn, attributes, password=dialog.result_password,
                force_password_change=dialog.result_force_change,
            )
            self.audit_log.record(
                "creation_utilisateur_manuel", sam, "succes", self.session_id,
                ou_destination=ou_dn, simulation=simulation,
            )
            QMessageBox.information(
                self, "Succès",
                f"Utilisateur créé : {sam}{'  (simulé)' if simulation else ''}.\n\n"
                f"Mot de passe : {dialog.result_password}",
            )
            if self._current_ou_dn == ou_dn:
                leaf = ou_dn.split(",", 1)[0]
                label = leaf.split("=", 1)[-1] if "=" in leaf else leaf
                self._load_users_for_ou(ou_dn, label)
        except ADError as exc:
            self.audit_log.record(
                "creation_utilisateur_manuel", sam, "echec", self.session_id,
                ou_destination=ou_dn, simulation=simulation, detail=str(exc),
            )
            QMessageBox.critical(self, "Erreur", str(exc))

    def _on_manage_groups(self) -> None:
        if not self._current_user or self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            all_groups = [
                (dn, name) for dn, name in self.ad_connection.list_groups(base_dn)
                if not is_builtin_group_dn(dn)
            ]
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

class ResetPasswordDialog(QDialog):
    """Réinitialisation d'un mot de passe individuel : aléatoire ou personnalisé,
    avec option de forcer le changement à la prochaine connexion."""

    def __init__(self, sam: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Réinitialiser le mot de passe de {sam}")
        self.setMinimumWidth(420)
        self.result_password = ""
        self.result_force_change = False

        self.password_edit = QLineEdit()
        gen_btn = QPushButton("Générer aléatoirement")
        gen_btn.clicked.connect(self._on_generate)
        password_row = QHBoxLayout()
        password_row.addWidget(self.password_edit)
        password_row.addWidget(gen_btn)

        self.chk_force_change = QCheckBox("Forcer le changement à la prochaine connexion")

        form = QFormLayout()
        form.addRow("Mot de passe :", password_row)
        form.addRow("", self.chk_force_change)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._on_generate()

    def _on_generate(self) -> None:
        policy = PasswordPolicy(longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True)
        self.password_edit.setText(generate_random_password(policy))

    def _on_accept(self) -> None:
        password = self.password_edit.text()
        if not password:
            QMessageBox.warning(self, "Mot de passe vide", "Saisissez un mot de passe.")
            return
        self.result_password = password
        self.result_force_change = self.chk_force_change.isChecked()
        self.accept()


class CreateUserDialog(QDialog):
    """Création manuelle d'un utilisateur avec tous les champs disponibles."""

    def __init__(self, ous: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Créer un utilisateur")
        self.setMinimumWidth(440)

        self.result_prenom = ""
        self.result_nom = ""
        self.result_sam = ""
        self.result_ou_dn = ""
        self.result_email = ""
        self.result_password = ""
        self.result_force_change = False

        self.prenom_edit = QLineEdit()
        self.nom_edit = QLineEdit()
        self.sam_edit = QLineEdit()
        self.sam_edit.setPlaceholderText("généré automatiquement, modifiable")
        self._sam_manually_edited = False
        self.sam_edit.textEdited.connect(lambda: setattr(self, "_sam_manually_edited", True))
        self.prenom_edit.textChanged.connect(self._suggest_sam)
        self.nom_edit.textChanged.connect(self._suggest_sam)

        self.ou_combo = QComboBox()
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("(optionnel)")

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        gen_btn = QPushButton("Générer aléatoirement")
        gen_btn.clicked.connect(self._on_generate_password)
        password_row = QHBoxLayout()
        password_row.addWidget(self.password_edit)
        password_row.addWidget(gen_btn)

        self.chk_force_change = QCheckBox("Forcer le changement de mot de passe à la prochaine connexion")

        form = QFormLayout()
        form.addRow("Prénom *", self.prenom_edit)
        form.addRow("Nom *", self.nom_edit)
        form.addRow("Identifiant (sAMAccountName) *", self.sam_edit)
        form.addRow("OU *", self.ou_combo)
        form.addRow("Email", self.email_edit)
        form.addRow("Mot de passe *", password_row)
        form.addRow("", self.chk_force_change)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._on_generate_password()

    def _suggest_sam(self) -> None:
        if self._sam_manually_edited:
            return
        prenom = clean_token(self.prenom_edit.text())
        nom = clean_token(self.nom_edit.text())
        if prenom and nom:
            self.sam_edit.setText(f"{prenom}.{nom}")

    def _on_generate_password(self) -> None:
        policy = PasswordPolicy(longueur=12, majuscules=True, chiffres=True, caracteres_speciaux=True)
        self.password_edit.setText(generate_random_password(policy))

    def _on_accept(self) -> None:
        prenom = self.prenom_edit.text().strip()
        nom = self.nom_edit.text().strip()
        sam = self.sam_edit.text().strip()
        password = self.password_edit.text()
        if not prenom or not nom or not sam or not password:
            QMessageBox.warning(self, "Champs requis", "Prénom, nom, identifiant et mot de passe sont obligatoires.")
            return

        self.result_prenom = prenom
        self.result_nom = nom
        self.result_sam = sam
        self.result_ou_dn = self.ou_combo.currentData()
        self.result_email = self.email_edit.text().strip()
        self.result_password = password
        self.result_force_change = self.chk_force_change.isChecked()
        self.accept()


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
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        dn_to_item: dict[str, QTreeWidgetItem] = {}
        for dn, name in sorted(ous, key=lambda x: x[0].count(",")):
            item = QTreeWidgetItem([name])
            item.setIcon(0, folder_icon)
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


class ManageGroupMembersDialog(QDialog):
    """Gère les membres d'un groupe (perspective inverse de ManageGroupsDialog :
    on coche ici les utilisateurs, pas les groupes)."""

    def __init__(self, all_users: list[dict], member_dns: set[str], group_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Membres de {group_name}")
        self.setMinimumSize(460, 480)
        self.to_add: list[str] = []
        self.to_remove: list[str] = []
        self._all_users = {u["dn"]: u for u in all_users}
        self._initial_member_dns = {dn.lower() for dn in member_dns}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cochez les utilisateurs membres de ce groupe :"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filtrer…")
        self.search_edit.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self._checked_dns: set[str] = set(self._initial_member_dns)
        self._populate(all_users)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, users: list[dict]) -> None:
        self.list_widget.clear()
        for user in sorted(users, key=lambda u: u["cn"].lower()):
            item = QListWidgetItem(f"{user['cn']} ({user['sam']})")
            item.setData(Qt.ItemDataRole.UserRole, user["dn"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = user["dn"].lower() in self._checked_dns
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        dn = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_dns.add(dn.lower())
        else:
            self._checked_dns.discard(dn.lower())

    def _on_filter_changed(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._populate(list(self._all_users.values()))
            return
        filtered = [
            u for u in self._all_users.values()
            if needle in u["cn"].lower() or needle in u["sam"].lower()
        ]
        self._populate(filtered)

    def _on_accept(self) -> None:
        self.to_add = [
            dn for dn in self._all_users
            if dn.lower() in self._checked_dns and dn.lower() not in self._initial_member_dns
        ]
        self.to_remove = [
            dn for dn in self._all_users
            if dn.lower() not in self._checked_dns and dn.lower() in self._initial_member_dns
        ]
        self.accept()
