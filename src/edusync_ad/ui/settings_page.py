"""Paramètres globaux (§12 du cahier des charges) utilisés par le Module 1."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.config import AppConfig
from edusync_ad.core.identifiers import CAMEL_PRESETS, PRESETS
from edusync_ad.core.models import DoublonRule, PasswordPolicy, PrenomComposeRule

IDENTIFIER_PRESET_KEYS = list(PRESETS.keys()) + list(CAMEL_PRESETS)

DOUBLON_LABELS = {
    DoublonRule.SUFFIXE_NUMERIQUE: "Suffixe numérique direct (thomas.martin2)",
    DoublonRule.SUFFIXE_NUMERIQUE_SEPARATEUR: "Suffixe numérique avec séparateur (thomas.martin-2)",
    DoublonRule.PREFIXE_NUMERIQUE: "Préfixe numérique (2.thomas.martin)",
    DoublonRule.LETTRES_PRENOM: "Lettres supplémentaires du prénom",
    DoublonRule.LETTRES_NOM: "Lettres supplémentaires du nom",
    DoublonRule.ANNEE_SUFFIXE: "Année en suffixe",
}

PRENOM_COMPOSE_LABELS = {
    PrenomComposeRule.PREMIER_PRENOM: "Premier prénom uniquement",
    PrenomComposeRule.CONCATENATION: "Concaténation complète",
    PrenomComposeRule.TRONCATURE: "Troncature au tiret ou à l'espace",
}


class _PasswordPolicyForm(QWidget):
    def __init__(self, policy: PasswordPolicy, parent=None) -> None:
        super().__init__(parent)
        self.length_spin = QSpinBox()
        self.length_spin.setRange(6, 32)
        self.length_spin.setValue(policy.longueur)

        self.uppercase_check = QCheckBox("Majuscules")
        self.uppercase_check.setChecked(policy.majuscules)
        self.digits_check = QCheckBox("Chiffres")
        self.digits_check.setChecked(policy.chiffres)
        self.special_check = QCheckBox("Caractères spéciaux")
        self.special_check.setChecked(policy.caracteres_speciaux)
        self.identical_check = QCheckBox("Même mot de passe pour tout l'import")
        self.identical_check.setChecked(policy.mot_de_passe_identique)

        self.pattern_edit = QLineEdit(policy.pattern_fixe or "")
        self.pattern_edit.setPlaceholderText("Pattern fixe, ex. Ecole{AN}! (vide = génération aléatoire)")

        form = QFormLayout(self)
        form.addRow("Longueur", self.length_spin)
        form.addRow(self.uppercase_check)
        form.addRow(self.digits_check)
        form.addRow(self.special_check)
        form.addRow(self.identical_check)
        form.addRow("Pattern fixe", self.pattern_edit)

    def to_policy(self) -> PasswordPolicy:
        return PasswordPolicy(
            longueur=self.length_spin.value(),
            majuscules=self.uppercase_check.isChecked(),
            chiffres=self.digits_check.isChecked(),
            caracteres_speciaux=self.special_check.isChecked(),
            mot_de_passe_identique=self.identical_check.isChecked(),
            pattern_fixe=self.pattern_edit.text().strip() or None,
        )


class SettingsPage(QWidget):
    def __init__(self, config: AppConfig, on_save: Callable[[AppConfig], None], parent=None) -> None:
        super().__init__(parent)
        self._on_save = on_save

        self.identifier_eleve_combo = self._build_identifier_combo(config.identifiant_format_eleve)
        self.identifier_personnel_combo = self._build_identifier_combo(
            config.identifiant_format_personnel
        )

        self.doublon_combo = QComboBox()
        for rule, label in DOUBLON_LABELS.items():
            self.doublon_combo.addItem(label, rule)
        self.doublon_combo.setCurrentIndex(list(DOUBLON_LABELS).index(config.regle_doublons))

        self.prenom_compose_combo = QComboBox()
        for rule, label in PRENOM_COMPOSE_LABELS.items():
            self.prenom_compose_combo.addItem(label, rule)
        self.prenom_compose_combo.setCurrentIndex(
            list(PRENOM_COMPOSE_LABELS).index(config.regle_prenom_compose)
        )

        self.mail_domain_edit = QLineEdit(config.domaine_mail)
        self.mail_format_edit = QLineEdit(config.format_mail)

        self.groupes_auto_check = QCheckBox("Création automatique des groupes de classe")
        self.groupes_auto_check.setChecked(config.groupes_classe_auto)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Clair", "clair")
        self.theme_combo.addItem("Sombre", "sombre")
        self.theme_combo.setCurrentIndex(0 if config.theme == "clair" else 1)

        self.eleve_policy_form = _PasswordPolicyForm(config.politique_mdp_eleve)
        self.personnel_policy_form = _PasswordPolicyForm(config.politique_mdp_personnel)

        save_button = QPushButton("Enregistrer les paramètres")
        save_button.clicked.connect(self._save)

        identifiers_group = QGroupBox("Nomenclature des identifiants")
        identifiers_form = QFormLayout(identifiers_group)
        identifiers_form.addRow("Format par défaut — Élèves", self.identifier_eleve_combo)
        identifiers_form.addRow("Format par défaut — Personnels", self.identifier_personnel_combo)
        identifiers_form.addRow("Règle de résolution des doublons", self.doublon_combo)
        identifiers_form.addRow("Prénoms composés", self.prenom_compose_combo)

        mail_group = QGroupBox("Adresses mail")
        mail_form = QFormLayout(mail_group)
        mail_form.addRow("Domaine mail", self.mail_domain_edit)
        mail_form.addRow("Nomenclature mail", self.mail_format_edit)

        groupes_group = QGroupBox("Groupes de classe")
        groupes_layout = QVBoxLayout(groupes_group)
        groupes_layout.addWidget(self.groupes_auto_check)

        appearance_group = QGroupBox("Apparence")
        appearance_form = QFormLayout(appearance_group)
        appearance_form.addRow("Thème", self.theme_combo)

        eleve_group = QGroupBox("Politique de mot de passe — Élèves")
        eleve_layout = QVBoxLayout(eleve_group)
        eleve_layout.addWidget(self.eleve_policy_form)

        personnel_group = QGroupBox("Politique de mot de passe — Personnels")
        personnel_layout = QVBoxLayout(personnel_group)
        personnel_layout.addWidget(self.personnel_policy_form)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        for group in (
            identifiers_group,
            mail_group,
            groupes_group,
            appearance_group,
            eleve_group,
            personnel_group,
        ):
            content_layout.addWidget(group)
        content_layout.addWidget(save_button)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    @staticmethod
    def _build_identifier_combo(current_value: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(IDENTIFIER_PRESET_KEYS)
        combo.setCurrentText(current_value)
        return combo

    def _save(self) -> None:
        config = AppConfig(
            identifiant_format_eleve=self.identifier_eleve_combo.currentText().strip(),
            identifiant_format_personnel=self.identifier_personnel_combo.currentText().strip(),
            regle_doublons=self.doublon_combo.currentData(),
            politique_mdp_eleve=self.eleve_policy_form.to_policy(),
            politique_mdp_personnel=self.personnel_policy_form.to_policy(),
            domaine_mail=self.mail_domain_edit.text().strip(),
            format_mail=self.mail_format_edit.text().strip(),
            regle_prenom_compose=self.prenom_compose_combo.currentData(),
            groupes_classe_auto=self.groupes_auto_check.isChecked(),
            theme=self.theme_combo.currentData(),
        )
        self._on_save(config)
