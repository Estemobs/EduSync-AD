# EduSync AD

**Outil de gestion du cycle de vie des comptes Active Directory pour les établissements scolaires.**

Créez, migrez, archivez et gérez les comptes élèves et personnels en quelques clics — sans toucher aux consoles Microsoft.

---

## Téléchargement

Les binaires prêts à l'emploi sont disponibles dans les [**Releases**](../../releases/latest) :

| Plateforme | Fichier | Instructions |
|---|---|---|
| **Windows 10/11** | `EduSyncAD-windows.zip` | Décompressez n'importe où, lancez `EduSyncAD.exe` |
| **Linux** | `EduSyncAD-linux.flatpak` | `flatpak install EduSyncAD-linux.flatpak` |

Aucune installation requise sur Windows. Aucun Python nécessaire.

---

## Fonctionnalités

| Module | Description |
|--------|-------------|
| **Création de comptes** | Import CSV, génération d'identifiants et mots de passe, gestion des doublons |
| **Migration de classe** | Déplacement en masse entre OUs en fin d'année (via CSV ou interface) |
| **Arrivées en cours d'année** | Création avec vérification des doublons AD existants |
| **Gestion des départs** | Désactivation immédiate ou suppression différée avec archivage |
| **Réinitialisation MDP** | Par OU, par groupe AD ou par fichier CSV |
| **Explorateur AD** | Navigation OUs/groupes, modification d'attributs, gestion des groupes |
| **Journal d'actions** | Historique filtrable et exportable de toutes les opérations |
| **Mode simulation** | Testez tout sans écrire dans l'AD |
| **Mise à jour intégrée** | Vérification et installation des nouvelles versions depuis l'application |

---

## Connexion

Au lancement, renseignez :
- Nom de domaine (ex. `lycee-victor-hugo.local`)
- Adresse du contrôleur de domaine
- Compte administrateur du domaine

La connexion LDAPS (chiffrée, port 636) est tentée en priorité. Repli automatique sur LDAP (port 389) si indisponible.

---

## Prérequis

- Active Directory accessible sur le réseau
- Compte avec droits de création/modification de comptes utilisateurs

---

## Build depuis les sources

```bash
git clone <url-du-depot>
cd EduSync-AD
python -m venv .venv && source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
python src/edusync_ad/app.py
```

**Build Windows (.exe) :**
```bash
pip install pyinstaller cairosvg pillow
python tools/generate_icon.py
pyinstaller packaging/edusync_ad.spec
# → dist/EduSyncAD/EduSyncAD.exe
```

**Tests :**
```bash
pytest
```

---

## Format des fichiers CSV

### Création de comptes
```
prenom;nom;ou;email_perso;date_naissance
Thomas;Martin;OU=3emeA,OU=Eleves,DC=lycee,DC=local;;2010-03-15
```

### Migration
```
identifiant;ou_source;ou_destination
thomas.martin;OU=4emeA,OU=Eleves,DC=lycee,DC=local;OU=3emeA,OU=Eleves,DC=lycee,DC=local
```

### Départs / Réinitialisation MDP
```
identifiant
thomas.martin
```

Des exemples sont disponibles dans le dossier [`exemples/`](exemples/).

---

## Licence

MIT
