<div align="center">

<img src="docs/screenshots/logo.png" alt="EduSync AD" width="120">

# EduSync AD

**La gestion du cycle de vie Active Directory — Sans jamais ouvrir une console Microsoft.**

Import CSV → comptes créés. Restructuration → comptes migrés. Départ → compte archivé.
En quelques clics, pas en PowerShell.

📖 [Guide utilisateur](docs/guide_utilisateur.md) · 📥 [Télécharger la dernière version](../../releases/latest) · 🗺️ [Feuille de route](ROADMAP.md)

</div>

---

## Pour qui ?

| Profil | Cas d'usage typiques |
|--------|---------------------|
| **Admin réseau établissement** | Rentrée 1 500 élèves, mutations fin d'année, départs ponctuels, réinitialisation MDP classe complète |
| **Admin PME / Collectivité** | Onboarding/offboarding collaborateurs, gestion OU par service/équipe, délégation helpdesk, conformité RGPD |
| **MSSP / Intégrateur** | Multi-clients (multisite), déploiement standardisé, audit centralisé, modèles réutilisables |

> **Pas seulement éducation** : la logique « prénom/nom/équipe » s'applique à toute structure
> hiérarchique (service, équipe, projet, site) — il suffit de renommer « classe » en « équipe » dans l'UI.

---

## Fonctionnalités clés

### Cycle de vie complet (v1.x — Déployé)

| Module | Description |
|--------|-------------|
| **Création de comptes / Arrivées** | Import CSV, génération identifiants/MDP/mail auto, résolution doublons, vérification AD existants, export CSV étiquettes prêt à imprimer |
| **Migration (restructuration)** | Via CSV (prénom/nom/équipe) ou interface (sélection OU), déplacement AD + groupes auto, résolution par prénom/nom |
| **Gestion des départs** | Désactivation immédiate ou archivage + purge planifiée (délai configurable, 30j par défaut) |
| **Réinitialisation MDP** | Par OU, groupe AD ou CSV, politiques différées (élèves/personnels), forçage changement à la prochaine connexion |
| **Explorateur AD** | Arborescence OUs/groupes, panneau central unifié (utilisateurs + groupes + sous-OU), clic droit complet (modif, move, MDP, groupes, suppr), recherche temps réel |
| **Export (CSV / Étiquettes PDF)** | Sélection OU ± sous-OU, champs cochables (incl. MDP mémorisés), PDF Avery L7160/L7163, QR codes, couleurs pastel |
| **Journal d'actions** | Filtres date/type/résultat/domaine/opérateur, export CSV, stockage local SQLite (append-only) |
| **Mode simulation** | Toute écriture AD = prévisualisation modifiable avant validation |

### Sécurité & Exploitation

- **LDAPS par défaut** (port 636), fallback LDAP explicite avec avertissement, certificat CA custom
- **Coffre MDP AES-256** local (machines admin), jamais sur l'AD
- **Mise à jour intégrée** : vérification, téléchargement, installation, redémarrage auto, vérification somme de contrôle
- **Configuration chiffrée** (domaine, utilisateur) via `platformdirs` + AES

### Disponible (v3.5 — Livré)

Étiquettes avancées : éditeur WYSIWYG, badges/cartes, trombinoscope photo A4/A3 et envoi d'une étiquette par mail, **multisite : plusieurs domaines/forêts AD gérés depuis une seule instance (profils chiffrés, sélecteur de site, journal par domaine)**, **délégation d'administration (RBAC) : 5 rôles, portées par OU/groupe, garde-fou sur chaque écriture AD, journal filtrable par opérateur**, **portail auto-service élève/parent : réinitialisation MDP par code mail, consultation identifiant/mail, demande création compte avec validation admin (serveur web stdlib, sans dépendance externe)**.

### Prochainement (v4+)

---

## Prérequis

- Active Directory accessible (Windows Server 2012 → 2025)
- Compte avec droits création/modification utilisateurs (Délégation OU ciblée recommandée)
- **Pas de Python requis** sur le poste admin (binaires Windows/Linux fournis)

---

## Installation rapide

```bash
# Windows 10/11
EduSyncAD-Setup.exe
# → Raccourcis Menu Démarrer/Bureau, désinstallation standard via Paramètres Windows

# Linux
flatpak install EduSyncAD-linux.flatpak
```

---

## Connexion

| Champ | Exemple |
|-------|---------|
| Domaine | `entreprise.local` ou `lycee-victor-hugo.fr` |
| Contrôleur | `dc01.entreprise.local` ou `10.0.0.5` |
| Utilisateur | `admin` (ou `ENTREPRISE\admin`) |
| Mot de passe | *Jamais stocké* |

La connexion **LDAPS chiffrée** est tentée en priorité. Repli automatique sur LDAP (port 389) si indisponible.
Si le contrôleur utilise un certificat émis par une autorité interne (cas courant), voir la [section dépannage du guide](docs/guide_utilisateur.md#10-dépannage--erreur-de-certificat-ldaps).

---

## Format CSV (exemple universel)

```csv
prenom;nom;equipe
Thomas;Martin;Comptabilité
Léa;Petit;IT
```

Seuls `prenom` et `nom` sont obligatoires. `equipe` (ex `classe`, `service`, `projet`, `site`) → OU auto-résolue.
Colonne `ou` (DN complet) acceptée pour cas avancés — voir [guide utilisateur](docs/guide_utilisateur.md).

Des exemples sont disponibles dans le dossier [`exemples/`](exemples/).

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

## Licence

MIT — Usage libre, modification, distribution, usage commercial autorisé.