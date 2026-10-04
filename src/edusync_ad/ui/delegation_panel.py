"""Panneau de délégation d'administration (M26 RBAC).

Liste des opérateurs, de leur rôle, de leur site éventuel (M25) et de leurs
portées (OU/groupes). Chaque action est persistée immédiatement dans
``delegations.json`` — voir ``core/rbac.py``.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.multisite import ensure_sites
from edusync_ad.core.rbac import (
    PERMISSIONS,
    ROLE_ORDER,
    ROLE_PERMISSIONS,
    OperatorGrant,
    Role,
    load_grants,
    role_label,
    save_grants,
    unique_grant_id,
)

COLUMNS = ["Opérateur", "Rôle", "Site", "Portées OU", "Portées groupe", "Actif"]


class DelegationPanel(QWidget):
    """Gestion des délégués — visible uniquement pour un rôle avec « admin »."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grants: list[OperatorGrant] = load_grants()
        self._editing_id: str | None = None
        self._build_ui()
        self._refresh_table()

    # -- Construction --------------------------------------------------------

    def _build_ui(self) -> None:
        intro = QLabel(
            "Chaque opérateur dispose d'un rôle. Les rôles « Admin classe » et "
            "« Helpdesk » n'agissent que dans les OU/groupes délégués ci-dessous ; "
            "le rôle complet (Super-admin / Admin site) n'est pas limité par une "
            "portée. Les changements s'appliquent à la prochaine connexion."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888;")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.operator_edit = QLineEdit()
        self.operator_edit.setPlaceholderText("admin  ·  LYCEE\\admin  ·  admin@lycee.local")

        self.role_combo = QComboBox()
        for role in ROLE_ORDER:
            self.role_combo.addItem(role_label(role), role)
        self.role_combo.setCurrentIndex(ROLE_ORDER.index(Role.LECTURE_SEULE))
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)

        self.site_combo = QComboBox()
        self.site_combo.addItem("(tous les sites)", "")
        for profile in ensure_sites():
            self.site_combo.addItem(profile.display_name, profile.domain)

        self.ous_edit = QLineEdit()
        self.ous_edit.setPlaceholderText(
            "OU=3emeA,OU=eleves,DC=lycee,DC=local ; OU=2eme,OU=eleves,DC=lycee,DC=local"
        )
        self.groupes_edit = QLineEdit()
        self.groupes_edit.setPlaceholderText("CN=Professeurs,OU=groupes,DC=lycee,DC=local")
        self.actif_check = QCheckBox("Délégué actif")
        self.actif_check.setChecked(True)

        self.role_hint = QLabel("")
        self.role_hint.setWordWrap(True)
        self.role_hint.setStyleSheet("color: #888; font-size: 11px;")

        self.scope_warning = QLabel("")
        self.scope_warning.setWordWrap(True)
        self.scope_warning.setStyleSheet("color: #d24343; font-weight: 600;")

        add_button = QPushButton("Ajouter / mettre à jour")
        add_button.clicked.connect(self._on_add_or_update)
        reset_button = QPushButton("Vider le formulaire")
        reset_button.clicked.connect(self._reset_form)
        remove_button = QPushButton("Supprimer le délégué")
        remove_button.clicked.connect(self._on_remove)
        row = QHBoxLayout()
        row.addWidget(add_button)
        row.addWidget(reset_button)
        row.addWidget(remove_button)
        row.addStretch()

        form = QFormLayout()
        form.addRow("Compte opérateur", self.operator_edit)
        form.addRow("Rôle", self.role_combo)
        form.addRow("Site (domaine AD)", self.site_combo)
        form.addRow("Portées OU (séparées par « ; »)", self.ous_edit)
        form.addRow("Portées groupe (séparées par « ; »)", self.groupes_edit)
        form.addRow("", self.actif_check)
        form.addRow("", self.role_hint)
        form.addRow("", self.scope_warning)
        form.addRow("", row)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table)
        layout.addLayout(form)
        self._on_role_changed()

    # -- Remplissage ---------------------------------------------------------

    @staticmethod
    def _split_dns(text: str) -> list[str]:
        return [item.strip() for item in text.replace("\n", ";").split(";") if item.strip()]

    @staticmethod
    def _join_dns(values: list[str]) -> str:
        return " ; ".join(values)

    def _selected_grant(self) -> OperatorGrant | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.grants):
            return None
        return self.grants[row]

    def _refresh_table(self) -> None:
        current_id = self._editing_id
        self.table.setRowCount(len(self.grants))
        for row, grant in enumerate(self.grants):
            values = [
                grant.operateur,
                grant.role_label,
                grant.site or "(tous)",
                self._join_dns(grant.ous),
                self._join_dns(grant.groupes),
                "Oui" if grant.actif else "Non",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(0x0100 + 1, grant.id)  # clé de sélection
                self.table.setItem(row, column, item)
        if current_id:
            for row, grant in enumerate(self.grants):
                if grant.id == current_id:
                    self.table.selectRow(row)
                    return

    def _on_selection_changed(self) -> None:
        grant = self._selected_grant()
        if grant is None:
            return
        self._editing_id = grant.id
        self.operator_edit.setText(grant.operateur)
        self.role_combo.setCurrentIndex(ROLE_ORDER.index(grant.role))
        self.site_combo.setCurrentIndex(max(0, self.site_combo.findData(grant.site)))
        self.ous_edit.setText(self._join_dns(grant.ous))
        self.groupes_edit.setText(self._join_dns(grant.groupes))
        self.actif_check.setChecked(grant.actif)

    def _on_role_changed(self, *_args) -> None:
        role: Role = self.role_combo.currentData()
        permissions = ROLE_PERMISSIONS[role]
        labels = [PERMISSIONS[key] for key in sorted(permissions)]
        self.role_hint.setText("Autorisations : " + " · ".join(labels))
        scoped = role in {Role.ADMIN_CLASSE, Role.HELPDESK}
        self.ous_edit.setEnabled(scoped)
        self.groupes_edit.setEnabled(scoped)
        self.scope_warning.setText(
            "Ce rôle exige au moins une portée (OU ou groupe délégué)."
            if scoped
            else "Ce rôle n'est pas limité par une portée."
        )

    def _reset_form(self) -> None:
        self._editing_id = None
        self.table.clearSelection()
        self.operator_edit.clear()
        self.role_combo.setCurrentIndex(ROLE_ORDER.index(Role.LECTURE_SEULE))
        self.site_combo.setCurrentIndex(0)
        self.ous_edit.clear()
        self.groupes_edit.clear()
        self.actif_check.setChecked(True)

    # -- Actions -------------------------------------------------------------

    def _read_form(self) -> OperatorGrant:
        return OperatorGrant(
            id=self._editing_id or unique_grant_id(self.grants),
            operateur=self.operator_edit.text().strip(),
            role=self.role_combo.currentData(),
            site=str(self.site_combo.currentData() or ""),
            ous=self._split_dns(self.ous_edit.text()),
            groupes=self._split_dns(self.groupes_edit.text()),
            actif=self.actif_check.isChecked(),
        )

    def _persist(self) -> bool:
        try:
            save_grants(self.grants)
        except OSError as exc:
            QMessageBox.critical(
                self, "Écriture impossible", f"Impossible d'enregistrer delegations.json : {exc}"
            )
            return False
        return True

    def _on_add_or_update(self) -> None:
        grant = self._read_form()
        errors = grant.validate()
        if errors:
            QMessageBox.warning(
                self, "Délégué incomplet", "\n".join(f"• {error}" for error in errors)
            )
            return
        replaced = False
        for index, existing in enumerate(self.grants):
            if existing.id == grant.id:
                self.grants[index] = grant
                replaced = True
                break
        if not replaced:
            duplicate = [
                item
                for item in self.grants
                if item.operateur.lower() == grant.operateur.lower()
                and item.site.lower() == grant.site.lower()
            ]
            if duplicate:
                answer = QMessageBox.question(
                    self,
                    "Opérateur déjà délégué",
                    f"« {grant.operateur} » possède déjà un délégué sur ce site. "
                    "Remplacer son rôle par celui du formulaire ?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.grants = [item for item in self.grants if item.id != duplicate[0].id]
            self.grants.append(grant)
        if not self._persist():
            return
        self._editing_id = grant.id
        self._refresh_table()
        self._select_id(grant.id)

    def _on_remove(self) -> None:
        grant = self._selected_grant()
        if grant is None:
            QMessageBox.information(
                self, "Suppression", "Sélectionnez d'abord un délégué dans la liste."
            )
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le délégué",
            f"Retirer le rôle « {grant.role_label} » à « {grant.operateur} » ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        previous = self.grants
        self.grants = [item for item in self.grants if item.id != grant.id]
        if not self._persist():
            self.grants = previous
            return
        self._editing_id = None
        self._refresh_table()

    def _select_id(self, grant_id: str) -> None:
        for row, grant in enumerate(self.grants):
            if grant.id == grant_id:
                self.table.selectRow(row)
                return
