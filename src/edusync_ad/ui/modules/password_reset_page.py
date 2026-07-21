"""Module 5 — Réinitialisation de mot de passe en masse (§8 du cahier des charges).

Trois sources possibles : OU entière, groupe AD, ou fichier CSV (identifiants).
La politique de mot de passe utilisée est celle configurée dans les Paramètres
(élèves ou personnels selon un choix de l'opérateur) ; elle peut être surchargée
via un formulaire dédié avant validation.

Flux : choisir la source → charger les utilisateurs → prévisualiser les nouveaux
mots de passe générés → valider → journaliser chaque réinitialisation.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection, is_builtin_group_dn
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.csv_io import has_identifier_column, load_identifiers_csv, load_names_csv
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.password_vault import PasswordVault
from edusync_ad.core.passwords import generate_password
from edusync_ad.ui.progress_panel import BatchProgressPanel

COL_SAM, COL_CN, COL_ETAT, COL_MDP = range(4)
PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "État", "Nouveau mot de passe"]

SOURCE_OU = "ou"
SOURCE_GROUPE = "groupe"
SOURCE_CSV = "csv"


class PasswordResetPage(QWidget):
    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        password_vault: PasswordVault,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.password_vault = password_vault
        self.session_id = session_id

        self._users: list[dict] = []
        self._passwords: list[str] = []
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Une OU/un groupe créé ailleurs dans l'appli (ex. Explorateur AD)
        # doit apparaître ici sans manipulation supplémentaire.
        if self.ad_connection.domain is not None:
            self._load_ous(silent=True)
            self._load_groups(silent=True)

    # -- Construction UI -------------------------------------------------------

    def _build_ui(self) -> None:
        source_group = QGroupBox("1. Source des utilisateurs")
        source_layout = QVBoxLayout(source_group)

        radio_row = QHBoxLayout()
        self.radio_ou = QRadioButton("Unité Organisationnelle (OU)")
        self.radio_groupe = QRadioButton("Groupe AD")
        self.radio_csv = QRadioButton("Fichier CSV (identifiants)")
        self.radio_ou.setChecked(True)
        self._source_group = QButtonGroup(self)
        for r in (self.radio_ou, self.radio_groupe, self.radio_csv):
            self._source_group.addButton(r)
            radio_row.addWidget(r)
        radio_row.addStretch()
        source_layout.addLayout(radio_row)

        self.ou_row_widget = QWidget()
        ou_row = QHBoxLayout(self.ou_row_widget)
        ou_row.setContentsMargins(0, 0, 0, 0)
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(300)
        self.refresh_ous_btn = QPushButton("Actualiser")
        self.refresh_ous_btn.clicked.connect(self._load_ous)
        ou_row.addWidget(QLabel("OU :"))
        ou_row.addWidget(self.ou_combo)
        ou_row.addWidget(self.refresh_ous_btn)
        ou_row.addStretch()

        self.groupe_row_widget = QWidget()
        groupe_row = QHBoxLayout(self.groupe_row_widget)
        groupe_row.setContentsMargins(0, 0, 0, 0)
        self.groupe_combo = QComboBox()
        self.groupe_combo.setMinimumWidth(300)
        self.refresh_groups_btn = QPushButton("Actualiser")
        self.refresh_groups_btn.clicked.connect(self._load_groups)
        groupe_row.addWidget(QLabel("Groupe :"))
        groupe_row.addWidget(self.groupe_combo)
        groupe_row.addWidget(self.refresh_groups_btn)
        groupe_row.addStretch()

        self.csv_row_widget = QWidget()
        csv_row = QHBoxLayout(self.csv_row_widget)
        csv_row.setContentsMargins(0, 0, 0, 0)
        self.csv_label = QLabel("Aucun fichier sélectionné.")
        self.csv_btn = QPushButton("Choisir…")
        self.csv_btn.clicked.connect(self._pick_csv)
        csv_row.addWidget(QLabel("CSV :"))
        csv_row.addWidget(self.csv_btn)
        csv_row.addWidget(self.csv_label)
        csv_row.addStretch()

        source_layout.addWidget(self.ou_row_widget)
        source_layout.addWidget(self.groupe_row_widget)
        source_layout.addWidget(self.csv_row_widget)

        self.radio_ou.toggled.connect(self._on_source_changed)
        self.radio_groupe.toggled.connect(self._on_source_changed)
        self.radio_csv.toggled.connect(self._on_source_changed)
        self._on_source_changed()

        self._csv_path: Path | None = None
        self._csv_ids: list[str] = []
        self._csv_names: list[tuple[str, str]] = []

        load_btn_row = QHBoxLayout()
        self.load_btn = QPushButton("2. Charger les utilisateurs")
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.load_info = QLabel("")
        load_btn_row.addWidget(self.load_btn)
        load_btn_row.addWidget(self.load_info)
        load_btn_row.addStretch()

        policy_group = QGroupBox("3. Mot de passe")
        policy_layout = QFormLayout(policy_group)

        mode_row = QHBoxLayout()
        self.radio_mode_auto = QRadioButton("Générer automatiquement selon une politique")
        self.radio_mode_custom = QRadioButton("Définir un mot de passe personnalisé")
        self.radio_mode_auto.setChecked(True)
        self._mode_group_radio = QButtonGroup(self)
        self._mode_group_radio.addButton(self.radio_mode_auto)
        self._mode_group_radio.addButton(self.radio_mode_custom)
        mode_row.addWidget(self.radio_mode_auto)
        mode_row.addWidget(self.radio_mode_custom)
        mode_row.addStretch()
        policy_layout.addRow("Mode :", mode_row)

        self.custom_password_widget = QWidget()
        custom_pwd_row = QHBoxLayout(self.custom_password_widget)
        custom_pwd_row.setContentsMargins(0, 0, 0, 0)
        self.custom_password_edit = QLineEdit()
        self.custom_password_edit.setPlaceholderText("Mot de passe à appliquer à tous les comptes sélectionnés")
        custom_pwd_row.addWidget(self.custom_password_edit)
        self.custom_password_widget.setVisible(False)
        policy_layout.addRow("Mot de passe :", self.custom_password_widget)

        self.radio_mode_auto.toggled.connect(self._on_password_mode_changed)

        self.auto_policy_widget = QWidget()
        auto_policy_layout = QFormLayout(self.auto_policy_widget)
        auto_policy_layout.setContentsMargins(0, 0, 0, 0)

        policy_type_row = QHBoxLayout()
        self.radio_policy_eleve = QRadioButton("Politique élèves")
        self.radio_policy_personnel = QRadioButton("Politique personnels")
        self.radio_policy_eleve.setChecked(True)
        self._policy_type_group = QButtonGroup(self)
        self._policy_type_group.addButton(self.radio_policy_eleve)
        self._policy_type_group.addButton(self.radio_policy_personnel)
        policy_type_row.addWidget(self.radio_policy_eleve)
        policy_type_row.addWidget(self.radio_policy_personnel)
        policy_type_row.addStretch()
        auto_policy_layout.addRow("Profil :", policy_type_row)

        self.policy_summary_label = QLabel("")
        self.policy_summary_label.setStyleSheet("color: #666;")
        auto_policy_layout.addRow("Politique actuelle :", self.policy_summary_label)

        self.chk_customize = QCheckBox("Personnaliser la politique pour cette réinitialisation")
        self.chk_customize.toggled.connect(self._on_customize_toggled)
        auto_policy_layout.addRow("", self.chk_customize)

        self.custom_policy_widget = QWidget()
        custom_form = QFormLayout(self.custom_policy_widget)
        custom_form.setContentsMargins(0, 0, 0, 0)
        self.spin_length = QSpinBox()
        self.spin_length.setRange(6, 32)
        self.spin_length.setValue(10)
        self.chk_maj = QCheckBox("Majuscules")
        self.chk_chiffres = QCheckBox("Chiffres")
        self.chk_speciaux = QCheckBox("Caractères spéciaux")
        self.chk_identique = QCheckBox("Mot de passe identique pour tout le lot")
        self.chk_maj.setChecked(True)
        self.chk_chiffres.setChecked(True)
        custom_form.addRow("Longueur :", self.spin_length)
        chars_row = QHBoxLayout()
        for chk in (self.chk_maj, self.chk_chiffres, self.chk_speciaux, self.chk_identique):
            chars_row.addWidget(chk)
        chars_row.addStretch()
        custom_form.addRow("Options :", chars_row)
        self.custom_policy_widget.setVisible(False)
        auto_policy_layout.addRow("", self.custom_policy_widget)

        policy_layout.addRow("", self.auto_policy_widget)
        self._on_password_mode_changed()

        self.chk_force_change = QCheckBox("Forcer le changement à la prochaine connexion")
        policy_layout.addRow("", self.chk_force_change)

        self.radio_policy_eleve.toggled.connect(self._sync_policy_fields)
        self.radio_policy_personnel.toggled.connect(self._sync_policy_fields)
        self._sync_policy_fields()

        gen_btn_row = QHBoxLayout()
        self.generate_btn = QPushButton("4. Générer les mots de passe")
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        self.generate_btn.setEnabled(False)
        gen_btn_row.addWidget(self.generate_btn)
        gen_btn_row.addStretch()

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        action_row = QHBoxLayout()
        self.validate_btn = QPushButton("Valider la réinitialisation")
        self.validate_btn.clicked.connect(self._on_validate_clicked)
        self.validate_btn.setEnabled(False)
        self.export_btn = QPushButton("Exporter CSV (mots de passe)")
        self.export_btn.clicked.connect(self._on_export_clicked)
        self.export_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        action_row.addWidget(self.validate_btn)
        action_row.addWidget(self.export_btn)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(source_group)
        layout.addLayout(load_btn_row)
        layout.addWidget(policy_group)
        layout.addLayout(gen_btn_row)
        layout.addWidget(self.preview_table)
        layout.addLayout(action_row)
        layout.addWidget(self.progress_panel)

    # -- Politique -------------------------------------------------------------

    def _on_password_mode_changed(self, *_args) -> None:
        auto_mode = self.radio_mode_auto.isChecked()
        self.auto_policy_widget.setVisible(auto_mode)
        self.custom_password_widget.setVisible(not auto_mode)

    def _sync_policy_fields(self) -> None:
        p = self.config.politique_mdp_eleve if self.radio_policy_eleve.isChecked() else self.config.politique_mdp_personnel
        self.spin_length.setValue(p.longueur)
        self.chk_maj.setChecked(p.majuscules)
        self.chk_chiffres.setChecked(p.chiffres)
        self.chk_speciaux.setChecked(p.caracteres_speciaux)
        self.chk_identique.setChecked(p.mot_de_passe_identique)
        self.policy_summary_label.setText(self._policy_summary_text(p))

    @staticmethod
    def _policy_summary_text(p: PasswordPolicy) -> str:
        options = []
        if p.majuscules:
            options.append("majuscules")
        if p.chiffres:
            options.append("chiffres")
        if p.caracteres_speciaux:
            options.append("caractères spéciaux")
        options_text = ", ".join(options) if options else "minuscules uniquement"
        identique = " — identique pour tout le lot" if p.mot_de_passe_identique else ""
        return f"{p.longueur} caractères ({options_text}){identique}"

    def _on_customize_toggled(self, checked: bool) -> None:
        self.custom_policy_widget.setVisible(checked)
        if not checked:
            self._sync_policy_fields()

    def _current_policy(self) -> PasswordPolicy:
        return PasswordPolicy(
            longueur=self.spin_length.value(),
            majuscules=self.chk_maj.isChecked(),
            chiffres=self.chk_chiffres.isChecked(),
            caracteres_speciaux=self.chk_speciaux.isChecked(),
            mot_de_passe_identique=self.chk_identique.isChecked(),
        )

    # -- Source ----------------------------------------------------------------

    def _pick_csv(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Fichier CSV (identifiants ou prénom/nom)", "", "CSV (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        try:
            # Le personnel administratif ne fournit jamais d'identifiant AD —
            # seulement prénom/nom. On ne bascule sur les identifiants que si
            # une colonne identifiant/login/sam est explicitement présente.
            if has_identifier_column(path):
                ids, names = load_identifiers_csv(path), []
            else:
                names = load_names_csv(path)
                ids = [] if names else load_identifiers_csv(path)  # repli colonne unique historique
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._csv_path = path
        self._csv_ids = ids
        self._csv_names = names
        total = len(ids) + len(names)
        self.csv_label.setText(f"{path.name} — {total} identifiant(s)/nom(s)")

    def _on_source_changed(self, *_args) -> None:
        source = self._current_source()
        self.ou_row_widget.setVisible(source == SOURCE_OU)
        self.groupe_row_widget.setVisible(source == SOURCE_GROUPE)
        self.csv_row_widget.setVisible(source == SOURCE_CSV)
        if source == SOURCE_OU and self.ou_combo.count() == 0:
            self._load_ous()
        elif source == SOURCE_GROUPE and self.groupe_combo.count() == 0:
            self._load_groups()

    def _load_ous(self, *, silent: bool = False) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            if not silent:
                QMessageBox.warning(self, "Erreur", str(exc))
            return
        previous = self.ou_combo.currentData()
        self.ou_combo.clear()
        for dn, name in sorted(ous, key=lambda x: x[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        idx = self.ou_combo.findData(previous)
        if idx >= 0:
            self.ou_combo.setCurrentIndex(idx)

    def _load_groups(self, *, silent: bool = False) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError as exc:
            if not silent:
                QMessageBox.warning(self, "Erreur", str(exc))
            return
        groups = [(dn, name) for dn, name in groups if not is_builtin_group_dn(dn)]
        previous = self.groupe_combo.currentData()
        self.groupe_combo.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            self.groupe_combo.addItem(name, dn)
        idx = self.groupe_combo.findData(previous)
        if idx >= 0:
            self.groupe_combo.setCurrentIndex(idx)

    # -- Chargement utilisateurs ----------------------------------------------

    def _on_load_clicked(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.critical(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        source = self._current_source()

        try:
            if source == SOURCE_OU:
                ou_dn = self.ou_combo.currentData()
                if not ou_dn:
                    QMessageBox.warning(self, "Aucune OU", "Sélectionnez une OU ou actualisez la liste.")
                    return
                users = self.ad_connection.list_users_in_ou(ou_dn)
            elif source == SOURCE_GROUPE:
                group_dn = self.groupe_combo.currentData()
                if not group_dn:
                    QMessageBox.warning(self, "Aucun groupe", "Sélectionnez un groupe ou actualisez la liste.")
                    return
                users = self.ad_connection.list_users_in_group(group_dn, base_dn)
            else:
                if not self._csv_ids and not self._csv_names:
                    QMessageBox.warning(self, "Aucun CSV", "Importez un fichier CSV.")
                    return
                users = self._resolve_csv_ids(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        self._users = users
        self._passwords = []
        self.generate_btn.setEnabled(bool(users))
        self.validate_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self._populate_table(with_passwords=False)
        self.load_info.setText(f"{len(users)} utilisateur(s) chargé(s).")

    def _resolve_csv_ids(self, base_dn: str) -> list[dict]:
        users = []
        not_found = []
        for sam in self._csv_ids:
            result = self.ad_connection.search_user_by_sam(sam, base_dn)
            if result is None:
                not_found.append(sam)
            else:
                dn, cn = result
                users.append({"dn": dn, "sam": sam, "cn": cn, "disabled": False})
        # Recherche par nom dans tout l'annuaire — cas du personnel
        # administratif, qui ne fournit jamais d'identifiant AD.
        for prenom, nom in self._csv_names:
            full_name = f"{prenom} {nom}"
            user_dn = self.ad_connection.search_user_by_cn(full_name, base_dn)
            if user_dn is None:
                not_found.append(full_name)
                continue
            attrs = self.ad_connection.get_user_attributes(user_dn)
            users.append({
                "dn": user_dn,
                "sam": attrs.get("sAMAccountName", full_name),
                "cn": attrs.get("cn", full_name),
                "disabled": attrs.get("disabled", False),
            })
        if not_found:
            QMessageBox.warning(
                self, "Utilisateurs introuvables",
                f"{len(not_found)} identifiant(s)/nom(s) non trouvé(s) :\n" + ", ".join(not_found[:20])
            )
        return users

    def _current_source(self) -> str:
        if self.radio_ou.isChecked():
            return SOURCE_OU
        if self.radio_groupe.isChecked():
            return SOURCE_GROUPE
        return SOURCE_CSV

    # -- Génération ------------------------------------------------------------

    def _on_generate_clicked(self) -> None:
        if not self._users:
            return
        if self.radio_mode_custom.isChecked():
            custom = self.custom_password_edit.text()
            if not custom:
                QMessageBox.warning(self, "Mot de passe vide", "Saisissez le mot de passe à appliquer.")
                return
            self._passwords = [custom] * len(self._users)
        else:
            policy = self._current_policy()
            if policy.mot_de_passe_identique:
                shared = generate_password(policy)
                self._passwords = [shared] * len(self._users)
            else:
                self._passwords = [generate_password(policy) for _ in self._users]
        self._populate_table(with_passwords=True)
        self.validate_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

    # -- Tableau ---------------------------------------------------------------

    def _populate_table(self, *, with_passwords: bool) -> None:
        self.preview_table.setRowCount(len(self._users))
        for i, user in enumerate(self._users):
            item_sam = QTableWidgetItem(user["sam"])
            item_sam.setFlags(item_sam.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, COL_SAM, item_sam)
            item_cn = QTableWidgetItem(user["cn"])
            item_cn.setFlags(item_cn.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, COL_CN, item_cn)
            etat = "Désactivé" if user.get("disabled") else "Actif"
            item_etat = QTableWidgetItem(etat)
            item_etat.setFlags(item_etat.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, COL_ETAT, item_etat)
            mdp = self._passwords[i] if with_passwords and i < len(self._passwords) else ""
            self.preview_table.setItem(i, COL_MDP, QTableWidgetItem(mdp))  # éditable : ajustement manuel possible

    def _sync_passwords_from_table(self) -> None:
        for i in range(len(self._passwords)):
            item = self.preview_table.item(i, COL_MDP)
            if item is not None:
                self._passwords[i] = item.text()

    # -- Validation ------------------------------------------------------------

    def _on_validate_clicked(self) -> None:
        if not self._users or len(self._passwords) != len(self._users):
            QMessageBox.warning(self, "Mots de passe manquants", "Générez d'abord les mots de passe.")
            return
        self._sync_passwords_from_table()

        simulation = self.ad_connection.dry_run
        force_change = self.chk_force_change.isChecked()
        suffix_sim = " (mode simulation)" if simulation else ""
        suffix_force = " + forcer changement" if force_change else ""
        reply = QMessageBox.question(
            self,
            "Confirmer",
            f"Réinitialiser les mots de passe de {len(self._users)} compte(s){suffix_force}{suffix_sim} ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        items = list(zip(self._users, self._passwords))
        labels = [user["sam"] for user, _ in items]
        self.validate_btn.setEnabled(False)

        def run_one(entry: tuple[dict, str]) -> None:
            user, pwd = entry
            self.ad_connection.set_password(user["dn"], pwd)
            if force_change:
                self.ad_connection.enable_account(user["dn"], force_password_change=True)

        def on_result(position: int, success: bool, message: str) -> None:
            user, pwd = items[position]
            if success:
                self.audit_log.record(
                    "reinitialisation_mdp", user["sam"], "succes", self.session_id,
                    simulation=simulation, detail="force_change=1" if force_change else "",
                )
                if not simulation:
                    self.password_vault.store(user["sam"], pwd)
                etat = "Réinitialisé" + (" (sim.)" if simulation else "")
            else:
                self.audit_log.record(
                    "reinitialisation_mdp", user["sam"], "echec", self.session_id,
                    simulation=simulation, detail=message,
                )
                etat = f"Erreur : {message}"
            self.preview_table.setItem(position, COL_ETAT, QTableWidgetItem(etat))

        def on_finished() -> None:
            self.validate_btn.setEnabled(True)
            QMessageBox.information(
                self, "Terminé",
                f"{self.progress_panel.success_count}/{len(self._users)} réinitialisation(s){suffix_sim}.",
            )

        self.progress_panel.finished.connect(on_finished, type=Qt.ConnectionType.SingleShotConnection)
        self.progress_panel.start(
            "Réinitialisation des mots de passe en cours…", items, labels, run_one, on_item_result=on_result,
        )

    # -- Export CSV ------------------------------------------------------------

    def _on_export_clicked(self) -> None:
        if not self._users or not self._passwords:
            return
        self._sync_passwords_from_table()
        path_str, _ = QFileDialog.getSaveFileName(self, "Exporter les mots de passe", "mots_de_passe.csv", "CSV (*.csv)")
        if not path_str:
            return
        try:
            with open(path_str, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["identifiant", "nom_complet", "mot_de_passe"])
                for user, pwd in zip(self._users, self._passwords):
                    writer.writerow([user["sam"], user["cn"], pwd])
            QMessageBox.information(self, "Export réussi", f"Fichier enregistré : {path_str}")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur d'export", str(exc))

    # -- Annulation -----------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        self._users = []
        self._passwords = []
        self.preview_table.setRowCount(0)
        self.validate_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.generate_btn.setEnabled(False)
        self.load_info.setText("")
