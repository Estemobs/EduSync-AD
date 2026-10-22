<div align="center">

<img src="docs/screenshots/logo.png" alt="EduSync AD" width="120">

# EduSync AD

**La gestion du cycle de vie Active Directory — Sans jamais ouvrir une console Microsoft.**

Import CSV → comptes créés. Restructuration → comptes migrés. Départ → compte archivé.
En quelques clics, pas en PowerShell.

📖 [Guide utilisateur](docs/guide_utilisateur.md) · 📥 [Télécharger la dernière release](https://github.com/estemobs/EduSync-AD/releases/latest) · 🗺️ [Feuille de route Go](ROADMAP_GO_MIGRATION.md) · 📊 [État d'avancement détaillé](docs/STATUS.md)

</div>

---

## Pour qui ?

| Profil | Cas d'usage typiques |
|--------|---------------------|
| **Admin réseau établissement** | Rentrée 1 500 élèves, mutations fin d'année, départs ponctuels, réinitialisation MDP classe complète |
| **Admin PME / Collectivité** | Onboarding/offboarding collaborateurs, gestion OU par service/équipe, délégation helpdesk, conformité RGPD |
| **MSSP / Intégrateur** | Multi-clients (multisite), déploiement standardisé, audit centralisé, modèles réutilisables |

> **Pas seulement éducation** : la logique « prénom/nom/équipe » s'applique à toute structure hiérarchique (service, équipe, projet, site) — il suffit de renommer « classe » en « équipe » dans l'UI.

---

## Fonctionnalités clés

### ✅ Core v1.x — **Terminé** (M1 → M11 + M10/M11)

| Module | Statut | Description |
|--------|--------|-------------|
| **M1 — Création de comptes** | ✅ | Import CSV, génération identifiants/MDP/mail auto, résolution doublons, vérification AD existants, export CSV étiquettes |
| **M2 — Migration / Restructuration** | ✅ | Via CSV (prénom/nom/équipe) ou UI (sélection OU), déplacement AD + groupes auto, résolution par prénom/nom |
| **M3 — Gestion des départs** | ✅ | Désactivation immédiate ou archivage + purge planifiée (délai configurable, 30j défaut), restauration |
| **M4 — Réinitialisation MDP** | ✅ | Par OU, groupe AD ou CSV, politiques différées (élèves/personnels), forçage changement 1er logon |
| **M5 — Explorateur AD** | ✅ | Arborescence OUs/groupes, panneau central unifié, recherche temps réel, détails user/groupe/OU |
| **M6 — Export (CSV / PDF étiquettes)** | ✅ | Sélection OU ± sous-OU, champs cochables, PDF Avery L7160/L7163, QR codes, trombinoscope |
| **M7 — Journal d'actions** | ✅ | Filtres date/type/résultat/domaine/opérateur, export CSV, SQLite append-only |
| **M8 — Paramètres globaux** | ✅ | 3 onglets : Comptes, Mots de passe, Apparence (thème/ langue) |
| **M9 — Connexion LDAPS/LDAP** | ✅ | Pool connexions, TLS, SASL/GSSAPI, fallback LDAP 389 + avertissement, health check |
| **M10 — Mise à jour intégrée** | ✅ | Vérification GitHub releases, téléchargement, SHA-256, installation préparée |
| **M11 — Mode simulation + progression** | ✅ | Validate→Simulate→Execute→Audit sur toute écriture AD, barre progression + annulation |

### ✅ Modules avancés — **Tous implémentés** (M12 → M32 + fondations)

| Lot | Modules | État code |
|-----|---------|-----------|
| **Lot B** (M12-M24 + T2) | M12 Photos, M13 Profils, M14 Quotas, M15 Logon scripts, M16 Heures connexion, M17 Home dirs, M18 Class spaces, M19 M365/Graph, M20 Exchange, M21 RDS, M22 Advanced I/O, M23 Group templates, M24 Label Studio, T2 Cache AD | ✅ Code complet |
| **Lot C** (M25-M32) | M25 Multisite, M26 RBAC, M27 Portail, M28 API/Webhooks, M29 Assistant IA, M30 Conformité, M31 DFS, M32 Backup/Restore, Coffre MDP | ✅ Code complet |

### Sécurité & Exploitation

- **LDAPS par défaut** (port 636), fallback LDAP explicite avec avertissement, certificat CA custom
- **Coffre MDP AES-256** local (machines admin), jamais sur l'AD
- **Mise à jour intégrée** : vérification, téléchargement, installation, redémarrage auto, checksum
- **Configuration chiffrée** (domaine, utilisateur) via `platformdirs` + AES-GCM
- **RBAC 5 rôles** + portées OU/groupe, garde-fou sur chaque écriture AD
- **Conformité** : RGPD, MFA admin (TOTP), session unique, PSO, signature exports HMAC-SHA256

---

## Prérequis

- Active Directory accessible (Windows Server 2012 → 2025)
- Compte avec droits création/modification utilisateurs (Délégation OU ciblée recommandée)
- **Pas de Python requis** sur le poste admin (binaires Windows/Linux fournis)

---

## Installation

### Windows 10/11
```bash
EduSyncAD-Setup.exe
```
→ Raccourcis Menu Démarrer/Bureau, désinstallation standard via Paramètres Windows

### Linux
```bash
# AppImage (recommandé - WebKitGTK embarqué)
chmod +x EduSyncAD-linux.AppImage && ./EduSyncAD-linux.AppImage

# Archive binaire (nécessite libwebkit2gtk-4.1-0 installé)
tar xzf edusync-ad-linux-amd64.tar.gz && ./edusync-ad
```

---

## Connexion

| Champ | Exemple |
|-------|---------|
| Domaine | `entreprise.local` ou `lycee-victor-hugo.fr` |
| Contrôleur | `dc01.entreprise.local` ou `10.0.0.5` |
| Utilisateur | `admin` (ou `ENTREPRISE\admin`) |
| Mot de passe | *Jamais stocké* |

La connexion **LDAPS chiffrée** est tentée en priorité. Repli automatique sur LDAP (port 389 + StartTLS) si indisponible.
Si le contrôleur utilise un certificat émis par une autorité interne, voir la [section dépannage du guide](docs/guide_utilisateur.md#10-dépannage--erreur-de-certificat-ldaps).

---

## Format CSV (exemple universel)

```csv
prenom;nom;equipe
Thomas;Martin;Comptabilité
Léa;Petit;IT
```

Seuls `prenom` et `nom` sont obligatoires. `equipe` (ex `classe`, `service`, `projet`, `site`) → OU auto-résolue.
Colonne `ou` (DN complet) acceptée pour cas avancés — voir [guide utilisateur](docs/guide_utilisateur.md).
Des exemples dans le dossier [`exemples/`](exemples/).

---

## Build depuis les sources (Go)

```bash
# Prérequis : Go 1.27+, Task (go install github.com/go-task/task/v3/cmd/task@latest)
git clone https://github.com/estemobs/EduSync-AD.git
cd EduSync-AD

task --list          # toutes les commandes
task dev             # application desktop (Wails v3, mode dev)
task cli -- paths --json   # CLI
task test            # tests Go (28 packages)
task lint            # gofmt + go vet
task build:all       # cross-build → dist/ (5 cibles CLI)
```

> La version Python 1.17.1 reste en production jusqu'à la fin de la migration. Les deux stacks sont testées en CI (`ci.yml`).

---

## État d'avancement — Résumé visuel

| Priorité | Description | Progression |
|----------|-------------|-------------|
| **P0** | Starter kit Go, config, crypto, pool AD, modèles, audit, testutil, CLI, Wails v3 | ✅ **100%** |
| **P1** | Câblage Core M1→M11 (UI bout en bout) | ✅ **100%** |
| **P2.1** | Pipeline release α1 (Windows InnoSetup + Linux AppImage) | ✅ **100%** (CI prêt) |
| **P2.2** | Un module avancé = une release (M13→M32) | ✅ Code 100% — releases à déclencher |
| **P2.5** | Checklist pré-alpha (tests manuels Windows + AD réel) | 🔲 **En attente validation** |
| **P3** | Campagne tests (intégration Samba, e2e, benchmarks) | ⏳ Démarre post-α1 |

### Prochaines releases planifiées (prêtes à être taguées)

| Tag | Module | État |
|-----|--------|------|
| `v0.1.0-alpha.1` | **Core M1→M11** (α1) | ✅ CI déclenchée |
| `v0.2.0` | M13 Profils utilisateurs | ✅ Implémenté |
| `v0.3.0` | M16 Heures de connexion | ✅ Implémenté |
| `v0.4.0` | M15 Scripts logon/logoff | ✅ Implémenté |
| `v0.5.0` | M17 Dossiers personnels | ✅ Implémenté |
| `v0.6.0` | M14 Quotas disque | ✅ Implémenté |
| `v0.7.0` | M18 Espaces partagés classe | ✅ Implémenté |
| `v0.8.0` | T2 Cache AD SQLite | ✅ Implémenté |
| `v0.9.0` | M12 Photos d'identité | ✅ Implémenté |
| `v0.10.0` | M22 Import/Export avancés | ✅ Implémenté |
| `v0.11.0` | M19 Microsoft 365 / Entra ID | ✅ Implémenté |
| `v0.12.0` | M20 Exchange | ✅ Implémenté |
| `v0.13.0` | M21 RDS / Bureau à distance | ✅ Implémenté |
| `v0.14.0` | M23 Modèles de groupes | ✅ Implémenté |
| `v0.15.0` | M24 Étiquettes avancées | ✅ Implémenté |
| `v0.16.0+` | M25→M32 + Coffre MDP | ✅ Implémenté |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Feuille de route Go](ROADMAP_GO_MIGRATION.md) | 3 priorités, phases, critères acceptation, risques, prompts IA |
| [État d'avancement](docs/STATUS.md) | Vue rapide modules, tests, build, prochaine action |
| [Architecture Go](docs/ARCHITECTURE.md) | Architecture cible, flux UI, paquets, formats partagés Python↔Go |
| [Environnement test AD](docs/AD_TEST_ENV.md) | Samba AD DC en container (LDAPS, Kerberos, DNS, GPO, PSO) |
| [Guide utilisateur](docs/guide_utilisateur.md) | Manuel complet v1.x (modules, connexion, dépannage) |
| [Migration Go - modules](docs/migration/) | Fiches détaillées par module (objectif, ref Python, Go, validation) |
| [Référence Python v1.x](docs/python/) | Périmètre complet M1-M32 + fondations T0-T2 pour parité |

---

## Statut CI / Qualité

![Go Tests](https://github.com/estemobs/EduSync-AD/actions/workflows/ci.yml/badge.svg)
![Release Go](https://github.com/estemobs/EduSync-AD/actions/workflows/release-go.yml/badge.svg)

| Métrique | Cible | Actuel |
|----------|-------|--------|
| `gofmt -l` | 0 fichier | ✅ 0 |
| `go vet` | Vert | ✅ Vert |
| Tests Go | >0 | ✅ 28 packages OK |
| Tests Python | Vert | ✅ 843 passés |
| CLI cross-build | 5 cibles | ✅ Linux/Win/macOS |
| Taille CLI (`-s -w`) | 8-12 MB | ✅ 9 MB |
| Taille Desktop | < 15 MB | ⚠️ 17 MB (optimisation α) |

---

## Licence

MIT — Usage libre, modification, distribution, usage commercial autorisé.