"""Guide de démarrage / Assistant de bienvenue — présenté au premier lancement."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QWidget,
    QScrollArea,
    QFrame,
)
from PyQt6.QtGui import QFont, QPixmap, QIcon

from edusync_ad import __version__
from edusync_ad.core.config import AppConfig, load_config, save_config


class StartupGuide(QDialog):
    """Assistant de bienvenue multi-étapes présenté au premier lancement."""

    STEPS = [
        {
            "title": "Bienvenue dans EduSync AD",
            "text": (
                "EduSync AD est l'outil de référence pour la gestion du cycle de vie "
                "des comptes Active Directory dans les établissements scolaires.\n\n"
                "<b>Import CSV → comptes créés. Fin d'année → classes migrées. "
                "Départ → compte archivé.</b> En quelques clics, pas en PowerShell."
            ),
            "image": None,
        },
        {
            "title": "Connexion au domaine",
            "text": (
                "Au démarrage, la fenêtre de connexion vous demande :\n"
                "• <b>Serveur</b> : IP ou nom DNS de votre contrôleur de domaine\n"
                "• <b>Port</b> : 636 (LDAPS recommandé) ou 389 (LDAP)\n"
                "• <b>Domaine</b> : ex. <i>mondomaine.local</i>\n"
                "• <b>Utilisateur / Mot de passe</b> : compte admin du domaine\n\n"
                "Cochez <b>LDAPS</b> pour chiffrer les échanges. Le certificat est "
                "validé automatiquement si l'AD a une PKI, sinon vous pouvez l'accepter "
                "manuellement (stockage chiffré AES-256)."
            ),
            "image": None,
        },
        {
            "title": "Barre supérieure — vos repères permanents",
            "text": (
                "En haut de la fenêtre, toujours visible :\n"
                "● <b>Indicateur de connexion</b> — vert = connecté, orange = en cours, rouge = déconnecté\n"
                "▼ <b>Sélecteur de domaine</b> — bascule entre plusieurs sites AD (M25 multisite)\n"
                "🔧 <b>Domaines…</b> — ajoute/modifie/supprime les profils de domaine\n"
                "🐞 <b>Signaler un problème</b> — ouvre un ticket GitHub prérempli (version, système, logs)\n"
                "⟳ <b>Mises à jour</b> — vérifie/installe la dernière version (Flatpak / Windows)\n"
                "<b>vX.Y.Z</b> — version courante avec suffixe -win / -lin"
            ),
            "image": None,
        },
        {
            "title": "Barre latérale — tous vos modules",
            "text": (
                "À gauche, la navigation par modules (clic pour basculer) :\n\n"
                "<b>Gestion des comptes</b>\n"
                "• Création de comptes / Arrivées\n"
                "• Migration (fin d'année)\n"
                "• Gestion des départs\n"
                "• Réinitialisation mots de passe\n\n"
                "<b>Exploration & Export</b>\n"
                "• Explorateur AD (arborescence, recherche, clic-droit)\n"
                "• Export CSV / Étiquettes PDF (Avery L7160, QR codes)\n\n"
                "<b>Administration avancée</b>\n"
                "• Profils utilisateurs, Dossiers personnels, Quotas\n"
                "• Heures de connexion, Scripts de session\n"
                "• Espaces de classe, Modèles de groupes\n"
                "• Étiquettes / Trombinoscopes\n\n"
                "<b>Intégrations cloud</b>\n"
                "• Microsoft 365, Exchange, RDS\n\n"
                "<b>Système</b>\n"
                "• Journal d'actions (audit), Journal de l'application\n"
                "• Paramètres, Portail auto-service, API REST"
            ),
            "image": None,
        },
        {
            "title": "Premiers pas recommandés",
            "text": (
                "1. <b>Paramètres</b> (en bas à gauche) → onglet <i>Comptes</i> : "
                "définissez le format d'identifiant, le domaine mail, l'OU par défaut.\n\n"
                "2. <b>Paramètres</b> → onglet <i>Mots de passe</i> : "
                "configurez les politiques élèves / personnels, activez le coffre fort.\n\n"
                "3. <b>Paramètres</b> → onglet <i>Apparence</i> : "
                "choisissez votre thème (Clair / Sombre / Système).\n\n"
                "4. Testez avec <b>Création de comptes</b> : importez un CSV exemple "
                "(fournis dans l'application) et lancez en simulation d'abord.\n\n"
                "💡 <i>Astuce : double-clic sur la barre de titre = plein écran / fenêtre.</i>"
            ),
            "image": None,
        },
        {
            "title": "Raccourcis & Astuces",
            "text": (
                "<b>Double-clic barre de titre</b> → Plein écran / Fenêtre\n"
                "<b>F11</b> → Bascule plein écran\n"
                "<b>Ctrl+1..9</b> → Accès direct aux 9 premiers modules\n"
                "<b>Échap</b> → Ferme dialogue / annule action\n\n"
                "<b>Fichiers de configuration</b> :\n"
                "• Linux : ~/.config/edusync-ad/config.json\n"
                "• Windows : %APPDATA%/EduSync-AD/config.json\n\n"
                "<b>Journaux (logs)</b> : menu <i>Journal de l'application</i> → "
                "activez le mode debug pour le dépannage.\n\n"
                "<b>Mise à jour</b> : l'application vérifie automatiquement au démarrage. "
                "Cliquez « Mises à jour » pour forcer la vérification."
            ),
            "image": None,
        },
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant de bienvenue — EduSync AD")
        self.setModal(True)
        self.setMinimumSize(700, 520)
        self.resize(800, 580)

        self._current_step = 0
        self._build_ui()
        self._show_step(0)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stacked widget pour les étapes
        self.stack = QStackedWidget()
        for step in self.STEPS:
            page = self._create_step_page(step)
            self.stack.addWidget(page)
        layout.addWidget(self.stack, stretch=1)

        # Barre de navigation bas
        nav_bar = QFrame()
        nav_bar.setObjectName("GuideNavBar")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(16, 12, 16, 12)

        self.prev_btn = QPushButton("← Précédent")
        self.prev_btn.clicked.connect(self._prev_step)
        nav_layout.addWidget(self.prev_btn)

        self.step_indicator = QLabel()
        self.step_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_indicator.setStyleSheet("font-weight: 600; color: #666;")
        nav_layout.addWidget(self.step_indicator, stretch=1)

        self.next_btn = QPushButton("Suivant →")
        self.next_btn.clicked.connect(self._next_step)
        nav_layout.addWidget(self.next_btn)

        self.finish_btn = QPushButton("Terminer")
        self.finish_btn.clicked.connect(self.accept)
        self.finish_btn.setVisible(False)
        nav_layout.addWidget(self.finish_btn)

        self.skip_btn = QPushButton("Ignorer")
        self.skip_btn.clicked.connect(self.reject)
        nav_layout.addWidget(self.skip_btn)

        layout.addWidget(nav_bar)

    def _create_step_page(self, step: dict) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(24)

        # Titre
        title = QLabel(step["title"])
        title.setWordWrap(True)
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #1a1a2e;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Image optionnelle
        if step.get("image"):
            img_label = QLabel()
            pix = QPixmap(step["image"])
            if not pix.isNull():
                img_label.setPixmap(pix.scaledToWidth(400, Qt.TransformationMode.SmoothTransformation))
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(img_label)

        # Texte
        text = QLabel(step["text"])
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.setStyleSheet("font-size: 14px; line-height: 1.6; color: #333;")

        scroll = QScrollArea()
        scroll.setWidget(text)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll, stretch=1)

        layout.addStretch()
        return page

    def _show_step(self, index: int) -> None:
        self._current_step = index
        self.stack.setCurrentIndex(index)
        self.step_indicator.setText(f"Étape {index + 1} / {len(self.STEPS)}")

        self.prev_btn.setVisible(index > 0)
        is_last = (index == len(self.STEPS) - 1)
        self.next_btn.setVisible(not is_last)
        self.finish_btn.setVisible(is_last)

    def _next_step(self) -> None:
        if self._current_step < len(self.STEPS) - 1:
            self._show_step(self._current_step + 1)

    def _prev_step(self) -> None:
        if self._current_step > 0:
            self._show_step(self._current_step - 1)


def should_show_guide(config: AppConfig | None = None) -> bool:
    """Vérifie si le guide doit être affiché (premier lancement)."""
    if config is None:
        config = load_config()
    return not getattr(config, "startup_guide_completed", False)


def mark_guide_completed(config: AppConfig | None = None) -> None:
    """Marque le guide comme vu."""
    if config is None:
        config = load_config()
    config.startup_guide_completed = True
    save_config(config)


def run_startup_guide_if_needed(parent=None) -> bool:
    """Lance le guide si premier lancement. Retourne True si guide affiché."""
    config = load_config()
    if should_show_guide(config):
        guide = StartupGuide(parent)
        result = guide.exec()
        if result == QDialog.DialogCode.Accepted:
            mark_guide_completed(config)
        return True
    return False