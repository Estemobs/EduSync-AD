Simplification et nettoyage sur retour d'usage : un module en moins, le mode simulation retiré, et plusieurs petites frictions corrigées (mot de passe qui débordait, Paramètres trop longs à scruter).

### Fusion : Création de comptes / Arrivées en cours d'année

- Les deux menus n'affichaient que des formulaires identiques — seule différence réelle : la vérification de doublon AD (compte existant avec le même prénom+nom dans l'OU cible). Elle est désormais **toujours activée**, dans un seul menu **Création de comptes**.
- Au passage, un vrai bug corrigé : cette vérification cherchait le doublon dans `row.ou` (le champ CSV brut, vide dès qu'on ne fournit qu'une colonne `classe` — le cas le plus courant) au lieu de l'OU réellement résolue. Elle cherche maintenant dans la bonne OU.

### Mode simulation retiré

- Sur demande explicite : le bouton, le court-circuit d'écriture dans la couche AD, et tous les messages "(simulé)" ont été retirés. Toutes les actions écrivent désormais réellement dans l'AD (comme c'était déjà systématiquement le cas en usage normal).

### Bandeau supérieur

- **🐞 Signaler un problème** : ouvre un ticket GitHub prérempli (version, système, dernières lignes du journal) dans le navigateur — rien n'est envoyé automatiquement, vous relisez et cliquez "Submit" vous-même. Aucun jeton d'API embarqué dans l'application distribuée.
- **Numéro de version suffixé** `-win` (Windows) ou `-lin` (Linux), pour distinguer les deux d'un coup d'œil.

### Corrections et réorganisation

- **Explorateur AD** : la ligne "Mot de passe : Non enregistré par le logiciel" débordait sur 3 lignes et tronquait le bouton "Réinitialiser…" dans l'étroit panneau de droite — corrigé (layout vertical). Le même problème touchait aussi l'affichage du mot de passe connu (champ + boutons Afficher/Copier) — corrigé de la même façon.
- **Paramètres** : éclaté en 3 onglets (Comptes, Mots de passe, Apparence) au lieu d'une longue page à scroller.
- **Sidebar** : un séparateur visuel distingue maintenant les pages d'action (Création, Migration…) du groupe Journal/Paramètres.
