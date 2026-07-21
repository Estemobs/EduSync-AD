"""Paramètres globaux (§12 du cahier des charges) utilisés par le Module 1."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from edusync_ad.core.config import AppConfig
from edusync_ad.core.identifiers import CAMEL_PRESETS, PRESETS, render_template
from edusync_ad.core.models import DoublonRule, PasswordPolicy, PrenomComposeRule
from edusync_ad.core.password_vault import PasswordVault

MAIL_FORMAT_PRESET_KEYS = [
    "{P}.{N}",
    "{N}.{P}",
    "{p1}.{N}",
    "{P}{N}",
    "{P}_{N}",
    "{p1}{N}",
]

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
    def __init__(
        self,
        config: AppConfig,
        on_save: Callable[[AppConfig], None],
        password_vault: PasswordVault,
        ad_domain: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_save = on_save
        self._ad_domain = ad_domain
        self.password_vault = password_vault
        # Conservé pour préserver au moment d'enregistrer les champs que ce
        # formulaire ne gère pas (ex. réglages LDAPS de l'écran de connexion) —
        # voir _save().
        self.config = config

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
        self.mail_domain_edit.setPlaceholderText(
            ad_domain or "sera rempli automatiquement avec le domaine AD connecté"
        )
        self.mail_domain_use_ad_button = QPushButton("Utiliser le domaine AD")
        self.mail_domain_use_ad_button.setEnabled(bool(ad_domain))
        self.mail_domain_use_ad_button.clicked.connect(self._use_ad_domain)
        if not config.domaine_mail and ad_domain:
            self.mail_domain_edit.setText(ad_domain)

        self.mail_format_combo = QComboBox()
        self.mail_format_combo.setEditable(True)
        self.mail_format_combo.addItems(MAIL_FORMAT_PRESET_KEYS)
        self.mail_format_combo.setCurrentText(config.format_mail)
        self.mail_format_preview_label = QLabel("")
        self.mail_format_combo.editTextChanged.connect(self._update_mail_format_preview)
        self.mail_domain_edit.textChanged.connect(self._update_mail_format_preview)
        self._update_mail_format_preview()

        self.groupes_auto_check = QCheckBox("Création automatique des groupes de classe")
        self.groupes_auto_check.setChecked(config.groupes_classe_auto)

        self.ou_parente_classes_edit = QLineEdit(config.ou_parente_classes)
        self.ou_parente_classes_edit.setPlaceholderText(
            "Vide = racine du domaine. Ex. : OU=eleves,DC=lycee,DC=local"
        )

        self.ou_archive_edit = QLineEdit(config.ou_archive)
        self.ou_archive_edit.setPlaceholderText("Ex. : OU=Archive,DC=lycee,DC=local")

        self.delai_spin = QSpinBox()
        self.delai_spin.setRange(1, 3650)
        self.delai_spin.setSuffix(" jours")
        self.delai_spin.setValue(config.delai_suppression_jours)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Clair", "clair")
        self.theme_combo.addItem("Sombre", "sombre")
        self.theme_combo.setCurrentIndex(0 if config.theme == "clair" else 1)

        self.langue_combo = QComboBox()
        self.langue_combo.addItem("Français", "fr")
        self.langue_combo.addItem("English", "en")
        self.langue_combo.setCurrentIndex(0 if config.langue == "fr" else 1)

        self.eleve_policy_form = _PasswordPolicyForm(config.politique_mdp_eleve)
        self.personnel_policy_form = _PasswordPolicyForm(config.politique_mdp_personnel)

        self.save_button = QPushButton("💾 Enregistrer les paramètres")
        self.save_button.setStyleSheet(
            "QPushButton { font-weight: 600; padding: 8px 18px; font-size: 14px; }"
        )
        self.save_button.clicked.connect(self._save)
        save_button = self.save_button  # utilisé plus bas pour l'ajout au layout

        self.save_confirmation_label = QLabel("✓ Paramètres enregistrés avec succès")
        self.save_confirmation_label.setStyleSheet(
            "color: #1f9d55; font-weight: 600; padding: 4px 0; font-size: 13px;"
        )
        self.save_confirmation_label.setVisible(False)

        identifiers_group = QGroupBox("Nomenclature des identifiants")
        identifiers_form = QFormLayout(identifiers_group)
        identifiers_form.addRow("Format par défaut — Élèves", self.identifier_eleve_combo)
        identifiers_form.addRow("Format par défaut — Personnels", self.identifier_personnel_combo)
        identifiers_form.addRow("Règle de résolution des doublons", self.doublon_combo)
        identifiers_form.addRow("Prénoms composés", self.prenom_compose_combo)

        mail_group = QGroupBox("Adresses mail")
        mail_form = QFormLayout(mail_group)
        domain_row = QHBoxLayout()
        domain_row.addWidget(self.mail_domain_edit)
        domain_row.addWidget(self.mail_domain_use_ad_button)
        mail_form.addRow("Domaine mail", domain_row)
        format_row = QHBoxLayout()
        format_row.addWidget(self.mail_format_combo)
        format_row.addWidget(QLabel("Aperçu :"))
        format_row.addWidget(self.mail_format_preview_label)
        format_row.addStretch()
        mail_form.addRow("Nomenclature mail", format_row)

        groupes_group = QGroupBox("Groupes de classe")
        groupes_layout = QVBoxLayout(groupes_group)
        groupes_layout.addWidget(self.groupes_auto_check)
        groupes_form = QFormLayout()
        groupes_form.addRow("OU parente pour les classes", self.ou_parente_classes_edit)
        groupes_layout.addLayout(groupes_form)

        departs_group = QGroupBox("Gestion des départs")
        departs_form = QFormLayout(departs_group)
        departs_form.addRow("OU d'archivage", self.ou_archive_edit)
        departs_form.addRow("Délai avant suppression", self.delai_spin)

        appearance_group = QGroupBox("Apparence")
        appearance_form = QFormLayout(appearance_group)
        appearance_form.addRow("Thème", self.theme_combo)
        appearance_form.addRow("Langue", self.langue_combo)

        eleve_group = QGroupBox("Politique de mot de passe — Élèves")
        eleve_layout = QVBoxLayout(eleve_group)
        eleve_layout.addWidget(self.eleve_policy_form)

        personnel_group = QGroupBox("Politique de mot de passe — Personnels")
        personnel_layout = QVBoxLayout(personnel_group)
        personnel_layout.addWidget(self.personnel_policy_form)

        security_group = QGroupBox("Sécurité — coffre des mots de passe")
        security_layout = QVBoxLayout(security_group)
        security_layout.addWidget(QLabel(
            "EduSync AD retient, chiffrés, les mots de passe qu'il positionne lui-même "
            "(création de compte, réinitialisation) pour pouvoir les réafficher ensuite "
            "(fiche utilisateur, export). Un mot de passe changé par un autre outil "
            "n'y figure jamais."
        ))
        self.pwd_vault_count_label = QLabel("")
        security_layout.addWidget(self.pwd_vault_count_label)
        self.pwd_vault_clear_button = QPushButton("Vider le coffre des mots de passe…")
        self.pwd_vault_clear_button.clicked.connect(self._on_clear_password_vault)
        security_layout.addWidget(self.pwd_vault_clear_button)
        self._refresh_pwd_vault_count()

        content = QWidget()
        content_layout = QVBoxLayout(content)
        for group in (
            identifiers_group,
            mail_group,
            groupes_group,
            departs_group,
            appearance_group,
            eleve_group,
            personnel_group,
            security_group,
        ):
            content_layout.addWidget(group)
        content_layout.addWidget(save_button)
        content_layout.addWidget(self.save_confirmation_label)
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

    def _refresh_pwd_vault_count(self) -> None:
        count = self.password_vault.count()
        self.pwd_vault_count_label.setText(
            f"{count} mot(s) de passe actuellement enregistré(s)."
        )
        self.pwd_vault_clear_button.setEnabled(count > 0)

    def _on_clear_password_vault(self) -> None:
        reply = QMessageBox.question(
            self, "Vider le coffre",
            "Supprimer définitivement tous les mots de passe enregistrés par EduSync AD ? "
            "Cette action est irréversible — les comptes concernés resteront fonctionnels, "
            "seule la possibilité de reconsulter leur mot de passe ici sera perdue.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = self.password_vault.clear_all()
        self._refresh_pwd_vault_count()
        QMessageBox.information(self, "Coffre vidé", f"{removed} mot(s) de passe supprimé(s).")

    def _use_ad_domain(self) -> None:
        if self._ad_domain:
            self.mail_domain_edit.setText(self._ad_domain)

    def _update_mail_format_preview(self) -> None:
        template = self.mail_format_combo.currentText().strip()
        domain = self.mail_domain_edit.text().strip() or self._ad_domain or "domaine"
        if not template:
            self.mail_format_preview_label.setText("")
            return
        try:
            local_part = render_template(template, "Thomas", "Martin")
            self.mail_format_preview_label.setText(f"{local_part}@{domain}")
        except Exception:
            self.mail_format_preview_label.setText("(format invalide)")

    def _save(self) -> None:
        # dataclasses.replace (et non AppConfig(...) à neuf) : préserve les champs
        # que ce formulaire ne gère pas (ex. réglages LDAPS de l'écran de connexion)
        # au lieu de les réinitialiser silencieusement à leur valeur par défaut.
        config = replace(
            self.config,
            identifiant_format_eleve=self.identifier_eleve_combo.currentText().strip(),
            identifiant_format_personnel=self.identifier_personnel_combo.currentText().strip(),
            regle_doublons=self.doublon_combo.currentData(),
            politique_mdp_eleve=self.eleve_policy_form.to_policy(),
            politique_mdp_personnel=self.personnel_policy_form.to_policy(),
            domaine_mail=self.mail_domain_edit.text().strip(),
            format_mail=self.mail_format_combo.currentText().strip(),
            regle_prenom_compose=self.prenom_compose_combo.currentData(),
            groupes_classe_auto=self.groupes_auto_check.isChecked(),
            ou_parente_classes=self.ou_parente_classes_edit.text().strip(),
            ou_archive=self.ou_archive_edit.text().strip(),
            delai_suppression_jours=self.delai_spin.value(),
            theme=self.theme_combo.currentData(),
            langue=self.langue_combo.currentData(),
        )
        self.config = config
        self._on_save(config)

        self.save_confirmation_label.setVisible(True)
        QTimer.singleShot(2500, lambda: self.save_confirmation_label.setVisible(False))

        self.save_button.setText("✓ Enregistré !")
        self.save_button.setStyleSheet(
            "QPushButton { font-weight: 600; padding: 8px 18px; font-size: 14px; "
            "background-color: #1f9d55; color: white; }"
        )
        QTimer.singleShot(2000, self._reset_save_button)

    def _reset_save_button(self) -> None:
        self.save_button.setText("💾 Enregistrer les paramètres")
        self.save_button.setStyleSheet(
            "QPushButton { font-weight: 600; padding: 8px 18px; font-size: 14px; }"
        )
