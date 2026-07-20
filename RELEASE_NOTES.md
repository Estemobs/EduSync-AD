Correctifs et améliorations demandés après un premier essai de l'Explorateur AD (clic droit, confirmations, volume de groupes affichés), plus un correctif de fiabilité sur la publication des releases elle-même.

### Confirmations de suppression simplifiées

- OU, utilisateur, groupe : la ressaisie obligatoire du nom exact est remplacée par une simple confirmation Oui/Non — jugée trop lourde à l'usage pour une opération déjà protégée par ailleurs (mode simulation, journal d'actions).

### Suppression multiple dans l'Explorateur AD

- Sélection de plusieurs comptes (Ctrl/Shift-clic) dans la liste centrale, puis **clic droit → Supprimer les comptes sélectionnés…** : une seule confirmation pour tout le lot, avec un résumé des échecs éventuels si certains comptes ne peuvent pas être supprimés.

### Listes de groupes allégées

- La liste "Groupes" (panneau gauche), le dialogue "Gérer les groupes" et la source "Groupe AD" du Module 5 n'affichent plus les dizaines de groupes système d'Active Directory (Administrateurs, Opérateurs de sauvegarde, Contrôleurs de domaine…) — seuls les groupes créés par l'établissement (classes, personnels) apparaissent désormais. Vérifié contre un vrai AD : 51 groupes réduits à 3 pertinents.

### Fiabilité de la publication des releases

- Les notes de version affichées sur GitHub se perdaient de façon répétée (contenu vide ou tronqué à un simple message de commit) à cause d'un problème dans la synchronisation Gitea → GitHub qui ne préservait pas le message du tag annoté. Les notes proviennent désormais de ce fichier, suivi normalement dans le dépôt — ne dépend plus de cette synchronisation fragile.
