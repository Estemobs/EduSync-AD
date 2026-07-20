"""Module 1 — Création de comptes (§4 du cahier des charges).

Import CSV -> sélection du type de compte -> sélection du format
d'identifiant -> génération de la prévisualisation (identifiants, mots de
passe, emails, groupe de classe, doublons résolus) -> validation (écriture
dans l'AD ou simulation) -> export CSV des comptes créés.
"""

from __future__ import annotations

from datetime import date
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
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ldap3.utils.dn import escape_rdn

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.csv_io import (
    CsvPreview,
    EXPECTED_COLUMNS,
    REQUIRED_COLUMNS,
    export_created_accounts,
    export_failed_rows,
    load_preview,
    load_rows,
)
from edusync_ad.core.identifiers import (
    CAMEL_PRESETS,
    PRESETS,
    IdentifierEngine,
    apply_prenom_compose_rule,
    clean_token,
    render_template,
)
from edusync_ad.core.models import AccountType, GeneratedUser, RawUserRow
from edusync_ad.core.passwords import generate_passwords_for_batch
from edusync_ad.ui.progress_panel import BatchProgressPanel

IDENTIFIER_PRESET_KEYS = list(PRESETS.keys()) + list(CAMEL_PRESETS)

PREVIEW_COLUMNS = ["Identifiant", "Nom complet", "Mot de passe", "Email", "OU cible", "Groupe", "État"]
COL_IDENTIFIANT, COL_NOM, COL_MDP, COL_EMAIL, COL_OU, COL_GROUPE, COL_ETAT = range(7)


def _ou_leaf_name(ou_dn: str) -> str:
    leaf = ou_dn.split(",")[0].strip()
    if "=" in leaf:
        return leaf.split("=", 1)[1]
    return leaf


class CreateAccountsPage(QWidget):
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

        self._csv_path: Path | None = None
        self._csv_preview: CsvPreview | None = None
        self._mapping_combos: dict[str, QComboBox] = {}
        self._generated: list[GeneratedUser] = []

        self._build_ui()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Une OU créée ailleurs (ex. Explorateur AD) doit apparaître ici sans
        # avoir à cliquer manuellement sur "Charger les OUs depuis l'AD".
        if self.ad_connection.domain is not None:
            self._on_load_ous_clicked(silent=True)

    # -- Construction de l'interface -----------------------------------------

    def _build_ui(self) -> None:
        import_group = QGroupBox("1. Import du fichier CSV")
        import_layout = QVBoxLayout(import_group)

        import_row = QHBoxLayout()
        self.import_button = QPushButton("Choisir un fichier CSV…")
        self.import_button.clicked.connect(self._on_import_clicked)
        self.import_label = QLabel("Aucun fichier importé.")
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.import_label)
        import_row.addStretch()
        import_layout.addLayout(import_row)

        self.mapping_form = QFormLayout()
        mapping_widget = QWidget()
        mapping_widget.setLayout(self.mapping_form)
        import_layout.addWidget(mapping_widget)

        options_group = QGroupBox("2. Type de compte et identifiant")
        options_layout = QVBoxLayout(options_group)

        type_row = QHBoxLayout()
        self.eleve_radio = QRadioButton("Élève")
        self.eleve_radio.setChecked(True)
        self.personnel_radio = QRadioButton("Personnel")
        self._type_group = QButtonGroup(self)
        self._type_group.addButton(self.eleve_radio)
        self._type_group.addButton(self.personnel_radio)
        self.eleve_radio.toggled.connect(self._on_account_type_changed)
        type_row.addWidget(QLabel("Type de compte :"))
        type_row.addWidget(self.eleve_radio)
        type_row.addWidget(self.personnel_radio)
        type_row.addStretch()
        options_layout.addLayout(type_row)

        format_row = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.setEditable(True)
        self.format_combo.addItems(IDENTIFIER_PRESET_KEYS)
        self.format_combo.editTextChanged.connect(self._update_format_preview)
        self.format_preview_label = QLabel("")
        format_row.addWidget(QLabel("Format d'identifiant :"))
        format_row.addWidget(self.format_combo)
        format_row.addWidget(QLabel("Aperçu :"))
        format_row.addWidget(self.format_preview_label)
        format_row.addStretch()
        options_layout.addLayout(format_row)

        self._on_account_type_changed(True)

        ou_group = QGroupBox("3. OU cible (si non précisée dans le fichier)")
        ou_layout = QVBoxLayout(ou_group)

        load_ou_row = QHBoxLayout()
        self.load_ous_button = QPushButton("Charger les OUs depuis l'AD")
        self.load_ous_button.clicked.connect(self._on_load_ous_clicked)
        load_ou_row.addWidget(self.load_ous_button)
        load_ou_row.addStretch()
        ou_layout.addLayout(load_ou_row)

        ou_form = QFormLayout()
        self.classe_parent_ou_combo = QComboBox()
        self.classe_parent_ou_combo.addItem("(aucune)", "")
        ou_form.addRow("OU parente pour les classes (colonne « classe ») :", self.classe_parent_ou_combo)
        self.default_ou_combo = QComboBox()
        self.default_ou_combo.addItem("(aucune)", "")
        ou_form.addRow("OU par défaut (si ni OU ni classe renseignée) :", self.default_ou_combo)
        ou_layout.addLayout(ou_form)

        self.auto_create_ou_checkbox = QCheckBox("Créer automatiquement les OU de classe manquantes (avec confirmation)")
        self.auto_create_ou_checkbox.setChecked(True)
        ou_layout.addWidget(self.auto_create_ou_checkbox)

        generate_row = QHBoxLayout()
        self.generate_button = QPushButton("4. Générer la prévisualisation")
        self.generate_button.clicked.connect(self._on_generate_clicked)
        generate_row.addWidget(self.generate_button)
        generate_row.addStretch()

        self.preview_table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.preview_table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )

        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Valider")
        self.validate_button.clicked.connect(self._on_validate_clicked)
        self.validate_button.setEnabled(False)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch()

        self.progress_panel = BatchProgressPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(import_group)
        layout.addWidget(options_group)
        layout.addWidget(ou_group)
        layout.addLayout(generate_row)
        layout.addWidget(self.preview_table)
        layout.addLayout(action_row)
        layout.addWidget(self.progress_panel)

    # -- Import CSV et mapping de colonnes -----------------------------------

    def _on_import_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Importer un fichier CSV", "", "CSV (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        try:
            preview = load_preview(path)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Erreur d'import", str(exc))
            return

        self._csv_path = path
        self._csv_preview = preview
        self.import_label.setText(f"{path.name} — {len(preview.rows)} ligne(s) en aperçu")
        self._rebuild_mapping_form(preview)

    def _rebuild_mapping_form(self, preview: CsvPreview) -> None:
        while self.mapping_form.rowCount():
            self.mapping_form.removeRow(0)
        self._mapping_combos.clear()

        for column in EXPECTED_COLUMNS:
            combo = QComboBox()
            combo.addItem("(non utilisé)", "")
            for header in preview.headers:
                combo.addItem(header, header)
            suggested = preview.suggested_mapping.get(column, "")
            if suggested:
                combo.setCurrentIndex(combo.findData(suggested))
            label = column + (" *" if column in REQUIRED_COLUMNS else "")
            if column == "ou":
                # Piège fréquent : associer par erreur cette colonne au nom
                # de classe (ex. "6emeA") au lieu de "classe" — ce champ
                # attend un chemin AD complet (OU=...,DC=...), pas un nom.
                label += "  (chemin AD complet — laissez « (non utilisé) » si vous n'avez qu'un nom de classe)"
            self.mapping_form.addRow(label, combo)
            self._mapping_combos[column] = combo

    def _current_mapping(self) -> dict[str, str]:
        return {column: combo.currentData() or "" for column, combo in self._mapping_combos.items()}

    # -- Type de compte / format d'identifiant -------------------------------

    def _on_account_type_changed(self, _checked: bool) -> None:
        default_format = (
            self.config.identifiant_format_eleve
            if self.eleve_radio.isChecked()
            else self.config.identifiant_format_personnel
        )
        self.format_combo.setCurrentText(default_format)
        self._update_format_preview()

    def _update_format_preview(self) -> None:
        format_key = self.format_combo.currentText().strip()
        if not format_key:
            self.format_preview_label.setText("")
            return
        try:
            engine = IdentifierEngine(format_key=format_key, annee=date.today().year)
            self.format_preview_label.setText(engine.base_identifier("Thomas", "Martin"))
        except Exception:
            self.format_preview_label.setText("(format invalide)")

    def _current_account_type(self) -> AccountType:
        return AccountType.ELEVE if self.eleve_radio.isChecked() else AccountType.PERSONNEL

    # -- OU cible ---------------------------------------------------------------

    def _on_load_ous_clicked(self, *, silent: bool = False) -> None:
        if self.ad_connection.domain is None:
            if not silent:
                QMessageBox.warning(self, "Non connecté", "Connectez-vous d'abord à l'AD.")
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            ous = self.ad_connection.list_ous(base_dn)
        except ADError as exc:
            if not silent:
                QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        for combo in (self.classe_parent_ou_combo, self.default_ou_combo):
            previous = combo.currentData()
            if not previous and combo is self.classe_parent_ou_combo:
                # Première ouverture (pas encore de sélection ponctuelle) :
                # préremplit avec le réglage permanent des Paramètres, pour ne
                # pas avoir à le resélectionner à chaque import.
                previous = self.config.ou_parente_classes
            combo.clear()
            combo.addItem("(aucune)", "")
            for dn, name in sorted(ous, key=lambda x: x[0]):
                combo.addItem(f"{name}  ({dn})", dn)
            idx = combo.findData(previous)
            combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _resolve_ou(self, row: RawUserRow) -> tuple[str, bool]:
        """Retourne (ou_dn, générée_depuis_la_classe).

        La colonne "classe" doit toujours suffire à produire une OU valide
        sans configuration préalable obligatoire : sélection ponctuelle dans
        "OU parente pour les classes" > réglage permanent des Paramètres >
        racine du domaine connecté en dernier recours."""
        if row.ou:
            return row.ou, False
        if row.classe:
            parent = (
                self.classe_parent_ou_combo.currentData()
                or self.config.ou_parente_classes
                or (ADConnection.domain_to_base_dn(self.ad_connection.domain) if self.ad_connection.domain else "")
            )
            if parent:
                return f"OU={row.classe},{parent}", True
        return self.default_ou_combo.currentData() or "", False

    def _ensure_classe_ous_exist(self, resolved_ous: list[tuple[str, bool]]) -> set[str]:
        """Détecte les OU de classe manquantes et propose de les créer.
        Retourne l'ensemble des DN désormais considérés comme existants."""
        if not self.auto_create_ou_checkbox.isChecked():
            return set()

        candidates = sorted({ou for ou, from_classe in resolved_ous if from_classe and ou})
        if not candidates:
            return set()

        missing = []
        for ou_dn in candidates:
            try:
                if not self.ad_connection.ou_exists(ou_dn):
                    missing.append(ou_dn)
            except ADError:
                pass  # sera re-signalé comme erreur par ligne au moment de la génération
        if not missing:
            return set()

        reply = QMessageBox.question(
            self, "OU de classe manquantes",
            "Les OU suivantes n'existent pas encore et seront créées automatiquement :\n\n"
            + "\n".join(missing)
            + "\n\nConfirmer la création ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return set()

        simulation = self.ad_connection.dry_run
        created: set[str] = set()
        for ou_dn in missing:
            name = ou_dn.split(",", 1)[0].split("=", 1)[-1]
            try:
                self.ad_connection.create_ou(ou_dn, name)
                created.add(ou_dn)
                self.audit_log.record(
                    "creation_ou", name, "succes", self.session_id,
                    ou_destination=ou_dn, simulation=simulation,
                )
            except ADError as exc:
                self.audit_log.record(
                    "creation_ou", name, "echec", self.session_id,
                    ou_destination=ou_dn, simulation=simulation, detail=str(exc),
                )
        return created

    # -- Génération de la prévisualisation ------------------------------------

    def _on_generate_clicked(self) -> None:
        if self._csv_path is None:
            QMessageBox.warning(self, "Aucun fichier", "Importez d'abord un fichier CSV.")
            return
        mapping = self._current_mapping()
        missing = [c for c in REQUIRED_COLUMNS if not mapping.get(c)]
        if missing:
            QMessageBox.warning(
                self, "Colonnes manquantes", f"Associez les colonnes obligatoires : {', '.join(missing)}"
            )
            return

        result = load_rows(self._csv_path, mapping)
        if not result.rows:
            QMessageBox.warning(self, "Aucune ligne valide", "Le fichier ne contient aucune ligne exploitable.")
            return
        if result.skipped_row_numbers:
            QMessageBox.warning(
                self,
                "Lignes ignorées",
                "Lignes incomplètes ignorées : "
                + ", ".join(str(n) for n in result.skipped_row_numbers),
            )

        if self.ad_connection.domain is None:
            QMessageBox.critical(self, "Non connecté", "Aucune connexion à l'Active Directory.")
            return

        account_type = self._current_account_type()
        policy = (
            self.config.politique_mdp_eleve
            if account_type == AccountType.ELEVE
            else self.config.politique_mdp_personnel
        )
        format_key = self.format_combo.currentText().strip()
        year = date.today().year

        try:
            base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
            existing_ids = self.ad_connection.search_existing_identifiers(base_dn)
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return

        engine = IdentifierEngine(
            format_key=format_key,
            doublon_rule=self.config.regle_doublons,
            prenom_compose_rule=self.config.regle_prenom_compose,
            annee=year,
        )

        passwords = generate_passwords_for_batch(
            policy, [(row.prenom, row.nom) for row in result.rows], year=year
        )

        resolved_ous = [self._resolve_ou(row) for row in result.rows]
        created_ous = self._ensure_classe_ous_exist(resolved_ous)

        generated: list[GeneratedUser] = []
        for row, password, (ou_dn, from_classe) in zip(result.rows, passwords, resolved_ous):
            if not ou_dn:
                generated.append(
                    GeneratedUser(
                        source=row, identifiant="", mot_de_passe="", adresse_mail="", ou_cible="",
                        erreur="Aucune OU déterminée — précisez une classe, une OU par défaut, "
                               "ou une colonne OU dans le fichier.",
                    )
                )
                continue
            if not from_classe and "=" not in ou_dn:
                # Un vrai DN AD contient toujours au moins un "=" (OU=, DC=…).
                # Une valeur sans "=" (ex. juste "6emeA") vient presque
                # toujours d'une colonne "ou" associée par erreur à la
                # colonne "classe" dans le mapping — sans ce garde-fou, AD ne
                # refuserait qu'au moment de la création réelle, avec un
                # "noSuchObject" incompréhensible.
                generated.append(
                    GeneratedUser(
                        source=row, identifiant="", mot_de_passe="", adresse_mail="", ou_cible=ou_dn,
                        erreur=f"« {ou_dn} » ne ressemble pas à un chemin AD valide (attendu : "
                               f"OU=...,DC=...). Si c'est un nom de classe, associez plutôt cette "
                               f"colonne à « classe » (pas « ou ») dans le mapping ci-dessus.",
                    )
                )
                continue
            if from_classe and ou_dn not in created_ous:
                try:
                    exists = self.ad_connection.ou_exists(ou_dn)
                except ADError as exc:
                    generated.append(
                        GeneratedUser(
                            source=row, identifiant="", mot_de_passe="", adresse_mail="", ou_cible=ou_dn,
                            erreur=str(exc),
                        )
                    )
                    continue
                if not exists:
                    generated.append(
                        GeneratedUser(
                            source=row, identifiant="", mot_de_passe="", adresse_mail="", ou_cible=ou_dn,
                            erreur=f"OU introuvable et non créée : {ou_dn}",
                        )
                    )
                    continue

            try:
                identifiant, doublon = engine.generate_unique(row.prenom, row.nom, existing_ids)
            except ValueError as exc:
                generated.append(
                    GeneratedUser(
                        source=row,
                        identifiant="",
                        mot_de_passe="",
                        adresse_mail="",
                        ou_cible=ou_dn,
                        erreur=str(exc),
                    )
                )
                continue

            ad_dup = self._ad_duplicate_check(row)
            if ad_dup:
                generated.append(
                    GeneratedUser(
                        source=row,
                        identifiant=identifiant,
                        mot_de_passe="",
                        adresse_mail="",
                        ou_cible=ou_dn,
                        doublon_ad=True,
                        erreur=ad_dup,
                    )
                )
                continue

            existing_ids.add(identifiant)

            prenom_clean = clean_token(
                apply_prenom_compose_rule(row.prenom, self.config.regle_prenom_compose)
            )
            nom_clean = clean_token(row.nom)
            mail_local = render_template(self.config.format_mail, prenom_clean, nom_clean, year=year)
            mail_domain = self.config.domaine_mail or self.ad_connection.domain or ""
            mail = f"{mail_local}@{mail_domain}"

            groupe = _ou_leaf_name(ou_dn) if self.config.groupes_classe_auto else None

            generated.append(
                GeneratedUser(
                    source=row,
                    identifiant=identifiant,
                    mot_de_passe=password,
                    adresse_mail=mail,
                    ou_cible=ou_dn,
                    groupe=groupe,
                    doublon_resolu=doublon,
                )
            )

        self._generated = generated
        self._populate_preview_table()
        self.validate_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def _populate_preview_table(self) -> None:
        self.preview_table.setRowCount(len(self._generated))
        for row_index, user in enumerate(self._generated):
            self._set_preview_row(row_index, user)

    def _set_preview_row(self, row_index: int, user: GeneratedUser) -> None:
        self.preview_table.setItem(row_index, COL_IDENTIFIANT, QTableWidgetItem(user.identifiant))
        item_nom = QTableWidgetItem(user.nom_complet)
        item_nom.setFlags(item_nom.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.preview_table.setItem(row_index, COL_NOM, item_nom)
        self.preview_table.setItem(row_index, COL_MDP, QTableWidgetItem(user.mot_de_passe))
        item_email = QTableWidgetItem(user.adresse_mail)
        item_email.setFlags(item_email.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.preview_table.setItem(row_index, COL_EMAIL, item_email)
        item_ou = QTableWidgetItem(user.ou_cible)
        item_ou.setFlags(item_ou.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.preview_table.setItem(row_index, COL_OU, item_ou)
        item_groupe = QTableWidgetItem(user.groupe or "")
        item_groupe.setFlags(item_groupe.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.preview_table.setItem(row_index, COL_GROUPE, item_groupe)

        if user.doublon_ad:
            etat = "⚠ Doublon AD (compte existant)"
        elif user.erreur:
            etat = f"Erreur : {user.erreur}"
        elif user.doublon_resolu:
            etat = "⚠ Doublon résolu"
        else:
            etat = "OK"
        item_etat = QTableWidgetItem(etat)
        item_etat.setFlags(item_etat.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.preview_table.setItem(row_index, COL_ETAT, item_etat)

    def _sync_generated_from_table(self) -> None:
        for row_index, user in enumerate(self._generated):
            user.identifiant = self.preview_table.item(row_index, COL_IDENTIFIANT).text().strip()
            user.mot_de_passe = self.preview_table.item(row_index, COL_MDP).text()

    # -- Validation / annulation ----------------------------------------------

    def _on_validate_clicked(self) -> None:
        self._sync_generated_from_table()
        to_process = [(i, u) for i, u in enumerate(self._generated) if not u.erreur]
        if not to_process:
            QMessageBox.warning(self, "Rien à créer", "Aucune ligne valide à créer.")
            return

        simulation = self.ad_connection.dry_run
        labels = [u.identifiant or u.nom_complet for _, u in to_process]
        self.validate_button.setEnabled(False)

        def run_one(entry: tuple[int, GeneratedUser]) -> None:
            _, user = entry
            self._create_one_user(user)

        def on_result(position: int, success: bool, message: str) -> None:
            row_index, user = to_process[position]
            if success:
                self.audit_log.record(
                    "creation_compte", user.identifiant, "succes", self.session_id,
                    ou_destination=user.ou_cible, simulation=simulation,
                )
            else:
                user.erreur = message
                self.audit_log.record(
                    "creation_compte", user.identifiant, "echec", self.session_id,
                    ou_destination=user.ou_cible, simulation=simulation, detail=message,
                )
            self._set_preview_row(row_index, user)

        def on_finished() -> None:
            self.validate_button.setEnabled(True)
            suffix = " (mode simulation, aucune écriture réelle)" if simulation else ""
            QMessageBox.information(
                self,
                "Création terminée",
                f"{self.progress_panel.success_count}/{len(to_process)} compte(s) créé(s) avec succès{suffix}.",
            )
            if self.progress_panel.success_count:
                self._propose_export()
            if self.progress_panel.failure_count:
                self._propose_failed_export()

        self.progress_panel.finished.connect(on_finished, type=Qt.ConnectionType.SingleShotConnection)
        self.progress_panel.start(
            "Création des comptes en cours…", to_process, labels, run_one, on_item_result=on_result,
        )

    def _create_one_user(self, user: GeneratedUser) -> None:
        prenom, nom = user.source.prenom, user.source.nom
        cn = f"{prenom} {nom}"
        if user.doublon_resolu:
            # Le CN est le RDN AD : deux personnes de même prénom+nom dans la
            # même OU produiraient sinon un DN identique et un échec brut
            # "entryAlreadyExists" pour la 2e, alors même que son identifiant
            # (sAMAccountName) a déjà été rendu unique ci-dessus.
            cn = f"{cn} ({user.identifiant})"
        dn = f"CN={escape_rdn(cn)},{user.ou_cible}"
        attributes = {
            "sAMAccountName": user.identifiant,
            "givenName": prenom,
            "sn": nom,
            "displayName": f"{prenom} {nom}",
            "userPrincipalName": f"{user.identifiant}@{self.ad_connection.domain}",
            "mail": user.adresse_mail,
        }
        self.ad_connection.create_user(dn, attributes, password=user.mot_de_passe)

        if user.groupe:
            group_dn = f"CN={escape_rdn(user.groupe)},{user.ou_cible}"
            if not self.ad_connection.group_exists(group_dn):
                self.ad_connection.create_group(group_dn, user.groupe)
            self.ad_connection.add_user_to_group(dn, group_dn)

    def _propose_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les comptes créés", "comptes_crees.csv", "CSV (*.csv)"
        )
        if not path:
            return
        export_created_accounts(Path(path), [u for u in self._generated if not u.erreur])
        QMessageBox.information(self, "Export terminé", f"Export enregistré vers {path}")

    def _propose_failed_export(self) -> None:
        failed = [u for u in self._generated if u.erreur]
        if not failed:
            return
        reply = QMessageBox.question(
            self, "Lignes en échec",
            f"{len(failed)} ligne(s) ont échoué. Exporter ces lignes (au même format que "
            "l'import) pour les corriger et les réimporter, sans recréer les comptes déjà "
            "réussis ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les lignes en échec", "comptes_echecs.csv", "CSV (*.csv)"
        )
        if not path:
            return
        export_failed_rows(Path(path), failed)
        QMessageBox.information(self, "Export terminé", f"Lignes en échec exportées vers {path}")

    def _on_cancel_clicked(self) -> None:
        self._generated = []
        self.preview_table.setRowCount(0)
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    # -- Hook pour sous-classes -----------------------------------------------

    def _ad_duplicate_check(self, row: "RawUserRow") -> str | None:
        """Retourne un message si un doublon AD est détecté, sinon None.
        Surchargé par InscriptionPage (Module 4)."""
        return None
