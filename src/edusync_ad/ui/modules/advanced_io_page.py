"""Module 22 — Import/Export avancés.

Quatre outils dans un même écran :

1. **Import LDAP** — chargement depuis une OU, un groupe AD ou un filtre
   LDAP personnalisé (attributs et limite paramétrables), aperçu + export CSV.
2. **Exports** — LDIF (RFC 2849, attributs choisis, ``changetype`` optionnel)
   et vCard 3.0 (photo AD optionnelle) pour annuaires LDAP tiers / carnets.
3. **Publipostage HTML** — gabarit ``{{champ}}`` → un fichier par objet de
   l'import LDAP + ``index.html`` (chartes, conventions…).
4. **Import GEP / base Éducation nationale** — lancement de l'outil tiers
   fourni, import de son CSV (colonnes associées automatiquement) puis
   conversion en CSV au format d'import EduSync (création de comptes).
"""

from __future__ import annotations

from pathlib import Path

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.ad.connection import ADConnection, is_builtin_group_dn
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.advanced_io import (
    DEFAULT_EXPORT_ATTRIBUTES,
    DEFAULT_HTML_TEMPLATE,
    LDAP_SOURCE_MODES,
    AdvancedIOConfig,
    LDAPSource,
    export_entries_csv,
    export_ldif,
    export_vcards,
    find_placeholders,
    gep_to_edusync_csv,
    import_from_ad,
    load_adv_io_config,
    merge_html,
    parse_gep_csv,
    run_external_tool,
    save_adv_io_config,
    validate_ldap_filter,
)
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.csv_io import EXPECTED_COLUMNS

MAX_PREVIEW_ROWS = 1000
MAX_PREVIEW_COLUMNS = 10


def _read_text(path: Path) -> str:
    last: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last = exc
    raise ValueError(f"Impossible de décoder le fichier : {last}")


class AdvancedIOPage(QWidget):
    """Page M22 — Import/Export avancés."""

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

        self._entries: list[dict] = []
        self._source_label = "aucune"
        self._gep_rows: list[dict] = []
        self._gep_mapping: dict[str, str] = {}
        self._adv_cfg: AdvancedIOConfig = load_adv_io_config()

        self._build_ui()
        self._refresh_entries_state()

    def update_config(self, config: AppConfig) -> None:
        self.config = config

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ad_connection.domain is not None and self.ou_combo.count() == 0:
            self._load_ous()

    # -- UI --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_import_tab(), "Import LDAP")
        tabs.addTab(self._build_export_tab(), "Exports LDIF / vCard")
        tabs.addTab(self._build_merge_tab(), "Publipostage HTML")
        tabs.addTab(self._build_gep_tab(), "Import GEP / Éducation nationale")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    # -- Onglet 1 : import LDAP --------------------------------------------------------

    def _build_import_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        form = QFormLayout()
        self.mode_combo = QComboBox()
        for mode, label in LDAP_SOURCE_MODES.items():
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Source :", self.mode_combo)

        self.ou_row = QWidget()
        ou_layout = QHBoxLayout(self.ou_row)
        ou_layout.setContentsMargins(0, 0, 0, 0)
        self.ou_combo = QComboBox()
        self.ou_combo.setMinimumWidth(300)
        ou_layout.addWidget(self.ou_combo)
        refresh_ou = QPushButton("Actualiser")
        refresh_ou.clicked.connect(self._load_ous)
        ou_layout.addWidget(refresh_ou)
        ou_layout.addStretch()
        form.addRow("OU :", self.ou_row)

        self.group_row = QWidget()
        group_layout = QHBoxLayout(self.group_row)
        group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(300)
        group_layout.addWidget(self.group_combo)
        refresh_groups = QPushButton("Actualiser")
        refresh_groups.clicked.connect(self._load_groups)
        group_layout.addWidget(refresh_groups)
        group_layout.addStretch()
        form.addRow("Groupe :", self.group_row)

        self.filter_row = QWidget()
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_edit = QLineEdit("(&(objectClass=user)(objectCategory=person))")
        filter_layout.addWidget(self.filter_edit)
        self.filter_check = QPushButton("Tester le filtre")
        self.filter_check.clicked.connect(self._on_test_filter)
        filter_layout.addWidget(self.filter_check)
        form.addRow("Filtre LDAP :", self.filter_row)

        self.attributes_edit = QLineEdit(", ".join(DEFAULT_EXPORT_ATTRIBUTES))
        form.addRow("Attributs :", self.attributes_edit)

        limit_row = QHBoxLayout()
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 50_000)
        self.limit_spin.setValue(5_000)
        limit_row.addWidget(self.limit_spin)
        limit_row.addStretch()
        form.addRow("Limite :", limit_row)
        v.addLayout(form)

        action_row = QHBoxLayout()
        load_btn = QPushButton("Charger les objets")
        load_btn.clicked.connect(self._on_load)
        action_row.addWidget(load_btn)
        self.csv_btn = QPushButton("Exporter le résultat en CSV…")
        self.csv_btn.clicked.connect(self._on_export_csv)
        action_row.addWidget(self.csv_btn)
        action_row.addStretch()
        v.addLayout(action_row)

        self.entries_info = QLabel("Aucun objet chargé.")
        self.entries_info.setStyleSheet("color: #888;")
        v.addWidget(self.entries_info)

        self.entries_table = QTableWidget(0, 0)
        self.entries_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entries_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.entries_table)

        self._on_mode_changed()
        return widget

    def _on_mode_changed(self) -> None:
        mode = str(self.mode_combo.currentData() or "ou")
        self.ou_row.setVisible(mode == "ou")
        self.group_row.setVisible(mode == "groupe")
        self.filter_row.setVisible(mode == "filtre")

    def _current_source(self) -> LDAPSource:
        attributes = [
            attr.strip()
            for attr in self.attributes_edit.text().replace(",", ";").split(";")
            if attr.strip()
        ]
        return LDAPSource(
            mode=str(self.mode_combo.currentData() or "ou"),
            base_dn=ADConnection.domain_to_base_dn(self.ad_connection.domain)
            if self.ad_connection.domain
            else "",
            ou_dn=self.ou_combo.currentData() or "",
            group_dn=self.group_combo.currentData() or "",
            ldap_filter=self.filter_edit.text().strip(),
            attributes=attributes or list(DEFAULT_EXPORT_ATTRIBUTES),
            size_limit=self.limit_spin.value(),
        )

    def _on_test_filter(self) -> None:
        errors = validate_ldap_filter(self.filter_edit.text())
        if errors:
            QMessageBox.warning(self, "Filtre invalide", "\n".join(errors))
        else:
            QMessageBox.information(
                self, "Filtre valide",
                "Syntaxe correcte (contrôle local — le serveur reste la "
                "référence pour les attributs inconnus).",
            )

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
        for dn, name in sorted(ous, key=lambda item: item[0]):
            self.ou_combo.addItem(f"{name}  ({dn})", dn)
        index = self.ou_combo.findData(previous)
        self.ou_combo.setCurrentIndex(index if index >= 0 else 0)

    def _load_groups(self) -> None:
        if self.ad_connection.domain is None:
            return
        base_dn = ADConnection.domain_to_base_dn(self.ad_connection.domain)
        try:
            groups = [
                (dn, name)
                for dn, name in self.ad_connection.list_groups(base_dn)
                if not is_builtin_group_dn(dn)
            ]
        except ADError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        previous = self.group_combo.currentData()
        self.group_combo.clear()
        for dn, name in sorted(groups, key=lambda item: item[1].lower()):
            self.group_combo.addItem(f"{name}  ({dn})", dn)
        index = self.group_combo.findData(previous)
        self.group_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_load(self) -> None:
        source = self._current_source()
        errors = source.validate()
        if errors:
            QMessageBox.warning(self, "Source invalide", "\n".join(errors))
            return
        try:
            entries = import_from_ad(self.ad_connection, source)
        except ValueError as exc:
            QMessageBox.warning(self, "Source invalide", str(exc))
            return
        except ADError as exc:
            QMessageBox.critical(self, "Erreur AD", str(exc))
            return
        self._entries = entries
        self._source_label = {
            "ou": self.ou_combo.currentText(),
            "groupe": self.group_combo.currentText(),
            "filtre": source.ldap_filter,
        }.get(source.mode, source.mode)
        self._populate_entries_table(source.attributes)
        self._refresh_entries_state()
        self.audit_log.record(
            "import_ldap", "-", "succes", self.session_id,
            detail=f"{len(entries)} objet(s) — {self._source_label}",
        )

    def _populate_entries_table(self, attributes: list[str]) -> None:
        shown = attributes[:MAX_PREVIEW_COLUMNS]
        columns = ["dn"] + shown
        display = self._entries[:MAX_PREVIEW_ROWS]
        self.entries_table.setColumnCount(len(columns))
        self.entries_table.setHorizontalHeaderLabels(columns)
        self.entries_table.setRowCount(len(display))
        for row, entry in enumerate(display):
            for column, key in enumerate(columns):
                value = entry.get(key, "")
                if isinstance(value, bytes):
                    text = "…(binaire)"
                elif isinstance(value, list):
                    text = " | ".join(str(item) for item in value)
                else:
                    text = str(value)
                self.entries_table.setItem(row, column, QTableWidgetItem(text))

    def _refresh_entries_state(self) -> None:
        count = len(self._entries)
        if count:
            suffix = ""
            if count > MAX_PREVIEW_ROWS:
                suffix = f" — aperçu limité à {MAX_PREVIEW_ROWS} lignes"
            self.entries_info.setText(
                f"{count} objet(s) chargé(s) depuis : {self._source_label}{suffix}"
            )
        else:
            self.entries_info.setText("Aucun objet chargé.")
        for button in (self.csv_btn, self.export_ldif_btn, self.export_vcard_btn,
                       self.merge_btn):
            button.setEnabled(count > 0)

    def _require_entries(self) -> bool:
        if not self._entries:
            QMessageBox.warning(
                self, "Aucun objet",
                "Chargez d'abord des objets (onglet « Import LDAP »).",
            )
            return False
        return True

    def _on_export_csv(self) -> None:
        if not self._require_entries():
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter les objets", "objets_ldap.csv",
            "CSV (*.csv);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            path = export_entries_csv(self._entries, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record("export_objets_csv", "-", "succes", self.session_id,
                              detail=str(path))
        QMessageBox.information(self, "Exporté", f"« {path} »")

    # -- Onglet 2 : exports LDIF / vCard ------------------------------------------------

    def _build_export_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        info = QLabel(
            "Les exports utilisent les objets chargés dans l'onglet "
            "« Import LDAP » (attributs inclus = ceux chargés)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888;")
        v.addWidget(info)

        ldif_group = QGroupBox("Export LDIF (annuaires LDAP tiers)")
        ldif_layout = QFormLayout(ldif_group)
        self.changetype_combo = QComboBox()
        self.changetype_combo.addItem("Aucun (enregistrements simples)", "")
        self.changetype_combo.addItem("changetype: add (ajout d'entrées)", "add")
        ldif_layout.addRow("Type de modification :", self.changetype_combo)
        self.export_ldif_btn = QPushButton("Exporter en LDIF…")
        self.export_ldif_btn.clicked.connect(self._on_export_ldif)
        ldif_layout.addRow("", self.export_ldif_btn)
        v.addWidget(ldif_group)

        vcard_group = QGroupBox("Export vCard (carnets de contacts)")
        vcard_layout = QFormLayout(vcard_group)
        self.photo_check = QCheckBox("Inclure la photo AD (jpegPhoto / thumbnailPhoto)")
        vcard_layout.addRow(self.photo_check)
        self.export_vcard_btn = QPushButton("Exporter en vCard (.vcf)…")
        self.export_vcard_btn.clicked.connect(self._on_export_vcard)
        vcard_layout.addRow("", self.export_vcard_btn)
        v.addWidget(vcard_group)

        v.addStretch()
        return widget

    def _on_export_ldif(self) -> None:
        if not self._require_entries():
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter en LDIF", "objets.ldif",
            "LDIF (*.ldif);;Tous les fichiers (*)",
        )
        if not dest:
            return
        changetype = str(self.changetype_combo.currentData() or "") or None
        try:
            path = export_ldif(self._entries, Path(dest), changetype=changetype)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record("export_ldif", "-", "succes", self.session_id,
                              detail=f"{path} ({len(self._entries)} entrée(s))")
        QMessageBox.information(self, "Exporté", f"« {path} »")

    def _on_export_vcard(self) -> None:
        if not self._require_entries():
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter en vCard", "contacts.vcf",
            "vCard (*.vcf);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            path = export_vcards(
                self._entries, Path(dest), include_photo=self.photo_check.isChecked()
            )
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record("export_vcard", "-", "succes", self.session_id,
                              detail=f"{path} ({len(self._entries)} carte(s))")
        QMessageBox.information(self, "Exporté", f"« {path} »")

    # -- Onglet 3 : publipostage -----------------------------------------------------------

    def _build_merge_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        source_info = QLabel(
            "Documents générés pour chaque objet de l'onglet « Import LDAP ». "
            "Champs disponibles : {{champ}} au choix parmi les attributs chargés."
        )
        source_info.setWordWrap(True)
        source_info.setStyleSheet("color: #888;")
        v.addWidget(source_info)

        tools_row = QHBoxLayout()
        default_btn = QPushButton("Modèle par défaut")
        default_btn.clicked.connect(self._on_default_template)
        tools_row.addWidget(default_btn)
        load_btn = QPushButton("Charger un modèle…")
        load_btn.clicked.connect(self._on_load_template)
        tools_row.addWidget(load_btn)
        save_btn = QPushButton("Enregistrer le modèle…")
        save_btn.clicked.connect(self._on_save_template)
        tools_row.addWidget(save_btn)
        analyze_btn = QPushButton("Analyser les champs")
        analyze_btn.clicked.connect(self._on_analyze_template)
        tools_row.addWidget(analyze_btn)
        tools_row.addStretch()
        v.addLayout(tools_row)

        self.template_edit = QTextEdit()
        self.template_edit.setPlaceholderText("Collez ici votre gabarit HTML…")
        self.template_edit.setMinimumHeight(200)
        v.addWidget(self.template_edit)

        self.placeholders_label = QLabel("Champs : (non analysés)")
        self.placeholders_label.setStyleSheet("color: #888;")
        v.addWidget(self.placeholders_label)

        pattern_form = QFormLayout()
        self.pattern_edit = QLineEdit("{sAMAccountName}_document.html")
        pattern_form.addRow("Nom de fichier :", self.pattern_edit)
        v.addLayout(pattern_form)

        self.merge_btn = QPushButton("Générer les documents dans un dossier…")
        self.merge_btn.clicked.connect(self._on_generate_merge)
        v.addWidget(self.merge_btn)

        return widget

    def _on_default_template(self) -> None:
        self.template_edit.setPlainText(DEFAULT_HTML_TEMPLATE)
        self._on_analyze_template()

    def _on_analyze_template(self) -> None:
        fields = find_placeholders(self.template_edit.toPlainText())
        if fields:
            self.placeholders_label.setText(f"Champs détectés : {', '.join(fields)}")
        else:
            self.placeholders_label.setText(
                "Champs : aucun (syntaxe attendue : {{champ}})"
            )

    def _on_load_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger un gabarit HTML", "",
            "HTML (*.html *.htm);;Tous les fichiers (*)",
        )
        if not path:
            return
        try:
            self.template_edit.setPlainText(_read_text(Path(path)))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._on_analyze_template()
        self.audit_log.record("chargement_gabarit", "-", "succes", self.session_id,
                              detail=path)

    def _on_save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le gabarit", "gabarit.html",
            "HTML (*.html);;Tous les fichiers (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.template_edit.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record("enregistrement_gabarit", "-", "succes", self.session_id,
                              detail=path)

    def _on_generate_merge(self) -> None:
        if not self._require_entries():
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Dossier de sortie")
        if not out_dir:
            return
        try:
            paths = merge_html(
                self.template_edit.toPlainText(),
                self._entries,
                Path(out_dir),
                filename_pattern=self.pattern_edit.text().strip()
                or "{sAMAccountName}_document.html",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Publipostage impossible", str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record(
            "publipostage_html", "-", "succes", self.session_id,
            detail=f"{len(paths)} document(s) dans {out_dir}",
        )
        QMessageBox.information(
            self, "Terminé",
            f"{len(paths)} document(s) générés (+ index.html) dans : {out_dir}",
        )

    # -- Onglet 4 : GEP ---------------------------------------------------------------------

    def _build_gep_tab(self) -> QWidget:
        widget = QWidget()
        v = QVBoxLayout(widget)

        intro = QLabel(
            "L'export de la base Éducation nationale (GEP) est produit par un "
            "outil tiers fourni à l'établissement. Configurez son chemin, "
            "lancez-le, puis importez le CSV qu'il génère : les colonnes sont "
            "associées automatiquement et converties au format d'import EduSync "
            "(prénom, nom, classe, ou, email, date de naissance, numéro)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888;")
        v.addWidget(intro)

        tool_form = QFormLayout()
        self.tool_edit = QLineEdit(self._adv_cfg.gep_tool_path)
        self.tool_edit.setPlaceholderText(r"ex : C:\GEP\exporteur.exe")
        browse_tool = QPushButton("Parcourir…")
        browse_tool.clicked.connect(self._on_browse_tool)
        tool_row = QHBoxLayout()
        tool_row.addWidget(self.tool_edit)
        tool_row.addWidget(browse_tool)
        tool_form.addRow("Outil tiers :", tool_row)
        v.addLayout(tool_form)

        tool_btns = QHBoxLayout()
        run_tool_btn = QPushButton("Lancer l'outil tiers")
        run_tool_btn.clicked.connect(self._on_run_tool)
        tool_btns.addWidget(run_tool_btn)
        import_btn = QPushButton("Importer le fichier produit…")
        import_btn.clicked.connect(self._on_import_gep)
        tool_btns.addWidget(import_btn)
        convert_btn = QPushButton("Convertir vers CSV EduSync…")
        convert_btn.clicked.connect(self._on_convert_gep)
        tool_btns.addWidget(convert_btn)
        tool_btns.addStretch()
        v.addLayout(tool_btns)

        self.gep_info = QLabel("Aucun fichier GEP importé.")
        self.gep_info.setStyleSheet("color: #888;")
        v.addWidget(self.gep_info)

        self.gep_table = QTableWidget(0, len(EXPECTED_COLUMNS))
        self.gep_table.setHorizontalHeaderLabels(EXPECTED_COLUMNS)
        self.gep_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.gep_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.gep_table.setMaximumHeight(200)
        v.addWidget(self.gep_table)

        return widget

    def _on_browse_tool(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir l'outil tiers GEP", "", "Programmes (*.exe *.bat *.cmd);;Tous (*)"
        )
        if not path:
            return
        self.tool_edit.setText(path)
        self._adv_cfg = AdvancedIOConfig(gep_tool_path=path)
        try:
            save_adv_io_config(self._adv_cfg)
        except OSError:
            pass

    def _on_run_tool(self) -> None:
        self._adv_cfg = AdvancedIOConfig(gep_tool_path=self.tool_edit.text().strip())
        try:
            save_adv_io_config(self._adv_cfg)
        except OSError:
            pass
        error = run_external_tool(self._adv_cfg)
        if error:
            QMessageBox.warning(self, "Outil tiers", error)
            return
        self.audit_log.record("lancement_outil_gep", "-", "succes", self.session_id,
                              detail=self._adv_cfg.gep_tool_path)
        QMessageBox.information(
            self, "Outil lancé",
            "L'outil est parti — une fois son export terminé, utilisez "
            "« Importer le fichier produit ».",
        )

    def _on_import_gep(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer l'export GEP", "",
            "CSV (*.csv *.txt);;Tous les fichiers (*)",
        )
        if not path:
            return
        try:
            text = _read_text(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        result = parse_gep_csv(text)
        self._gep_rows = result.rows
        self._gep_mapping = result.mapping
        found = sum(1 for value in result.mapping.values() if value)
        association = " · ".join(
            f"{field} ← {result.mapping.get(field) or '(absente)'}"
            for field in ("prenom", "nom", "classe")
        )
        self.gep_info.setText(
            f"{len(result.rows)} ligne(s) valide(s) — {len(result.skipped_row_numbers)} "
            f"ignorée(s) (prénom/nom manquant) — délimiteur « {result.delimiter} » "
            f"— {found}/{len(EXPECTED_COLUMNS)} colonnes associées — {association}"
        )
        preview = result.rows[:MAX_PREVIEW_ROWS]
        self.gep_table.setRowCount(len(preview))
        for row_index, row in enumerate(preview):
            for column, field in enumerate(EXPECTED_COLUMNS):
                self.gep_table.setItem(
                    row_index, column, QTableWidgetItem(row.get(field, ""))
                )
        self.audit_log.record(
            "import_gep", "-", "succes", self.session_id,
            detail=f"{len(result.rows)} ligne(s) depuis {path}",
        )
        if not result.rows:
            QMessageBox.warning(
                self, "Aucune ligne",
                "Aucune ligne exploitable — vérifiez que le fichier provient "
                "bien de l'outil tiers et contient prénom + nom.",
            )

    def _on_convert_gep(self) -> None:
        if not self._gep_rows:
            QMessageBox.warning(
                self, "Aucune donnée",
                "Importez d'abord un fichier GEP (bouton ci-dessus).",
            )
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Convertir au format EduSync", "gep_import.csv",
            "CSV (*.csv);;Tous les fichiers (*)",
        )
        if not dest:
            return
        try:
            path = gep_to_edusync_csv(self._gep_rows, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", f"Écriture impossible : {exc}")
            return
        self.audit_log.record("export_gep_edusync", "-", "succes", self.session_id,
                              detail=f"{path} ({len(self._gep_rows)} ligne(s))")
        QMessageBox.information(
            self, "Converti",
            f"« {path} » ({len(self._gep_rows)} ligne(s)) — utilisable tel quel "
            "dans l'import CSV de la création de comptes.",
        )
