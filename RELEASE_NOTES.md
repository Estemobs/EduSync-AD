Correctif de fond sur l'Explorateur AD, qui répond directement à la confusion "OU non vide alors qu'elle semblait vide" du changelog précédent.

### Le panneau central montre enfin tout le contenu d'une OU

Jusqu'ici, cliquer sur une OU ne listait que les comptes utilisateurs — les groupes et sous-OU qui vivent dans le même conteneur restaient invisibles. C'est exactement ce qui rendait le groupe de classe auto-créé (Modules 1/2) indétectable : vider les élèves d'une classe laissait croire l'OU vide, alors que son groupe y était toujours.

- Nouvelle colonne **Type** dans le tableau central : utilisateurs, groupes et sous-OU apparaissent désormais ensemble, rien n'est caché.
- Le clic droit s'adapte au type sélectionné : menu complet sur un utilisateur, gérer les membres/supprimer sur un groupe, renvoi vers l'arborescence pour une sous-OU.
- La sélection multiple (Ctrl/Shift-clic) peut désormais mélanger utilisateurs et groupes pour une suppression groupée en une seule confirmation.

Vérifié de bout en bout contre un vrai Active Directory, y compris une suppression multiple mixte utilisateurs+groupes.
