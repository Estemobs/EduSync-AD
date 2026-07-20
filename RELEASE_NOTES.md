Amélioration de l'Explorateur AD pour se rapprocher encore d'un vrai outil d'admin (RSAT/ADUC), plus une simplification côté Migration.

### Explorateur AD : le double-clic fait enfin quelque chose

- **Double-clic sur une sous-OU** : navigue dedans (avant : rien ne se passait).
- **Double-clic sur un groupe** : ouvre la gestion des membres.
- **Double-clic sur un utilisateur** : ouvre l'édition d'attribut.
- **Clic droit sur une sous-OU** : nouvelle action "Ouvrir…".

### Migration : fini les chemins AD à taper à la main

L'onglet "Via l'interface" utilisait deux champs de texte libre attendant un DN complet (`OU=...,DC=...`). Remplacés par des menus déroulants alimentés directement depuis l'AD — sélectionnez simplement l'OU source et l'OU destination dans la liste.
