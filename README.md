# EduSync AD

Outil de gestion du cycle de vie des comptes utilisateurs dans un Active Directory,
conçu pour les établissements scolaires (collèges, lycées, écoles). Interface
moderne, sans connaissances PowerShell ou LDAP requises.

Voir `EduSync_AD_Cahier_des_charges.pdf` pour les spécifications complètes.

## État actuel

- Module 1 — Création de comptes : implémenté
- Modules 2 à 8 : à venir

## Prérequis

- Python 3.11+
- Un Active Directory accessible (LDAP/LDAPS) avec un compte disposant des droits
  de création/modification de comptes

## Installation (développement)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
```

## Lancement

```bash
python -m edusync_ad
```

## Tests

```bash
pytest
```

La couche de connexion AD est testée hors-ligne via la stratégie `MOCK_SYNC` de
`ldap3` (serveur LDAP simulé en mémoire) — aucun Active Directory réel n'est requis
pour faire tourner la suite de tests.

## Première connexion

Au lancement, l'écran de connexion demande :
- le nom de domaine complet (ex. `lycee-victor-hugo.local`)
- l'adresse du contrôleur de domaine
- un compte administrateur du domaine

La connexion LDAPS est tentée en priorité ; à défaut, une connexion LDAP est
établie avec un avertissement explicite. Le mot de passe n'est jamais stocké sur
disque.
