La fonctionnalité la plus demandée : EduSync AD peut désormais retenir les mots de passe qu'il positionne lui-même, pour les reconsulter plus tard sans repasser par une réinitialisation.

### Nouveau : coffre-fort de mots de passe

- À chaque création de compte (Modules 1 et 4) ou réinitialisation (individuelle dans l'Explorateur AD, ou en masse Module 5), le mot de passe est désormais enregistré localement, chiffré (AES-256, même mécanisme que le reste de l'appli).
- **Explorateur AD** : en sélectionnant un compte, ou en double-cliquant dessus (fiche Propriétés), une ligne **Mot de passe** apparaît — masqué par défaut, bouton 👁 pour révéler, bouton **Copier**.
- **Si le mot de passe n'est pas connu** (compte existant avant l'usage du logiciel, ou changé par un autre outil comme ADUC/PowerShell) : message clair « Non enregistré par le logiciel » avec un bouton **Réinitialiser…** direct.
- **Export** : nouveau champ **Mot de passe** dans le module Export (CSV et étiquettes) — jamais coché par défaut, avertissement affiché dès qu'il est activé (un document contenant des mots de passe en clair est à distribuer avec précaution).
- **Suppression d'un compte** (Explorateur AD ou purge différée des départs) efface aussi son mot de passe du coffre.
- **Paramètres** : nouveau bouton **Vider le coffre des mots de passe…** (avec confirmation), pour tout effacer d'un coup si besoin.
- Le mode simulation ne stocke jamais de mot de passe — ce ne seraient pas de vrais mots de passe appliqués dans l'AD.
