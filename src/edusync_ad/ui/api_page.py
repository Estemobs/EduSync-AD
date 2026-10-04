"""M28 — Page UI API REST : configuration, test des endpoints, gestion des clés."""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QProgressBar,
)

from edusync_ad.core.api import APIRouter, APIHandler, APIResponse, APIError, APIServer
from edusync_ad.core.api_auth import APIKeyStore, load_api_keys, save_api_keys, APIKeyInfo
from edusync_ad.core.webhooks import (
    WebhookEvent,
    WebhookStore,
    WebhookDispatcher,
    WebhookConfig,
)
from edusync_ad.core.openapi import OpenAPISpec, generate_spec, write_spec
from edusync_ad.core.config import config_dir, AppConfig

try:
    import urllib.request
    from urllib.error import HTTPError, URLError
except ImportError:  # pragma: no cover
    urllib = None  # type: ignore  # noqa: F811


# ---------------------------------------------------------------------------
# Données Mock (pour l'UI avant connexion au vrai serveur)
# ---------------------------------------------------------------------------


@dataclass
class EndpointInfo:
    """Métadonnée pour l'affichage dans la table."""

    method: str
    path: str
    summary: str
    description: str = ""
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    responses: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {"200": {"description": "Success"}})
    security: List[Dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# API Page
# -------------------------------------------------------------------------


class APIPage(QWidget):
    """Page M28 — Portail auto-service."""

    def __init__(
        self,
        config: AppConfig,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config

        # Composants du store (singleton par session)
        self.key_store: APIKeyStore | None = None
        self.webhook_store: WebhookStore | None = None
        self.dispatcher: WebhookDispatcher | None = None
        self.api_server: APIServer | None = None

        # État UI
        self.api_key_entered = False
        self.current_test_endpoint: Optional[str] = None
        self.test_in_progress = False

        self._build_ui()
        self._refresh_stores()

    # -- Construction UI -----------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Onglets ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_auth_tab(), "Clés API")
        self.tabs.addTab(self._build_endpoints_tab(), "Endpoints")
        self.tabs.addTab(self._build_webhooks_tab(), "Webhooks")
        self.tabs.addTab(self._build_openapi_tab(), "OpenAPI")
        layout.addWidget(self.tabs)

        # --- Barre d'état ---
        self.status_bar = QLabel("Prêt — aucune clé API chargée")
        self.status_bar.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.status_bar)

    # -- Onglet Clés API -----------------------------------------------------

    def _build_auth_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Zone de saisie clé
        group = QGroupBox("Authentification API")
        form = QFormLayout(group)

        self.keypair_group = QGroupBox("Générer / Gérer une clé")
        kform = QFormLayout(self.keypair_group)

        self.key_name = QLineEdit()
        self.key_name.setPlaceholderText("Ex. : admin-du-jour")
        kform.addRow("Nom", self.key_name)

        self.key_permissions = QComboBox()
        self.key_permissions.addItems(
            ["*"]  # admin complet
            + ["users:read", "users:write", "users:password"]
            + ["groups:read", "groups:write"]
            + ["ous:read", "ous:write"]
            + ["audit:read", "config:read", "config:write"]
            + ["portal:read", "portal:write"]
            + ["webhooks:read", "webhooks:write"]
        )
        kform.addRow("Permissions", self.key_permissions)

        self.key_expires = QSpinBox()
        self.key_expires.setRange(1, 3650)
        self.key_expires.setValue(30)
        self.key_expires.setSuffix(" jours")
        kform.addRow("Expiration (jours)", self.key_expires)

        btn_gen = QPushButton("Générer une clé")
        btn_gen.clicked.connect(self._on_generate_key)
        kform.addRow(btn_gen)

        self.key_list_group = QGroupBox("Clés existantes")
        lform = QVBoxLayout(self.key_list_group)
        self.key_list_table = QTableWidget(0, 5)
        self.key_list_table.setHorizontalHeaderLabels(["ID", "Nom", "Permissions", "Créée", "Actif"])
        self.key_list_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        lform.addWidget(self.key_list_table)

        btn_refresh_keys = QPushButton("Actualiser")
        btn_refresh_keys.clicked.connect(self._refresh_key_list)
        lform.addWidget(btn_refresh_keys)

        btn_revoke_selected = QPushButton("Révoquer sélectionnée")
        btn_revoke_selected.clicked.connect(self._on_revoke_key)
        lform.addWidget(btn_revoke_selected)

        layout.addWidget(group)
        layout.addWidget(self.keypair_group)
        layout.addWidget(self.key_list_group)

        # Test rapide avec la clé par défaut
        group2 = QGroupBox("Test rapide (clé par défaut)")
        tform = QFormLayout(group2)
        self.test_key = QLineEdit()
        self.test_key.setPlaceholderText("edusync_admin_default_change_me ou votre clé")
        tform.addRow("Clé de test", self.test_key)

        btn_test = QPushButton("Tester l'API")
        btn_test.clicked.connect(self._on_test_api_key)
        tform.addRow(btn_test)

        layout.addWidget(group2)

        return widget

    def _refresh_key_list(self) -> None:
        if not self.key_store:
            return
        keys = self.key_store.list_keys(include_inactive=False)
        self.key_list_table.setRowCount(len(keys))
        for row, info in enumerate(keys):
            items = [
                info.id,
                info.name,
                ", ".join(info.permissions) if info.permissions else "*",
                info.created_at[:10] if info.created_at else "",
                "Oui" if info.active else "Non",
            ]
            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                if col == 4 and not info.active:
                    item.setForeground(Qt.GlobalColor.red)
                self.key_list_table.setItem(row, col, item)

    def _on_generate_key(self) -> None:
        name = self.key_name.text().strip() or "Non nommé"
        perms = [p.strip() for p in self.key_permissions.currentText().split(",") if p.strip()]
        expires_days = self.key_expires.value()
        try:
            full_key, info = self.key_store.create(name=name, permissions=perms, expires_days=expires_days)
            QMessageBox.information(
                self,
                "Clé générée",
                f"Clé API créée avec succès.\n\n"
                f"ID : {info.id}\n"
                f"Préfixe : {info.prefix}\n"
                f"Permissions : {', '.join(info.permissions)}\n"
                f"Clé complète : {full_key}\n\n"
                "⚠️  Conservez cette clé en sécurité : elle ne sera plus affichée.",
            )
            # Afficher le prefix pour référence future
            self.key_name.setText(info.prefix)
            self._refresh_key_list()
        except ValueError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))

    def _on_test_api_key(self) -> None:
        """Teste la clé API saisie en faisant une requête simple vers l'endpoint /health."""
        test_key = self.test_key.text().strip()
        if not test_key:
            QMessageBox.warning(self, "Clé manquante", "Entrez une clé API à tester.")
            return

        # Tenter de joindre le serveur API local (port par défaut 8080)
        import urllib.request
        import urllib.error
        import json

        url = "http://127.0.0.1:8080/api/v1/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {test_key}"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8")
                    QMessageBox.information(
                        self,
                        "Test réussi",
                        f"✅ Clé API valide\n\nRéponse du serveur :\n{body}",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Échec du test",
                        f"Code HTTP {resp.status}\nLa clé pourrait être invalide ou expirée.",
                    )
        except urllib.error.HTTPError as exc:
            QMessageBox.warning(
                self,
                "Échec du test",
                f"Erreur HTTP {exc.code} : {exc.reason}\n\n"
                f"Vérifiez que le serveur API est démarré (onglet OpenAPI → Générer le spec)\n"
                f"et que la clé est correcte.",
            )
        except urllib.error.URLError as exc:
            QMessageBox.warning(
                self,
                "Serveur inaccessible",
                f"Impossible de joindre le serveur API :\n{exc.reason}\n\n"
                f"Le serveur API REST (port 8080) doit être démarré pour tester la clé.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", f"Erreur inattendue : {exc}")

    def _on_revoke_key(self) -> None:
        selected = self.key_list_table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Sélection", "Sélectionnez une ligne à révoquer.")
            return
        key_id = selected[0].text()
        reply = QMessageBox.question(
            self,
            "Confirmer révocation",
            f"Révoquer la clé {key_id} ?\nLes événements déjà traités ne seront pas annulés.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self.key_store.revoke(key_id):
            QMessageBox.information(self, "Clé révoquée", f"Clé {key_id} révoquée.")
            self._refresh_key_list()
        else:
            QMessageBox.warning(self, "Erreur", f"Impossible de révoquer la clé {key_id}.")

    # -- Onglet Endpoints ----------------------------------------------------

    def _build_endpoints_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Sélection de la clé en cours
        key_group = QGroupBox("Clé API active")
        kform = QFormLayout(key_group)
        self.active_key_label = QLabel("Aucune clé")
        self.active_key_label.setStyleSheet("color: #b00; font-weight: 600;")
        kform.addRow("Clé en cours", self.active_key_label)
        btn_use_default = QPushButton("Utiliser la clé par défaut")
        btn_use_default.clicked.connect(self._use_default_key)
        kform.addRow(btn_use_default)
        layout.addWidget(key_group)

        # Table des endpoints
        self.endpoints_table = QTableWidget(0, 4)
        self.endpoints_table.setHorizontalHeaderLabels(["Méthode", "Chemin", "Résumé", "Paramètres"])
        self.endpoints_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.endpoints_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        layout.addWidget(self.endpoints_table)

        # Actions
        actions = QHBoxLayout()
        btn_test = QPushButton("Tester sélectionné")
        btn_test.clicked.connect(self._on_test_endpoint)
        actions.addWidget(btn_test)
        btn_doc = QPushButton("Doc OpenAPI")
        btn_doc.clicked.connect(self._on_open_doc)
        actions.addWidget(btn_doc)
        layout.addLayout(actions)

        # Zone de test
        test_group = QGroupBox("Tester un endpoint")
        tform = QFormLayout(test_group)
        self.test_path = QLineEdit()
        self.test_path.setPlaceholderText("/api/v1/users (GET)")
        tform.addRow("Endpoint", self.test_path)
        self.test_body = QTextEdit()
        self.test_body.setMaximumHeight(80)
        tform.addRow("Body JSON", self.test_body)
        self.test_resp = QTextEdit()
        self.test_resp.setMaximumHeight(80)
        self.test_resp.setReadOnly(True)
        tform.addRow("Réponse", self.test_resp)
        btn_exec = QPushButton("Exécuter")
        btn_exec.clicked.connect(self._on_exec_test)
        tform.addRow(btn_exec)
        layout.addWidget(test_group)

        # Filtre
        filt_group = QGroupBox("Filtrer")
        ffilt = QFormLayout(filt_group)
        self.filter_method = QComboBox()
        self.filter_method.addItems(["Tous", "GET", "POST", "PUT", "PATCH", "DELETE"])
        ffilt.addRow("Méthode", self.filter_method)
        self.filter_path = QLineEdit()
        self.filter_path.setPlaceholderText("Filtre par chemin (ex: /users)")
        ffilt.addRow("Chemin", self.filter_path)
        btn_apply_filter = QPushButton("Appliquer")
        btn_apply_filter.clicked.connect(self._apply_filter)
        ffilt.addRow(btn_apply_filter)
        layout.addWidget(filt_group)

        return widget

    def _refresh_endpoints(self) -> None:
        # Remplir la table à partir du router global
        from edusync_ad.core.api_routes import router as global_router

        routes = []
        for route in global_router._routes:
            # Construire un résumé minimal
            summary = route.pattern.replace("{", "<").replace("}", ">")
            params = ", ".join(route.param_names) if route.param_names else ""
            routes.append(
                EndpointInfo(
                    method=route.method,
                    path=route.pattern,
                    summary=f"{route.method} {summary}",
                    description="",
                    parameters=[],
                    responses={},
                    security=[],
                )
            )

        # Trier par méthode puis chemin
        routes.sort(key=lambda r: (r.method, r.path))

        self.endpoints_table.setRowCount(len(routes))
        for row, ep in enumerate(routes):
            items = [
                ep.method,
                ep.path,
                ep.summary[:60] + ("..." if len(ep.summary) > 60 else ""),
                ep.parameters,
            ]
            for col, val in enumerate(items):
                item = QTableWidgetItem(str(val) if val else "")
                self.endpoints_table.setItem(row, col, item)

    def _use_default_key(self) -> None:
        self.key_store = APIKeyStore()  # vide par défaut
        # Utiliser la clé par défaut (hardcodée dans api_routes.py pour le demo)
        self.active_key_label.setText("clé par défaut (démo)")
        self.active_key_label.setStyleSheet("color: #1f9d55; font-weight: 600;")
        self._refresh_key_list()
        self._refresh_endpoints()

    # -- Onglet Webhooks -----------------------------------------------------

    def _build_webhooks_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Liste webhooks
        self.webhook_list = QTableWidget(0, 5)
        self.webhook_list.setHorizontalHeaderLabels(["ID", "Nom", "Événements", "Actif", "Dernier trigger"])
        self.webhook_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.webhook_list)

        # Actions
        wh_actions = QHBoxLayout()
        btn_refresh_wh = QPushButton("Actualiser")
        btn_refresh_wh.clicked.connect(self._refresh_webhook_list)
        wh_actions.addWidget(btn_refresh_wh)
        btn_create = QPushButton("Créer")
        btn_create.clicked.connect(self._on_create_webhook)
        wh_actions.addWidget(btn_create)
        btn_test = QPushButton("Tester sélectionné")
        btn_test.clicked.connect(self._on_test_webhook)
        wh_actions.addWidget(btn_test)
        layout.addLayout(wh_actions)

        # Création de webhook
        create_grp = QGroupBox("Créer un webhook")
        wcform = QFormLayout(create_grp)
        self.wh_name = QLineEdit()
        self.wh_name.setPlaceholderText("Ex. : Serveur LDAP events")
        wcform.addRow("Nom", self.wh_name)
        self.wh_url = QLineEdit()
        self.wh_url.setPlaceholderText("https://mon-serveur.local/webhooks")
        wcform.addRow("URL cible", self.wh_url)
        self.wh_events = QComboBox()
        self.wh_events.addItem("Tous les événements", "*")
        for ev in WebhookEvent.all_events():
            self.wh_events.addItem(ev, ev)
        wcform.addRow("Événements", self.wh_events)
        self.wh_secret = QLineEdit()
        self.wh_secret.setPlaceholderText("(optionnel, laissé vide si non chiffré)")
        wcform.addRow("Secret (HMAC)", self.wh_secret)
        btn_wh_create = QPushButton("Créer webhook")
        btn_wh_create.clicked.connect(self._on_create_webhook)
        wcform.addRow(btn_wh_create)
        layout.addWidget(create_grp)

        # Zone de log de diffusion
        log_grp = QGroupBox("Journal des livraisons récentes")
        llayout = QVBoxLayout(log_grp)
        self.webhook_log = QTextEdit()
        self.webhook_log.setMaximumHeight(120)
        self.webhook_log.setReadOnly(True)
        llayout.addWidget(self.webhook_log)
        # Bouton refresh manuel
        btn_refresh_log = QPushButton("Actualiser le journal")
        btn_refresh_log.clicked.connect(self._refresh_webhook_log)
        llayout.addWidget(btn_refresh_log)
        layout.addWidget(log_grp)

        return widget

    def _refresh_webhook_list(self) -> None:
        if not self.webhook_store:
            return
        whs = self.webhook_store.list(active_only=True)
        self.webhook_list.setRowCount(len(whs))
        for row, wh in enumerate(whs):
            events_str = ", ".join(wh.events) if wh.events else "—"
            items = [
                wh.id,
                wh.name[:20] + ("..." if len(wh.name) > 20 else ""),
                events_str[:30] + ("..." if len(events_str) > 30 else ""),
                "Oui" if wh.active else "Non",
                wh.last_triggered_at[:13] if wh.last_triggered_at else "—",
            ]
            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                self.webhook_list.setItem(row, col, item)

    def _refresh_webhook_log(self) -> None:
        if not self.webhook_store:
            return
        # On peut lire le SQLite directement ou compter les entrées
        # Pour l'instant, on vide et affiche un message
        self.webhook_log.setPlainText("Journal des livraisons (vide pour l'instant)\n"
                                      "Les livraisons sont stockées en SQLite dans data/")

    def _on_create_webhook(self) -> None:
        name = self.wh_name.text().strip()
        url = self.wh_url.text().strip()
        events = self.wh_events.currentData()
        if not name or not url:
            QMessageBox.warning(self, "Champs requis", "Nom et URL sont obligatoires.")
            return
        try:
            wh_config = WebhookConfig(
                id=f"w{__import__('uuid').uuid4().hex[:8]}",
                name=name,
                url=url,
                events=[events] if events != "*" else ["*"],
                secret=self.wh_secret.text().strip() or "",
                active=True,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            self.webhook_store.create(wh_config)
            QMessageBox.information(self, "Webhook créé", f"Webhook {name} créé avec ID {wh_config.id}.")
            self._refresh_webhook_list()
        except ValueError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))

    def _on_test_webhook(self) -> None:
        selected = self.webhook_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "Sélection", "Sélectionnez un webhook à tester.")
            return
        wh_id = selected[0].text()
        wh = self.webhook_store.get(wh_id)
        if not wh:
            return

        # Préparer un payload de test
        payload = {"event": "test", "timestamp": datetime.now(timezone.utc).isoformat()}
        body = json.dumps({"event": "user.created", "data": payload}).encode("utf-8")
        parsed = __import__('urllib.parse').urlparse(wh.url)
        req = __import__('urllib.request').Request(
            wh.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Event": "test",
                "X-Webhook-Delivery": wh.id,
            },
            method="POST",
        )
        try:
            with __import__('urllib.request').urlopen(req, timeout=5) as resp:
                resp_body = resp.read().decode("utf-8")[:200]
                QMessageBox.information(self, "Webhook test", f"Code {resp.status} : {resp_body}")
        except Exception as exc:
            QMessageBox.warning(self, "Erreur webhook test", str(exc))

    # -- Onglet OpenAPI ------------------------------------------------------

    def _build_openapi_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Génération du spec
        gen_grp = QGroupBox("Générer la spécification OpenAPI")
        gform = QFormLayout(gen_grp)
        self.spec_title = QLineEdit("EduSync AD REST API")
        self.spec_title.setText(self.config.theme if hasattr(self.config, 'theme') else "EduSync AD REST API")
        gform.addRow("Titre", self.spec_title)
        self.spec_version = QLineEdit("1.0.0")
        gform.addRow("Version", self.spec_version)
        btn_gen = QPushButton("Générer le spec JSON")
        btn_gen.clicked.connect(self._on_generate_spec)
        gform.addRow(btn_gen)
        self.spec_status = QLabel("Aucun spec généré")
        gform.addRow("Statut", self.spec_status)
        layout.addWidget(gen_grp)

        # Zone d'affichage du spec
        self.spec_viewer = QTextEdit()
        self.spec_viewer.setMaximumHeight(400)
        self.spec_viewer.setReadOnly(True)
        layout.addWidget(QLabel("Document OpenAPI généré :"))
        layout.addWidget(self.spec_viewer)

        # Boutons d'action
        btns = QHBoxLayout()
        btn_save = QPushButton("Enregistrer sur disque (api-spec.json)")
        btn_save.clicked.connect(self._on_save_spec)
        btns.addWidget(btn_save)
        btn_clear = QPushButton("Effacer")
        btn_clear.clicked.connect(self._on_clear_spec)
        btns.addWidget(btn_clear)
        layout.addLayout(btns)

        return widget

    def _on_generate_spec(self) -> None:
        try:
            spec = generate_spec(__import__('edusync_ad.core.api_routes').router)
            self.spec_viewer.setPlainText(json.dumps(spec.__dict__, ensure_ascii=False, indent=2))
            self.spec_status.setText("Spec généré avec succès")
            self.spec_status.setStyleSheet("color: #1f9d55;")
        except Exception as exc:
            self.spec_status.setText(f"Erreur : {exc}")
            self.spec_status.setStyleSheet("color: #b91c1c;")

    def _on_save_spec(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la spécification OpenAPI",
            str(config_dir() / "api-spec.json"),
            "JSON (*.json);;Tous fichiers (*)",
        )
        if not path:
            return
        try:
            spec = __import__('edusync_ad.core.openapi').generate_spec(__import__('edusync_ad.core.api_routes').router)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec.__dict__, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Enregistré", f"Spec enregistré sous {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible d'enregistrer : {exc}")

    def _on_clear_spec(self) -> None:
        self.spec_viewer.clear()
        self.spec_status.setText("Aucun spec généré")

    def _on_open_doc(self) -> None:
        """Ouvre la documentation OpenAPI dans le navigateur."""
        try:
            import webbrowser
            # Ouvre l'UI Swagger locale si le serveur tourne, sinon doc en ligne
            webbrowser.open("http://127.0.0.1:8080/docs")
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible d'ouvrir la doc : {exc}")

    # -- Utilitaires ---------------------------------------------------------

    @pyqtSlot()
    def _apply_filter(self) -> None:
        meth = self.filter_method.currentText()
        pat = self.filter_path.text().strip()
        # Filtrer la table déjà affichée
        for row in range(self.endpoints_table.rowCount):
            method_item = self.endpoints_table.item(row, 0)
            path_item = self.endpoints_table.item(row, 1)
            if meth != "Tous" and method_item.text() != meth:
                self.endpoints_table.setRowHidden(row, True)
            elif pat and pat.lower() not in path_item.text().lower():
                self.endpoints_table.setRowHidden(row, True)
            else:
                self.endpoints_table.setRowHidden(row, False)

    def _on_test_endpoint(self) -> None:
        selected = self.endpoints_table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Sélection", "Sélectionnez un endpoint dans la table.")
            return
        method = selected[0].text()
        path = selected[1].text()
        self.test_path.setText(f"{method} {path}")
        self.test_body.clear()
        self.test_resp.clear()

    def _on_exec_test(self) -> None:
        endpoint = self.test_path.text().strip()
        if not endpoint:
            return
        body_text = self.test_body.toPlainText().strip()
        body = None
        if body_text:
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                QMessageBox.warning(self, "JSON invalide", "Le body n'est pas un JSON valide.")
                return

        # Simple test : essayer de joindre l'endpoint avec la clé par défaut
        # Pour l'instant, on affiche juste ce qui serait envoyé
        QMessageBox.information(
            self,
            "Test endpoint",
            f"Endpoint : {endpoint}\n"
            f"Method GET par défaut\n"
            f"Body : {json.dumps(body, ensure_ascii=False) if body else 'aucun'}\n\n"
            "Pour tester vrai, démarrer le serveur API (M28)."
        )

    # -- Lifecycle / stores --------------------------------------------------

    def _refresh_stores(self) -> None:
        # Charger le key_store
        self.key_store = APIKeyStore()
        # Charger le webhook store
        self.webhook_store = __import__('edusync_ad.core.webhooks').load_webhooks()

        # Remplir les listes
        self._refresh_key_list()
        self._refresh_webhook_list()

        # Mettre à jour l'état
        if self.key_store and self.key_store.list_keys():
            self.active_key_label.setText(f"{len(self.key_store.list_keys())} clé(s) chargée(s)")
            self.active_key_label.setStyleSheet("color: #1f9d55; font-weight: 600;")
        else:
            self.active_key_label.setText("Aucune clé")
            self.active_key_label.setStyleSheet("color: #b00; font-weight: 600;")

    def shutdown(self) -> None:
        if self.dispatcher:
            self.dispatcher.stop()


def main() -> None:
    """Point d'entrée debug (lancer depuis la ligne de commande)."""
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    from edusync_ad.core.config import AppConfig
    page = APIPage(AppConfig())
    page.show()
    app.exec()


if __name__ == "__main__":
    main()