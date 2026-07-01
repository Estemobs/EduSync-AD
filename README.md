# EduSync AD

Outil open source de gestion du cycle de vie des comptes utilisateurs dans un
Active Directory, conçu pour les établissements scolaires (collèges, lycées,
écoles). Interface graphique moderne, sans connaissances PowerShell ou LDAP
requises. Remplace des outils payants comme Koxo Administrator.

**Plateformes** : Windows 10/11 (`.exe`) · Linux (`.flatpak`)  
**Licence** : MIT

---

## Fonctionnalités

| Module | Description |
|--------|-------------|
| Création de comptes | Import CSV → identifiants, mots de passe et adresses mail générés automatiquement |
| Migration de classe | Déplacement d'utilisateurs entre OUs en fin d'année scolaire |
| Gestion des départs | Désactivation immédiate ou suppression différée configurable |
| Arrivées en cours d'année | Comme la création, avec détection des doublons AD |
| Réinitialisation MDP | En masse pour une OU, un groupe ou un fichier CSV |
| Explorateur AD | Navigation OUs/groupes, modification d'attributs, gestion des groupes |
| Mode simulation | Prévisualisation complète sans aucune écriture dans l'AD |
| Journal d'actions | Audit local horodaté, filtrable et exportable CSV |

---

## Prérequis

- Python **3.11+**
- Active Directory accessible sur le réseau (LDAP port 389 ou LDAPS port 636)
- Compte administrateur du domaine avec droits de création/modification de comptes

---

## Installation (développement)

```bash
git clone https://github.com/votre-repo/edusync-ad.git
cd edusync-ad

python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate

pip install -e ".[dev]"
```

## Lancement

```bash
python -m edusync_ad
```

---

## Tests

```bash
pytest
```

La couche AD est testée hors-ligne via la stratégie `MOCK_SYNC` de `ldap3`
(serveur LDAP simulé en mémoire) — aucun Active Directory réel n'est requis.

---

## Première connexion

Au lancement, l'écran de connexion demande :

| Champ | Exemple |
|-------|---------|
| Nom de domaine complet | `lycee-victor-hugo.local` |
| Contrôleur de domaine | `10.0.0.5` ou `dc01.lycee-victor-hugo.local` |
| Nom d'utilisateur | `admin` ou `DOMAINE\admin` |
| Mot de passe | *(jamais stocké sur disque)* |

- La connexion **LDAPS** (chiffrée, port 636) est tentée en priorité.
- Si LDAPS est indisponible, repli automatique sur LDAP avec avertissement explicite.
- Cochez **Mémoriser la connexion** pour sauvegarder domaine et utilisateur
  chiffrés (AES-256). Le mot de passe n'est jamais conservé.

---

## Fichiers CSV d'exemple

Des fichiers d'exemple sont disponibles dans le dossier [`exemples/`](exemples/) :

| Fichier | Usage |
|---------|-------|
| `creation_comptes.csv` | Module 1 — Création et Module 4 — Arrivées en cours d'année |
| `migration_comptes.csv` | Module 2 — Migration de classe |
| `departs.csv` | Module 3 — Gestion des départs |
| `reinitialisation_mdp.csv` | Module 5 — Réinitialisation de mot de passe |

### Format `creation_comptes.csv`

```
prenom;nom;ou;email_perso;date_naissance;numero
Thomas;Martin;OU=3emeA,OU=Eleves,DC=lycee,DC=local;t.martin@gmail.com;2010-03-15;20100315
```

Colonnes obligatoires : `prenom`, `nom`, `ou`  
Colonnes facultatives : `email_perso`, `date_naissance`, `numero`

### Format `migration_comptes.csv`

```
identifiant;ou_source;ou_destination
thomas.martin;OU=4emeA,OU=Eleves,DC=lycee,DC=local;OU=3emeA,OU=Eleves,DC=lycee,DC=local
```

### Format `departs.csv` et `reinitialisation_mdp.csv`

```
identifiant
thomas.martin
alice.durand
```

---

## Paramètres globaux

Accessibles via le menu **Paramètres** de l'application :

- **Nomenclature des identifiants** : format prédéfini (`prenom.nom`, `p.nom`, etc.) ou personnalisé avec variables (`{P}`, `{N}`, `{p1}`, `{AN}`…)
- **Règle de résolution des doublons** : suffixe numérique, lettres supplémentaires, année…
- **Politique de mot de passe** : longueur, complexité, pattern fixe — distincte pour élèves et personnels
- **Domaine mail** et format des adresses générées
- **OU d'archivage** et **délai de suppression différée** (en jours, valeur libre)
- **Thème** : Clair / Sombre
- **Langue** : Français / English

---

## Build

### Windows (.exe)

```bash
pip install pyinstaller
pyinstaller build/edusync_ad.spec
# L'exécutable est généré dans dist/EduSyncAD/
```

### Linux (.flatpak)

```bash
flatpak-builder --force-clean build-dir build/org.edusync.AD.yml
flatpak-builder --run build-dir build/org.edusync.AD.yml edusync-ad
```

---

## Structure du projet

```
src/edusync_ad/
├── app.py                        # Point d'entrée
├── core/
│   ├── ad/connection.py          # Couche LDAP/LDAPS (ldap3)
│   ├── audit.py                  # Journal d'actions (SQLite)
│   ├── config.py                 # Paramètres globaux (JSON chiffré)
│   ├── identifiers.py            # Génération des identifiants
│   └── passwords.py              # Génération des mots de passe
└── ui/
    ├── main_window.py            # Fenêtre principale + sidebar
    ├── login_dialog.py           # Écran de connexion
    ├── audit_page.py             # Journal d'actions
    ├── settings_page.py          # Paramètres globaux
    └── modules/
        ├── create_accounts_page.py   # Module 1
        ├── migration_page.py         # Module 2
        ├── depart_page.py            # Module 3
        ├── inscription_page.py       # Module 4
        ├── password_reset_page.py    # Module 5
        └── ad_explorer_page.py       # Module 6
```
