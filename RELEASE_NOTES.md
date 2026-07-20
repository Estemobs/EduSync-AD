Nouveau module d'export (CSV et étiquettes PDF imprimables), et trois vrais bugs corrigés dans l'Explorateur AD suite aux retours d'usage sur le double-clic façon RSAT de la version précédente.

### Nouveau : module Export (CSV / étiquettes)

- Nouvelle entrée de menu **Export (CSV / étiquettes)**. Sélectionnez une OU (avec ou sans ses sous-OU), prévisualisez les comptes, cochez les champs à inclure (identifiant, nom complet, prénom, nom, classe/OU, adresse mail, état).
- **Export CSV** : mêmes conventions que le reste de l'appli (`;`, UTF-8).
- **Export étiquettes PDF** : planches A4 imprimables, formats standards du commerce (Avery L7160 — 21 étiquettes/planche ; Avery L7163 — 14 étiquettes/planche, format large), plusieurs planches générées automatiquement si nécessaire.

### Explorateur AD : le double-clic marche vraiment cette fois

- **Clic sur une OU imbriquée** : elle se déplie désormais automatiquement dans l'arborescence, à n'importe quelle profondeur — avant, seule la petite flèche fonctionnait, ce qui donnait l'impression que les sous-OU "ne s'ouvraient pas".
- **Double-clic sur un groupe dans l'onglet Groupes** : ouvre enfin la gestion des membres — cet onglet n'avait aucun gestionnaire de double-clic jusqu'ici, le double-clic n'y faisait donc rien.
- **Double-clic sur un utilisateur** : ouvre une vraie fiche **Propriétés**, façon RSAT — tous les attributs modifiables réunis dans une seule fenêtre (au lieu d'un par un), avec la case **Compte activé** et des raccourcis directs vers changer d'OU / réinitialiser le mot de passe / gérer les groupes. Avant, cette action dépendait d'un état interne fragile et pouvait rester silencieusement sans effet.

### Migration

- L'onglet "Via l'interface" utilisait déjà des menus déroulants d'OU depuis la version précédente — confirmé toujours en place, aucun changement nécessaire ici.
