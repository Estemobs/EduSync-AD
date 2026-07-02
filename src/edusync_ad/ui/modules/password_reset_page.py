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

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.models import PasswordPolicy
from edusync_ad.core.passwords import generate_password
from edusync_ad.ui.progress_dialog import BatchProgressDialog

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
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ad_connection = ad_connection
        self.config = config
        self.audit_log = audit_log
        self.session_id = session_id

        self._users: list[dict] = []
        self._passwords: list[str] = []
        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

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

        source_form = QFormLayout()
        self.ou_input = QLineEdit()
        self.ou_input.setPlaceholderText("OU=Eleves,DC=ecole,DC=local")
        self.groupe_combo = QComboBox()
        self.groupe_combo.setMinimumWidth(300)
        self.refresh_groups_btn = QPushButton("Actualiser")
        self.refresh_groups_btn.clicked.connect(self._load_groups)
        groupe_row = QHBoxLayout()
        groupe_row.addWidget(self.groupe_combo)
        groupe_row.addWidget(self.refresh_groups_btn)
        self.csv_label = QLabel("Aucun fichier sélectionné.")
        self.csv_btn = QPushButton("Choisir…")
        self.csv_btn.clicked.connect(self._pick_csv)
        csv_row = QHBoxLayout()
        csv_row.addWidget(self.csv_btn)
        csv_row.addWidget(self.csv_label)
        csv_row.addStretch()
        source_form.addRow("OU :", self.ou_input)
        source_form.addRow("Groupe :", groupe_row)
        source_form.addRow("CSV :", csv_row)
        source_layout.addLayout(source_form)

        self._csv_path: Path | None = None
        self._csv_ids: list[str] = []

        load_btn_row = QHBoxLayout()
        self.load_btn = QPushButton("2. Charger les utilisateurs")
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.load_info = QLabel("")
        load_btn_row.addWidget(self.load_btn)
        load_btn_row.addWidget(self.load_info)
        load_btn_row.addStretch()

        policy_group = QGroupBox("3. Politique de mot de passe")
        policy_layout = QFormLayout(policy_group)

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
        policy_layout.addRow("Profil :", policy_type_row)

        self.spin_length = QSpinBox()
        self.spin_length.setRange(6, 32)
        self.spin_length.setValue(10)
        self.chk_maj = QCheckBox("Majuscules")
        self.chk_chiffres = QCheckBox("Chiffres")
        self.chk_speciaux = QCheckBox("Caractères spéciaux")
        self.chk_identique = QCheckBox("Mot de passe identique pour tout le lot")
        self.chk_force_change = QCheckBox("Forcer le changement à la prochaine connexion")
        self.chk_maj.setChecked(True)
        self.chk_chiffres.setChecked(True)
        policy_layout.addRow("Longueur :", self.spin_length)
        chars_row = QHBoxLayout()
        for chk in (self.chk_maj, self.chk_chiffres, self.chk_speciaux, self.chk_identique):
            chars_row.addWidget(chk)
        chars_row.addStretch()
        policy_layout.addRow("Options :", chars_row)
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
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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

        layout = QVBoxLayout(self)
        layout.addWidget(source_group)
        layout.addLayout(load_btn_row)
        layout.addWidget(policy_group)
        layout.addLayout(gen_btn_row)
        layout.addWidget(self.preview_table)
        layout.addLayout(action_row)

    # -- Politique -------------------------------------------------------------

    def _sync_policy_fields(self) -> None:
        if self.radio_policy_eleve.isChecked():
            p = self.config.politique_mdp_eleve
        else:
            p = self.config.politique_mdp_personnel
        self.spin_length.setValue(p.longueur)
        self.chk_maj.setChecked(p.majuscules)
        self.chk_chiffres.setChecked(p.chiffres)
        self.chk_speciaux.setChecked(p.caracteres_speciaux)
        self.chk_identique.setChecked(p.mot_de_passe_identique)

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
        path_str, _ = QFileDialog.getOpenFileName(self, "Fichier CSV d'identifiants", "", "CSV (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        try:
            ids = _load_ids_csv(path)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._csv_path = path
        self._csv_ids = ids
        self.csv_label.setText(f"{path.name} — {len(ids)} identifiant(s)")

    def _load_groups(self) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = self.ad_connection.list_groups(base_dn)
        except ADError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        self.groupe_combo.clear()
        for dn, name in sorted(groups, key=lambda x: x[1].lower()):
            self.groupe_combo.addItem(name, dn)

    # -- Chargement utilisateurs ----------------------------------------------

    def _on_load_clicked(self) -> None:
        if self.ad_connection.domain is None:
            QMessageBox.critical(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return

        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        source = self._current_source()

        try:
            if source == SOURCE_OU:
                ou_dn = self.ou_input.text().strip()
                if not ou_dn:
                    QMessageBox.warning(self, "Champ vide", "Saisissez le DN de l'OU.")
                    return
                users = self.ad_connection.list_users_in_ou(ou_dn)
            elif source == SOURCE_GROUPE:
                group_dn = self.groupe_combo.currentData()
                if not group_dn:
                    QMessageBox.warning(self, "Aucun groupe", "Sélectionnez un groupe ou actualisez la liste.")
                    return
                users = self.ad_connection.list_users_in_group(group_dn, base_dn)
            else:
                if not self._csv_ids:
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
        if not_found:
            QMessageBox.warning(
                self, "Utilisateurs introuvables",
                f"{len(not_found)} identifiant(s) non trouvé(s) :\n" + ", ".join(not_found[:20])
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
            self.preview_table.setItem(i, COL_SAM, QTableWidgetItem(user["sam"]))
            self.preview_table.setItem(i, COL_CN, QTableWidgetItem(user["cn"]))
            etat = "Désactivé" if user.get("disabled") else "Actif"
            self.preview_table.setItem(i, COL_ETAT, QTableWidgetItem(etat))
            mdp = self._passwords[i] if with_passwords and i < len(self._passwords) else ""
            self.preview_table.setItem(i, COL_MDP, QTableWidgetItem(mdp))

    # -- Validation ------------------------------------------------------------

    def _on_validate_clicked(self) -> None:
        if not self._users or len(self._passwords) != len(self._users):
            QMessageBox.warning(self, "Mots de passe manquants", "Générez d'abord les mots de passe.")
            return

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

        def run_one(entry: tuple[dict, str]) -> None:
            user, pwd = entry
            self.ad_connection.set_password(user["dn"], pwd)
            if force_change:
                self.ad_connection.enable_account(user["dn"], force_password_change=True)

        def on_result(position: int, success: bool, message: str) -> None:
            user, _ = items[position]
            if success:
                self.audit_log.record(
                    "reinitialisation_mdp", user["sam"], "succes", self.session_id,
                    simulation=simulation, detail="force_change=1" if force_change else "",
                )
                etat = "Réinitialisé" + (" (sim.)" if simulation else "")
            else:
                self.audit_log.record(
                    "reinitialisation_mdp", user["sam"], "echec", self.session_id,
                    simulation=simulation, detail=message,
                )
                etat = f"Erreur : {message}"
            self.preview_table.setItem(position, COL_ETAT, QTableWidgetItem(etat))

        dialog = BatchProgressDialog(
            "Réinitialisation des mots de passe en cours…", items, labels, run_one,
            on_item_result=on_result, parent=self,
        )
        dialog.start()
        dialog.exec()

        QMessageBox.information(
            self, "Terminé", f"{dialog.success_count}/{len(self._users)} réinitialisation(s){suffix_sim}."
        )

    # -- Export CSV ------------------------------------------------------------

    def _on_export_clicked(self) -> None:
        if not self._users or not self._passwords:
            return
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


def _load_ids_csv(path: Path) -> list[str]:
    encodings = ["utf-8-sig", "utf-8", "latin-1"]
    for encoding in encodings:
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Impossible de décoder le fichier CSV.")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return []
    headers = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(headers) if "identifiant" in h or "login" in h or "sam" in h), 0)
    return [row[col].strip() for row in rows[1:] if len(row) > col and row[col].strip()]
