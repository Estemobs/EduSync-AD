"""Module 4 — Arrivées en cours d'année (§8 du cahier des charges).

Identique au Module 1 (Création de comptes) avec une vérification
préalable des doublons dans l'AD : si un utilisateur avec le même
CN (prénom + nom) existe déjà dans l'OU cible, la ligne est marquée
« ⚠ Doublon AD » et ignorée lors de la validation.
"""

from __future__ import annotations

from edusync_ad.core.ad.connection import ADConnection
from edusync_ad.core.ad.exceptions import ADError
from edusync_ad.core.audit import AuditLog
from edusync_ad.core.config import AppConfig
from edusync_ad.core.models import RawUserRow
from edusync_ad.ui.modules.create_accounts_page import CreateAccountsPage


class InscriptionPage(CreateAccountsPage):
    """Module 4 — hérite de CreateAccountsPage et ajoute la vérification AD."""

    def __init__(
        self,
        ad_connection: ADConnection,
        config: AppConfig,
        audit_log: AuditLog,
        session_id: str,
        parent=None,
    ) -> None:
        super().__init__(ad_connection, config, audit_log, session_id, parent)
        self.generate_button.setText("3. Générer la prévisualisation (avec vérification AD)")

    def _ad_duplicate_check(self, row: RawUserRow) -> str | None:
        cn = f"{row.prenom} {row.nom}"
        try:
            existing_dn = self.ad_connection.search_user_by_cn(cn, row.ou)
        except ADError:
            return None
        if existing_dn:
            return f"Compte existant : {existing_dn}"
        return None
