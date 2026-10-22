# 🗺️ Feuille de route — Migration EduSync-AD : Python/PyQt6 → Go/Wails

> **Version** : 3.0 — 15 octobre 2026
> **Objectif** : réécrire **toutes** les fonctionnalités d'EduSync-AD en Go
> (backend + UI desktop Wails) **sans perte de fonctionnalité**, **sortir une
> première alpha dès le Core câblé (M1 → M11)**, puis **livrer les modules
> avancés un par un, chacun avec sa propre release**.
>
> **Priorités ordonnées (décision utilisateur, 15 octobre 2026)** :
>
> 1. **Finir le Core (M1 → M11)** — câblage UI de bout en bout ; la logique
>    métier existe **déjà** dans `internal/ad/` (voir le constat §0).
> 2. **Alpha 1 dès que M11 est atteint** — Windows
>    (`EduSyncAD-Setup.exe`) + Linux (AppImage + `.tar.gz`). Ensuite :
>    **un module avancé livré = une release** (M12 → release, M13 → release,
>    … jusqu'à M32).
> 3. **Campagne de tests** (unitaires par module, intégration container
>    Samba, e2e, benchmarks vs Python) **intégrée à la boucle de chaque
>    release** — elle ne bloque plus la première alpha.
>
> Ce qui **ne change pas** : la boucle d'hygiène reste obligatoire à chaque
> livraison (`task lint && task test && task test:python`, `AGENTS.md`).
> Aucun test existant n'est supprimé ni désactivé.

---

## Table des matières

1. [État d'avancement](#0-état-davancement-mise-à-jour--15-octobre-2026)
2. [Contexte et motivations](#1-contexte-et-motivations)
3. [Décisions validées](#2-décisions-validées)
4. [Architecture cible](#3-architecture-cible)
5. [Inventaire — la référence « logiciel fini »](#4-inventaire--la-référence-logiciel-fini)
6. [Environnement de test AD](#5-environnement-de-test-ad)
7. [Roadmap — Core d'abord, puis une release par module](#6-roadmap--core-dabord-puis-une-release-par-module)
8. [Stratégie de tests « IA-ready » (priorité 3)](#7-stratégie-de-tests-ia-ready--priorité-3)
9. [Migration des données existantes](#8-migration-des-données-existantes)
10. [Packaging et cross-build](#9-packaging-et-cross-build)
11. [Prompts IA prêts à l'emploi](#10-prompts-ia-prêts-à-lemploi)
12. [Risques et mitigations](#11-risques-et-mitigations)
13. [Critères d'acceptation (3 portes)](#12-critères-dacceptation--3-portes)

---

## 0. État d'avancement (mise à jour : 15 octobre 2026)

> Statut vérifié **dans le code et les tests**, pas dans les intentions.

### Phase 0 (semaines 1-2) — ✅ livrée

| Jours | Livrable | Statut | Où |
|---|---|---|---|
| 1-2 | Starter kit : repo Go, `go.mod`, `Taskfile.yml`, CI, `CLAUDE.md`/`AGENTS.md`, cross-build | ✅ Livré | `go.mod`, `Taskfile.yml`, `.github/workflows/ci.yml`, `build/cross-build.sh` |
| 3-4 | Config chiffrée multi-profil, lecture des fichiers Python | ✅ Livré (testé) | `internal/config` (+ fixture `testdata/config_python.json` généré par Python) |
| 3-4 | Crypto compatible Python (AES-256-GCM) | ✅ Livré (testé) | `internal/crypto`, avec vecteur d'interopérabilité Python → Go |
| 5-6 | **Core AD : pool de connexions + TLS + SASL/GSSAPI** | ✅ Livré | `internal/ad/pool.go` (pool, health check, LDAPS/StartTLS, `AuthMethod` simple/NTLM/SASL-GSSAPI + client `gokrb5`) |
| 7-8 | Modèles domaine + audit SQLite | ✅ Livré (testé) | `internal/models`, `internal/audit` (schéma identique à Python, append-only) |
| 9-10 | Testutil container Samba + fixtures | ✅ Livré (hors campagne de tests) | `internal/testutil/samba.go` (testcontainers-go), `Dockerfile.samba-ad`, `provision-ad.sh`, `docs/AD_TEST_ENV.md` |
| 11-12 | CLI squelette | ✅ Livré (testé) | `cmd/edusync-cli` : `version`, `paths`, `config show`, `audit list`, `connect` (DNS + TCP + TLS) |
| 13-14 | Wails v3 + HTMX/Alpine/Tailwind embeddé | ✅ Livré (testé) | `cmd/edusync-ad`, `internal/ui`, `web/` — login, dashboard, thème clair/sombre, i18n fr/en + gabarits `users`, `password`, `migration` |

### Phase 1 (amorcée côté UI — semaine 3)

| Module | Avancement constaté | Où |
|---|---|---|
| Réinitialisation MDP | 🔨 Gabarit HTMX + routes + i18n fr/en livrés (logique AD à brancher) | `web/templates/password.html`, `internal/ui/server.go`, `internal/ui/i18n.go` |
| Migration / restructuration | 🔨 Gabarit HTMX + routes + i18n fr/en livrés (logique AD à brancher) | `web/templates/migration.html`, `internal/ui/server.go`, `internal/ui/i18n.go` |

### Priorité 1 en cours — câblage du Core (constat réel dans le code, 15/10)

| Module | Réel dans le code | Manque pour le dire « fini » |
|---|---|---|
| Login / dashboard / thème / i18n | ✅ Fonctionnel et testé | — |
| Connexion AD (M9) | ✅ **Livré et testé** (commit `86414c0`) | — (`BindPassword` câblé, repli LDAPS→LDAP + avertissement, erreurs fr/en, pool nettoyé à la déconnexion) |
| Création de comptes (M1) | ✅ **Livré et testé** (commit `f705d80`) | — (`usersSimulate`/`usersExecute` + gabarits, doublons, AD check, groupes auto, export étiquettes) |
| Réinitialisation MDP (M4) | ✅ **Livré et testé** (commit `7eacd2b`) | — (`passwordSimulate`/`passwordExecute` + gabarits, 3 modes OU/groupe/CSV, politiques élèves/perso, forçage MDP, export CSV) |
| Migration / restructuration (M2) | 🔨 Gabarit `migration.html` + routes | `migrationPlan` / `migrationSimulate` / `migrationExecute` = TODO ; 3 gabarits absents |
| Explorateur AD (M5) | 🔨 Routes + i18n | Handlers `pool.Search` / `pool.BuildTree` = TODO ; 3 gabarits absents (`explorer.html`, `explorer_results.html`, `explorer_tree.html`) |
| Export CSV/PDF (M6) | ✅ **Livré et testé** | Vrais handlers `ExportCSV` / `ExportLabelsPDF` + gabarit `export.html` |
| Journal d'actions (M7) | ✅ **Livré et testé** | `auditFilter` réel + gabarits `audit.html` + `audit_results.html` |
| Gestion des départs (M3) | ✅ **Livré et testé** | `internal/ad/depart.go`, routes, gabarit `departs.html`, CLI `departs`, i18n fr/en |
| Paramètres globaux (M8) | ✅ **Livré et testé** | 3 onglets (comptes, mots de passe, apparence), routes, gabarit `parametres.html`, i18n fr/en |
| Mode simulation (M11) | 🔨 Pattern `simulation.go` prêt | Câler `Validate → Simulate → Execute → Audit` sur tous les modules ci-dessus |
| Mise à jour intégrée (M10) | ✅ **Livrée et testée** | `internal/updater` (vérification GitHub releases, téléchargement, SHA-256, préparation installation) + routes UI `/app/updater/check` et `/app/updater/install`, i18n fr/en |

### Mesures constatées (Linux amd64 — Go 1.27.2, Wails v3.0.0-beta.29) — vérifiées le 15/10/2026

| Critère | Cible | Réel |
|---|---|---|
| `gofmt -l cmd internal web` | rien | **rien** ✅ |
| `go vet ./...` | vert | **vert** ✅ |
| Tests Go | — | **8 paquets verts** ; 3 paquets sans test (`cmd/edusync-ad`, `internal/testutil`, `web`) — `go test ./...` en **0,8 s** ✅ |
| Tests Python | vert | **843 tests passés** en **3,0 s** ✅ |
| CLI cross-compilée | toutes cibles | **5 cibles** produites en une commande (`./build/cross-build.sh cli`) |
| Taille CLI (`-s -w`) | 8-12 MB | **9,5 MB** ✅ |
| Taille app desktop (`-s -w`) | < 15 MB | **17,2 MB** ⚠️ (glue cgo/WebKit — optimisation au chantier alpha) |
| CI | vertes | `ci.yml` : job Go (fmt/vet/test/build/smoke) + job Python (pytest) |
| Release | alpha Win + Linux | ⬜ `release.yml` construit **encore la version Python** (PyInstaller + Flatpak) — à réécrire en `release-go.yml` (§6 P1) |

### Décisions d'implémentation (à conserver)

1. **Tailwind compilé puis versionné** (`web/static/css/tailwind.css`) : aucun
   build obligatoire pour un contributeur ni pour l'IA ; `task web:css`
   régénère après modification des gabarits. Source unique :
   `web/static/css/input.css` (`source(none)` → seuls `web/templates` et
   `web/static/js` sont scannés).
2. **Aucun service Wails lié au JS pour l'instant** : l'UI est intégralement
   rendue côté serveur (HTMX), ce qui supprime une couche à maintenir. Les
   bindings (`wails3 generate bindings`) arriveront avec les modules qui en
   ont réellement besoin.
3. **CLI sans cgo** : `internal/*` reste pur Go (`modernc.org/sqlite`), d'où
   la compilation croisée sur 5 cibles sans outil système.
4. **Parité Python ↔ Go exigée par les tests** : format de config JSON,
   format de chiffrement, schéma SQLite et chemins platformdirs sont couverts
   par des tests aller-retour (données produites par Python lues par Go).

---

## 1. Contexte et motivations

### Problème actuel
- Application **Python 3.11+ / PyQt6** devenue **lourde et lente** :
  - Démarrage froid 2-5 s (interpréteur + Qt + imports).
  - Binaire PyInstaller 80-200 MB.
  - RAM 80-150 MB au repos.
  - `ldap3` (pure Python) lent sur grosses OU ; GIL bloque les opérations CPU.
  - UI qui freeze sur les opérations longues (import 1500 comptes).
  - Packaging lourd (PyInstaller/Flatpak).

### Cible
- **Binaire unique 8-12 MB**, démarrage **< 100 ms**, RAM **30-50 MB**.
- **LDAP natif et concurrent** (go-ldap v3, goroutines).
- **Cross-compile en une commande** : Windows, Linux, macOS, amd64/arm64.
- **Tests en millisecondes** (pas de runtime Python à charger).
- **Workflow IA** : spec → IA génère tests → IA implémente → tests verts → review → merge.

### Pourquoi Go (et pas Rust ou autre)
| Critère | Go | Rust | Rester en Python |
|---|---|---|---|
| Perf LDAP | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| Courbe d'apprentissage | 1-2 semaines | 2-4 mois | connu |
| Démarrage binaire | < 100 ms | < 50 ms | 1-3 s |
| Taille binaire | 8-12 MB | 3-8 MB | 80-150 MB |
| Cross-compile | trivial (`GOOS`/`GOARCH`) | simple | lourd |
| Lisibilité pour IA | excellente (verbose, explicite) | moyenne (lifetimes) | bonne |
| Tests rapides | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Écosystème AD | go-ldap v3, gokrb5 | ldap3-rs | ldap3 |

---

## 2. Décisions validées

| Sujet | Décision | Rationale |
|---|---|---|
| **Ordre des priorités** | **1) câbler le Core M1→M11 → 2) alpha 1 → 3) un module = une release (M12→M32) → 4) campagne de tests intégrée** | Décision utilisateur du 15/10/2026 : sortir une alpha dès que le Core est prêt, puis livrer au fur et à mesure |
| **Périmètre de l'alpha 1** | **Core v1.x (M1 → M11) uniquement** | Décision utilisateur du 15/10/2026 : avoir une application utilisable tôt ; les modules avancés (M12 → M32) suivent, **un par release** |
| **Rythme des releases** | **Un module avancé livré = une release** (`v0.1.x` correctifs, `v0.2.0` par jalon) | Décision utilisateur du 15/10/2026 |
| **Formats alpha** | Windows **`EduSyncAD-Setup.exe`** (InnoSetup) + Linux **AppImage** + **`.tar.gz`** | Décision utilisateur du 14/10/2026 |
| Workflow de release | `release-go.yml` dédié aux tags **`v0.*`** ; `release.yml` (Python) inchangé pour les tags **`v1.*`** | Ne jamais casser le canal de release de la version Python publiée (1.17.1) |
| OS de dev principal | **Linux** | Cross-compile Go natif, containers Docker/Podman, Wails dev mode |
| Environnement de test AD | **Samba AD DC en container** (`testcontainers-go`) | Vrai AD (LDAP, LDAPS, Kerberos, DNS, GPO, schema) — se rapproche d'un Windows Server, démarre en 30 s, isolé, reproductible, gratuit |
| Timeline | **Qualité > vitesse, pas de deadline dure** | Lots livrables et vérifiables plutôt qu'une date ; la Python 1.17.1 reste en production pendant la migration |
| Ordre des modules | **Core v1.x d'abord** (création, migration, départs, MDP, explorateur, export, audit, simulation) → puis modules avancés (M12-M24) → puis v4.0 (M25-M32) | Fondations solides, valeur immédiate, zéro perte fonctionnelle |
| Frontend | **HTMX 2.0 + Alpine.js + Tailwind CSS** (embedded via `//go:embed`) | Zéro build JS, HTML-first (l'IA génère du HTML), < 50 KB JS, reactif, parfait avec Wails, pas de node_modules |
| SQLite | `modernc.org/sqlite` (pure Go, **sans CGO**) | Garde le cross-compile trivial (`CGO_ENABLED=0`) |
| Licence | MIT (identique à l'existant) | Compatibilité |
| Tests d'intégration | `testcontainers-go` + image Samba AD maison | Un vrai AD dans chaque test (socle déjà livré, campagne en priorité 3) |

---

## 3. Architecture cible

```
edusync-ad/
├── cmd/
│   ├── edusync-ad/            # App desktop (Wails v3)
│   ├── edusync-cli/           # CLI complète (scripts, CI, IA)
│   └── edusync-server/        # API REST + Webhooks (optionnel, même core)
├── internal/
│   ├── ad/                    # ⭐ Cœur métier LDAP/AD
│   │   ├── connection.go      # Pool, TLS, SASL/GSSAPI, découverte DC
│   │   ├── user.go            # CRUD, MDP, enable/disable, move, groupes
│   │   ├── group.go           # CRUD, membres, groupes imbriqués
│   │   ├── ou.go              # CRUD, arborescence, liens GPO
│   │   ├── depart.go          # Désactivation, archivage, purge planifiée
│   │   ├── migration.go       # Logique restructuration (CSV + UI)
│   │   ├── password.go        # Politiques de reset, génération, historique
│   │   ├── explorer.go        # Recherche paginée, cache, constructeur d'arbre
│   │   └── simulation.go      # Mode dry-run avec diff preview
│   ├── config/                # Multi-profil, chiffré (AES-GCM), platformdirs
│   ├── crypto/                # Vault MDP, certs, config TLS, signatures
│   ├── csv/                   # Import/export, validateurs, modèles
│   ├── models/                # Types domaine (User, Group, OU, Config, AuditEntry, Site)
│   ├── audit/                 # Journal SQLite (append-only, indexé, rétention)
│   ├── label/                 # Génération PDF (Avery L7160/L7163, QR, trombinoscope)
│   ├── multisite/             # Multi-domaine/forêt, profils chiffrés, sélecteur
│   ├── rbac/                  # 5 rôles, portées OU/groupe, garde-fous écriture
│   ├── portal/                # Self-service élève/parent (code mail, validation)
│   ├── api/                   # REST + Webhooks, spec OpenAPI
│   ├── ai/                    # Détection anomalies, suggestions, analyse logs
│   ├── compliance/            # RGPD, backup/restore, MFA, PSO, signature
│   └── testutil/              # Container Samba, fixtures, helpers
├── web/                       # Frontend embed (//go:embed)
│   ├── templates/
│   │   ├── layout.html        # Base + config HTMX + stores Alpine
│   │   ├── login.html / dashboard.html / <module>.html
│   │   └── partials/          # Composants réutilisables (table, form, tree, toast, modal)
│   └── static/
│       ├── css/tailwind.css
│       ├── js/alpine.stores.js
│       └── assets/
├── build/
│   ├── cross-build.sh         # Matrice GOOS/GOARCH + ldflags
│   ├── appimage/              # AppImage Linux
│   └── inno/                  # InnoSetup Windows (.iss, porté depuis packaging/)
├── .github/workflows/
│   ├── ci.yml                 # Test, lint, build multi-arch
│   ├── integration.yml        # Tests AD réels (container Samba) — exécution en priorité 3
│   ├── release.yml            # Tags v1.* → release Python (inchangé)
│   └── release-go.yml         # Tags v0.* → releases Go (§6 étape 2.1)
├── Taskfile.yml               # Commandes dev unifiées (task setup/dev/test/build)
├── go.mod / go.sum
├── CLAUDE.md                  # Contexte IA : patterns, règles, prompts
├── AGENTS.md                  # Instructions agents (test, implémenter, review)
├── Dockerfile.samba-ad        # Image AD de test
├── provision-ad.sh            # Provisioning domaine de test
└── docs/
    ├── ARCHITECTURE.md
    ├── AD_TEST_ENV.md
    ├── MIGRATION_GUIDE.md     # Guide de migration des données Python → Go
    └── IA_PROMPTS.md
```

### Principes d'architecture

1. **Pattern universel par opération AD** : `Validate() → Simulate() → Execute() → Audit()` — le mode simulation est natif, pas une option ajoutée après coup.
2. **Le core Go est UI-agnostic** : `cmd/edusync-ad` (Wails), `cmd/edusync-cli` (scripts) et `cmd/edusync-server` (API) partagent `internal/*`.
3. **Connexions poolées par site** (LRU, max simultanées, health check, retry/backoff).
4. **Toute écriture AD passe par RBAC** (garde-fou) + **audit** (append-only SQLite).
5. **Frontend embarqué** : `//go:embed web/` → zéro fichier externe, un seul binaire.

---

## 4. Inventaire — la référence « logiciel fini »

> **Règle : zéro perte fonctionnelle.** Chaque ligne ci-dessous doit exister
> en Go avant de déclarer la migration terminée. Le périmètre = **toutes les
> fonctionnalités de la version Python publiée** (M1 → M32, `docs/ROADMAP_python_v1.md`)
> + les fondations T0-T2. Statuts : ✅ livré / 🔨 partiellement câblé / ⬜ absent.

### 4.1 Core v1.x (M1 → M11) — Priorité 1, Lot A

| Module | Fonctionnalités | Statut Go |
|---|---|---|
| **M1 — Création de comptes / Arrivées** | Import CSV (encodage + séparateur auto), génération identifiants/MDP/mail, résolution doublons, vérification AD existants, groupes de classe auto, export CSV étiquettes | ✅ Livré et testé (A2) |
| **M2 — Migration (restructuration)** | Via CSV (prénom/nom/équipe) ou UI (sélection OU), déplacement AD + groupes auto, résolution par prénom/nom | ✅ Livré et testé (A4) |
| **M3 — Gestion des départs** | Désactivation immédiate ou archivage + purge planifiée (délai configurable, 30 j par défaut), réactivation | ✅ Livré et testé (A8) |
| **M4 — Réinitialisation MDP** | Par OU, groupe AD ou CSV, politiques différées (élèves/personnels), forçage changement à la prochaine connexion | ✅ Livré et testé (A3) |
| **M5 — Explorateur AD** | Arborescence OUs/groupes, panneau central unifié, recherche temps réel, détails utilisateur/groupe/OU | ✅ Livré et testé (A5) |
| **M6 — Export (CSV / Étiquettes PDF)** | Sélection OU ± sous-OU, champs cochables, PDF Avery L7160/L7163, QR codes | ✅ Livré et testé (A6) |
| **M7 — Journal d'actions** | Filtres date/type/résultat/domaine/opérateur, export CSV, SQLite append-only | ✅ Livré et testé (A7) |
| **M8 — Paramètres globaux** | 3 onglets : Comptes, Mots de passe, Apparence (délégation reportée en Lot C M26) | ✅ Livré et testé (A9) |
| **M9 — Connexion LDAPS/LDAP** | LDAPS prioritaire, repli LDAP avec avertissement, pool de connexions, nettoyage à la déconnexion | ✅ Livré et testé (A1) |
| **M10 — Mise à jour intégrée** | Vérification releases GitHub, téléchargement, vérification SHA-256, préparation installation | ✅ Livré et testé (A10) |
| **M11 — Mode simulation + progression** | Toute écriture AD par Validate → Simulate → Execute → Audit, progression + annulation (context.Context) | ✅ Livré et testé (A11, A12) |

### 4.2 Modules avancés (M12 → M24) — Priorité 2, Lot B

| Module | Fonctionnalités | Statut Go |
|---|---|---|
| **M12 — Photos d'identité** | Import manuel/masse/nommage auto, cadrage 3:4 ≤ 200 Ko, vignettes explorateur + étiquettes | ✅ Livré et testé (B6) |
| **M13 — Profils utilisateurs** | Itinérant/local/obligatoire par OU ou groupe, variables `%USERNAME%` `%SAM%` `%OU%` | ✅ Livré et testé (B1) |
| **M14 — Quotas de disque (FSRM)** | Quota par OU/groupe, application auto, rapport CSV | ✅ Livré et testé (B4) |
| **M15 — Scripts logon/logoff** | Éditeur bat/powershell/vbs, variables, déploiement `scriptPath`/GPO | ✅ Livré et testé (B3) |
| **M16 — Heures de connexion** | Grille 24h × 7j, préréglages, attribut `logonHours` | ✅ Livré et testé (B2) |
| **M17 — Dossiers personnels** | Création auto `\\srv\homes\%USERNAME%`, droits NTFS, lettre lecteur | ✅ Livré et testé (B5) |
| **M18 — Espaces partagés par classe** | Partage SMB + NTFS à la création du groupe de classe, sync membres ↔ droits | ✅ Livré et testé (B7) |
| **M19 — Microsoft 365 / Entra ID** | App registration, users cloud + licences, groupes/Teams, photos | ✅ Livré et testé (B12) — `internal/graph/graph.go` (client Microsoft Graph : OAuth2 client_credentials secret **ou** certificat JWT RS256, jeton mis en cache, pagination `@odata.nextLink`, erreurs traduites dont 429, persistance `m365.json` avec secret chiffré AES-GCM partagé avec Python) + `internal/m365/m365.go` (plans de comptes cloud + licences éducation, plans de groupes Entra/Teams, mapping photos, check-list identité hybride) |
| **M20 — Exchange** | Boîtes aux lettres, aliases/proxy, policies, quotas | ✅ Livré et testé (B11) — `internal/exchange/exchange.go` : configuration on-prem/online (quotas, archive, rétention), plans de boîtes et de groupes de messagerie, normalisation/alias, scripts PowerShell sur site et Exchange Online (app-only), politique d'adresses, écriture LDAP `mail`/`proxyAddresses`, rapport CSV, persistance `exchange.json` compatible Python |
| **M21 — RDS / Bureau à distance** | Collections, RemoteApp, UPD/FSLogix | ✅ Livré et testé (B10) — `internal/rds/rds.go` : configuration (broker, domaine, profils), plans de collections et RemoteApp, génération des scripts Remote PowerShell (collections idempotentes + UPD, RemoteApp avec publication par groupe, FSLogix registre), persistance `rds.json`/`rds_plans.json` compatible Python |
| **M22 — Import/Export avancés** | Import LDAP (OU/filtre), export LDIF/vCard, publipostage HTML, import GEP | ✅ Livré et testé (B9) — `internal/ad/advanced_io.go` : import LDAP (OU/groupe/filtre validé), export CSV/LDIF (RFC 2849, base64+pliage)/vCard 3.0, publipostage HTML + index, import GEP (mapping auto + conversion), outil tiers |
| **M23 — Modèles de groupes** | Modèles classe/personnel/services, instanciation 1-clic, duplication/import | ✅ Livré et testé (B14) — `internal/grouptemplates/` : `ModeleGroupe`/`SpecGroupe` (motifs `{champ}` validés, 4 modèles intégrés classe élève/prof + personnel admin + service technique), validation (`ValiderValeurs`, portées, script, quota, MDP, licences), persistance `group_templates.json` au format Python, duplication (`IDModeleUnique`), export/import JSON **et** XML typé (base64 pour les caractères de contrôle → CRLF préservé), instanciation idempotente via l'interface `Annuaire` (OU + groupes créés avant toute écriture, adaptateur `PoolAnnuaire`), configs scopées profil/quota/dossier perso (`profile_configs.json`, `quota_configs.json`, `home_dir_config.json` partagés avec Python), artefacts (script de connexion `.bat` sans BOM, espaces `espaces_classes.ps1`, quotas FSRM `quotas_fsr.ps1`, rapport JSON horodaté) et notes d'application différée ; CLI `group-templates list/show/validate/duplicate/remove/export/import/builtins/plan`, page UI `/app/group-templates`, i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens (`internal/ad.GroupCreateParams.Scope` additif pour les portées de groupe) |
| **M24 — Étiquettes avancées (Label Studio)** | Éditeur WYSIWYG, badges/cartes, trombinoscope photo A4/A3, envoi par mail | ✅ Livré et testé (B13) — `internal/label/label.go` (planches Avery L7160/L7163 + badge 85 × 55 + carte 85 × 54, planches validées, éléments texte/statique/photo/QR positionnés en mm, 4 modèles intégrés, persistance `label_templates.json` au format Python, rendu PDF gofpdf avec réduction/troncature du texte, photo AD ajustée 3:4 ou emplacement réservé, QR code coloré, trombinoscope A4/A3) + `internal/mailer/mailer.go` (SMTP STARTTLS/SSL, `label_mail.json` avec mot de passe chiffré AES-256-GCM, pièce jointe PDF, envoyeur injectable) ; CLI `label templates/show/render/trombinoscope/export/import/remove/mail-config/mail-set/send`, page UI `/app/label`, i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens |

### 4.3 Modules v4.0 (M25 → M32) — Priorité 2, Lot C

| Module | Fonctionnalités | Statut Go |
|---|---|---|
| **M25 — Multisite** | Plusieurs domaines/forêts, `domaines.json` chiffré, sélecteur, journal par domaine | ✅ Livré et testé (C1) — `internal/multisite/multisite.go` (`domaines.json` chiffré AES-256-GCM, permissions 0600, profils `ProfilDomaine` validés/normalisés, migration de `connection.local.json` M9, jamais bloquant) + `audit.Domains()` (liste des domaines du journal pour la sélection multi-site) ; CLI `multisite list/add/remove/import-legacy/ensure`, page UI `/app/multisite`, i18n fr/en, aller-retour Go ↔ Python vérifié |
| **M26 — RBAC (délégation)** | 5 rôles, portées par OU/groupe, garde-fou sur chaque écriture AD, journal par opérateur | ✅ Livré et testé (C2) — `internal/rbac/rbac.go` (5 rôles + matrice de permissions, délégués `delegations.json` en clair, portées par OU/groupe avec sous-arbre, résolution opérateur→rôle : aucun délégué = accès complet, opérateur inconnu = lecture seule, cumul de délégués = rôle le plus large + union des portées) ; garde-fou `Pool.SetPolicy`/`verifier` sur chaque écriture AD (création OU/groupe/compte, reset MDP, activation, déplacement, appartenance, suppression) ; `audit.Operators()` ; CLI `rbac roles/list/add/remove/policy`, page UI `/app/rbac`, i18n fr/en, aller-retour Go ↔ Python vérifié |
| **M27 — Portail auto-service** | Réinit MDP par code mail, consultation identifiant/mail, demande création compte + validation admin | ✅ Livré et testé (C3) — `internal/portal/` (configuration `portail.json` au format Python, base `portail.db` au schéma identique : `codes` + `demandes`, code à 6 chiffres **jamais stocké en clair** (PBKDF2-HMAC-SHA256 60 000 itérations, sel 16 octets, ticket 12 caractères), usage unique, expiration, plafond de tentatives et de 3 envois par identifiant sur 15 min, réponse générique anti-énumération, recherche par identifiant **ou** adresse, consultation d'identifiant, pré-inscription d'élève/personnel avec validation par un administrateur (identifiant unique suffixé, mot de passe conforme à la politique du profil, notification SMTP, écriture dans le coffre local), journal d'actions `utilisateur="portail"` ; serveur HTTP stdlib autonome (`Server: EduSyncPortail`, corps limité à 16 Kio, protection CSRF par méthode, échappement HTML systématique) ; aide à la saisie de mot de passe (`internal/config/passwords.go` : `CleanToken`, `RendreMotifMotDePasse`, `GenererMotDePasse`, `ValiderMotDePasse` — messages identiques à Python) ; `ad.EchapperRDN` partagé et échappement du RDN à la création de compte ; CLI `portal config/requests/status/refuse/approve`, page UI `/app/portal` (+ config/demarrer/arreter/approuver/refuser/etat), gabarit `portal.html`, i18n fr/en, aller-retour Go ↔ Python vérifié (`portail.json`, `portail.db`, mot de passe généré) |
| **M28 — API REST + Webhooks** | OpenAPI, webhooks (compte créé/migré/départ/MDP), sync bidirectionnelle SIS | ✅ Livré et testé (C4) — `internal/api/` (configuration `api.json` au format Python : `actif`/`hote`/`port`/`cors_origins`/`rate_limit_rpm`/`titre`, `api_keys.json` compatible — `id`/`name`/`key_hash`/`prefix`/`permissions`/`created_at`/`expires_at`/`last_used_at`/`active`, seule l'empreinte SHA-256 stockée, clé `edusync_` + 32 caractères, préfixe de 12 ; 15 permissions identiques à `STANDARD_PERMISSIONS` ; routeur stdlib à motifs `{param}` enregistré **avec et sans** préfixe `/api/v1` comme `api_routes.py` ; enveloppes `{"success":true,"data":…}` / `{"success":false,"error":{code,message,details}}` ; pagination `page`/`page_size` (défaut 50, plafond 500) ; corps limité à 1 Mio ; en-têtes de sécurité et CORS ; limitation de débit **réellement appliquée** par clé (60/min par défaut, `Retry-After`, `X-RateLimit-*`) ; routes utilisateurs/groupes/OU (via `Annuaire` + adaptateur `AnnuaireAD` sur le pool AD, filtres LDAP échappés), portail (demandes/approve/refuse), journal d'actions, `/health`, `/info`, `/openapi.json` **public**) + `internal/webhooks/` (24 événements au format Python, `webhooks.json` 0600, table `deliveries` au schéma identique — `webhook_deliveries.db` ; signature HMAC-SHA256 `sha256=<hex>` avec en-têtes `X-Webhook-Event`/`X-Webhook-Delivery`/`X-Webhook-Signature`, en-têtes personnalisés prioritaires ; réessais `exponential`/`fixed`/`none` avec reprise au démarrage ; secrets **chiffrés** AES-256-GCM avec `secret.key` partagé, relecture tolérante d'un secret en clair écrit par Python) ; CLI `api config/keys/routes/spec/webhooks/serve`, page UI `/app/api` (+ config/demarrer/arreter/etat/cles/webhooks), gabarit `api.html`, i18n fr/en, aller-retour Go ↔ Python vérifié (`api.json`, `api_keys.json`, `webhooks.json`, `webhook_deliveries.db`) |
| **M29 — IA / Assistant** | Détection anomalies, nettoyage fin d'année, identifiants intelligents, analyse logs | ✅ Livré et testé (C5) — `internal/config/identifiers.go` : **moteur d'identifiants** réellement porté de `core/identifiers.py` (19 préréglages + variantes camelCase, `{P}`/`{N}`/`{pN}`/`{nN}`/`{AN}`, règles prénom composé, 6 règles de doublons — suffixe numérique, suffixe/préfixe lettres, `NomPrenom`, `TRONCATURE`, plafond sAMAccountName 20 caractères, candidats successifs avec ou sans troncature), `StripAccents`/`CleanToken`/`RenderTemplate`/`MoteurIdentifiants().GenererUnique()` ; **parité Python corrigée** au passage (les préréglages étaient rendus littéralement, `002` devient `2`, `ResolveDuplicate` utilise `-`) ; `internal/assistant/` (analyse en **lecture seule** sur un `Inventaire` injectable : `DetecterAnomalies` — identifiants en double, comptes orphelins (OU disparue, conteneurs par défaut ignorés), OU vides, groupes sans membre ; `SuggererNettoyage` — archivage des comptes désactivés, suppression des groupes/OU vides, replacement des orphelins, **aucune suppression automatique** ; `AnalyserSecurite` — politiques MDP élèves/personnels, rôle RBAC et portées vides, score /100 ; `AnalyserJournal` — comptage par type et opérateur, échecs, suppressions, activité hors horaires 7 h→20 h) + `CollecteurAD` (parcours récursif des OU, erreurs contextuelles) ; CLI `assistant anomalies/cleanup/smart-id/security/journal` ; page UI `/app/assistant` (+ identifiant/anomalies/cleanup/securite/journal), gabarit `assistant.html`, i18n fr/en ; parité du moteur d'identifiants vérifiée sur **5280 cas** (préréglages + modèles libres × prénoms × noms × règles de doublons × jeux d'identifiants existants) **identiques octet pour octet** à la version Python |
| **M30 — Conformité & sécurité** | Rapport RGPD + droit à l'oubli, MFA admin (TOTP + codes de secours), session unique, PSO (analyse élèves/personnels + script PowerShell), signature exports (HMAC-SHA256) | ✅ Livré et testé — `internal/compliance/` (`config.go` : `conformite.json` + validation, `totp.go` : RFC 6238 HMAC-SHA1 30s 6 chiffres ±1, `mfa.go` : `VerifierMFAAdmin` + `CleSeche` + `VerifierSessionUnique` + `AnalyserGranulariteMDP`/`ScriptPSO` + `SignerExport`/`VerifierExport` HMAC-SHA256 canonique, `rgpd.go` : `GenererRapportRGPD`/`ChercherDroitOubli`/`ExporterRapportRGPDCSV` — lecture seule, append-only respecté) + CLI `compliance mfa/secret/session/pso/sign/verify/rgpd` + page UI `/app/compliance` (`compliance.html`, HTMX) + i18n fr/en (`compliance.fr.json`/`compliance.en.json`) + aller-retour Go ↔ Python vérifié pour le format JSON/crypto/schéma SQLite partagés |
| **M31 — DFS / IIS / WebDAV / FTP** | Espaces DFS, publication WebDAV/IIS, FTP isolé (priorité basse) | ⬜ |
| **M32 — Sauvegarde / Restauration** | Backup ZIP chiffré, restore 1-clic, migration vN→vN+1 | ⬜ |
| **Coffre MDP** | AES-256 local (machines admin), jamais sur l'AD | ✅ Livré et testé (B13) — `internal/vault/vault.go` : coffre SQLite local `password_vault.sqlite3` (schéma et chiffrement AES-256-GCM identiques à Python, relu dans les deux sens), stockage des mots de passe *positionnés par l'application* uniquement, recherche insensible à la casse, suppression/purge, permissions 0600 ; CLI `vault set/get/has/delete/list/count/clear`, page UI `/app/vault` |

### 4.4 Fondations Python (T0-T2) — équivalents Go

| Fondation Python | Équivalent Go | Décision |
|---|---|---|
| **T0 — couche LDAP asynchrone** | Goroutines + `context.Context` (natif) | Pas de chantier de portage **mais** : barre de progression, annulation et rapports d'erreur à câbler dans l'UI pour les opérations de masse (Lot A) |
| **T1 — système de plugins** (`entry_points`) | Modules `internal/*` câblés, activation par configuration | **Hot-reload hors périmètre** : écart documenté dans les notes alpha ; revue en priorité 3 si un vrai besoin de chargement à chaud émerge |
| **T2 — cache AD SQLite** | Cache OU/groupes/users, invalidation TTL + `uSNChanged` | ✅ Livré et testé (B8) — `internal/ad/cache.go` : fichier `ad_cache.sqlite` au schéma **identique à Python**, sync complète/incrémentale `uSNChanged`, TTL, intégration explorateur (`GetSubTree`) |

### 4.5 Transverses

| Fonctionnalité | Statut Go |
|---|---|
| Configuration chiffrée (domaine, utilisateur) via platformdirs + AES | ✅ |
| Thème dark/light, i18n fr/en | ✅ (toutes les clés des nouveaux modules en **français ET anglais**) |
| Migration des données Python (`edusync-cli migrate --from-python`) | ⬜ script à livrer en fin de Priorité 1 (§8) |
| Historique / mode démo VM (protocole de test) | ⬜ |

---

## 5. Environnement de test AD

> Socle **déjà livré** (Phase 0) mais **exploitation en priorité 3**. Il sert
> aussi de banc de test **manuel** ponctuel pendant les priorités 1 et 2
> (une connexion LDAPS réelle de bout en bout avant de publier l'alpha).

### Choix : Samba AD DC en container

On se rapproche d'un **Windows Server AD** avec un vrai Active Directory Samba 4 (LDAPS, Kerberos, DNS, GPO, schema étendu) provisionné automatiquement.

**Fourni dans le starter kit :**

1. **`Dockerfile.samba-ad`** — Ubuntu 24.04 + Samba AD + Winbind + Kerberos + BIND9 + ldap-utils.
2. **`provision-ad.sh`** — Au premier démarrage :
   - Provisionne le domaine `EDUSYNC.LOCAL` (realm identique).
   - Compte `Administrator` / `TestPass123`.
   - Structure OU : `Eleves`, `Personnels`, `Groupes`, `Services`, `Archive`.
   - ~50 utilisateurs de test (élèves + personnels, classes, accents français).
   - Groupes de test (classes, services, délégations RBAC).
   - GPO basiques + fine-grained password policies.
   - Certificat auto-signé pour LDAPS (port 636).
   - Démarre les services, loggue "Samba AD DC ready".

3. **`internal/testutil/samba.go`** — Helper Go :
   ```go
   func NewSambaADContainer(ctx context.Context, t *testing.T) (testcontainers.Container, *ADConfig)
   ```
   - Lève le container, expose 389/636/88/53, attend le log "ready".
   - Retourne un `*ADConfig` (domaine, controller, LDAPS, BaseDN, creds).
   - Arrêt + nettoyage automatique à la fin du test (`t.Cleanup`).

4. **`docs/AD_TEST_ENV.md`** — Comment lancer l'AD de test :
   - `task ad:start` (démarrage manuel pour test manuel/UI).
   - `task ad:reset` (reset propre du domaine).
   - `task ad:shell` (ldapsearch / samba-tool dans le container).
   - Ports mappés, identifiants, troubleshooting.

5. **Deux domaines** pour les tests multisite : `EDUSYNC.LOCAL` + `ANNEXE.LOCAL` (containers multiples).

### Validation "proche Windows Server"
Les tests d'intégration (priorité 3) couvrent explicitement les points où Samba ≠ Windows :
- Schema étendu (attributs personnalisés).
- GPO (lecture/écriture basique).
- Fine-grained password policies (PSO).
- LDAPS + certificat CA custom.
- Kerberos/GSSAPI (keytab, SPN).
- Recherche paginée, contrôle de référence (paged results, server-side sort).
- Fallback LDAP (389) avec avertissement.

---

## 6. Roadmap — Core d'abord, puis une release par module

> **Stratégie arrêtée le 15 octobre 2026** : **on sort une première alpha dès
> que le Core (M1 → M11) est câblé**, puis **chaque module avancé livré
> (M12 → M32) donne lieu à sa propre release**.
>
> - Chaque livraison passe `task lint && task test && task test:python`
>   (boucle d'hygiène, non négociable — `AGENTS.md`).
> - La **campagne de tests** (intégration Samba, e2e, benchmarks) accompagne
>   les releases ; elle ne bloque plus la première alpha.
> - La **version Python 1.17.1 reste en production** pendant toute la
>   migration : zéro rupture pour les utilisateurs.

### Priorité 1 — Finir le Core (M1 → M11)

#### Lot A — Câblage de bout en bout du Core (M1 → M11)

*Méthode par module (cérémonie allégée, décision du 15/10/2026) : backend
(souvent **déjà écrit**, cf. §0) → handlers HTTP réels → gabarit HTMX →
clés i18n fr/en → `task lint && task test` verts → commit. Les commandes CLI
(A11) et la revue parity (A13) sont regroupées en fin de lot.*

| # | Module | Travail réellement à faire (constat §0) |
|---|---|---|
| ~~A1~~ | ~~Connexion AD (M9)~~ | ✅ **Livré** (commit `86414c0`) : `BindPassword` câblé, repli LDAPS→LDAP + avertissement, erreurs fr/en |
| A2 | Création de comptes (M1) | Implémenter `usersSimulate` / `usersExecute` (TODO) + 2 gabarits, doublons, export étiquettes depuis la page |
| A3 | Réinitialisation MDP (M4) | `passwordSimulate` / `passwordExecute` + 2 gabarits, politiques élèves/personnels, forçage changement |
| A4 | Migration (M2) | `migrationPlan` (depuis CSV), `migrationSimulate`, `migrationExecute` + 3 gabarits, groupes auto, dry-run exact |
| A5 | Explorateur (M5) | 3 gabarits (`explorer.html`, `explorer_results.html`, `explorer_tree.html`), `pool.Search`, `pool.BuildTree` (TODO) |
| A6 | Export (M6) | Gabarit `export.html`, vrais `ExportCSV` / `ExportLabelsPDF` (stubs actuels), sélection OU ± sous-OU, champs cochables |
| A7 | Journal (M7) | Gabarits `audit.html` / `audit_results.html`, filtrage réel (`auditFilter` TODO), export CSV |
| A8 | **Départs (M3)** | **Module à créer** : `internal/ad/depart.go` (désactivation / archivage + purge planifiée idempotente), routes, gabarit, CLI, i18n (`models.DepartRow` existe) |
| A9 | **Paramètres (M8)** | **Page à créer** : 4 onglets (comptes, mots de passe, apparence, délégation reportée en Lot C), routes, gabarit, i18n |
| A10 | Simulation (M11) | Généraliser `Validate → Simulate → Execute → Audit` sur A2-A8 : toute écriture AD passe par une prévisualisation |
| A11 | CLI Core | Commandes JSON de chaque module (import, reset, migration, départs, explorer, export, audit) |
| A12 | Progression / annulation | Barre de progression + `context.Context` annulable sur les opérations de masse (équivalent T0, §4.4) |
| A13 | Revue parity Core | Checklist §4.1 = ✅ vérifiée **dans le code**, i18n fr+en sur toutes les clés ajoutées |

**Sortie du Lot A = Alpha 1** : depuis le login jusqu'au journal, chaque
module Core fonctionne de bout en bout contre l'AD de test (`task ad:start`,
vérification **manuellement**, pas une campagne). **C'est la première alpha
publiée** — la chaîne de release est décrite ci-dessous.

### Priorité 2 — Chaîne de release : Alpha 1, puis un module = une release

> **Format validé** : Windows **`EduSyncAD-Setup.exe`** (InnoSetup) ;
> Linux **AppImage** + **`.tar.gz`**. Chaque artefact avec son `.sha256`.
> Le squelette `release-go.yml` est posé **une fois, avec l'alpha 1**, puis
> réutilisé pour chaque module livré.

#### Étape 2.1 — Poser la chaîne de release (avec l'alpha 1)

| # | Travail | Détail |
|---|---|---|
| P2.1 | **Nouveau workflow `release-go.yml`** | Déclenché sur les tags **`v0.*`**, `prerelease: true`. `release.yml` (Python, PyInstaller + Flatpak) reste en place pour les tags `v1.*` — **ne pas le modifier** |
| P2.2 | Job **Windows** | Runner `windows-latest` (Go + WebView2 + Inno Setup 6 déjà présents) → `wails build` → portage de `packaging/installer.iss` vers `build/inno/` (cible le binaire Go, icônes `assets/`) → `EduSyncAD-Setup.exe` + `.sha256` |
| P2.3 | Job **Linux** | Runner `ubuntu-latest` + `libwebkit2gtk-4.1-dev` → `build/appimage/` (WebKitGTK bundlé) → `EduSyncAD-linux.AppImage` + `edusync-ad-linux-amd64.tar.gz` + `.sha256` |
| P2.4 | **M10 — mise à jour intégrée** | L'updater Go vérifie le `.sha256` de la release, télécharge, installe, redémarre (modèle = `core/updater.py`) — validé contre la première alpha |
| P2.5 | **Checklist pré-alpha** (vérifications **manuelles**, pas une campagne) | ① `task lint && task test && task test:python` verts ② app démarre sous Xvfb (smoke `ci.yml`) ③ app **démarre sur Windows 10/11** réel ④ installation + désinstallation propres ⑤ relecture d'une config Python existante sans reconfiguration ⑥ connexion LDAPS réelle contre `task ad:start` ⑦ thème + i18n fr/en sur les modules livrés |
| P2.6 | **Notes de version alpha** | Périmètre couvert (§4), limites connues, installation (Setup.exe / `chmod +x` AppImage), appel à testeurs, avertissement « logiciel en cours de migration, la 1.17.1 reste la version de production » |
| P2.7 | **Publication Alpha 1** | Tag **`v0.1.0-alpha.1`** → `release-go.yml` → 3 artefacts + 3 `.sha256` en release **pré-release** GitHub |

**Sortie de l'étape 2.1** : une alpha téléchargeable et installable sur
**Windows** et **Linux** (Core M1 → M11), avec notes de version et sommes de
contrôle.

#### Étape 2.2 — Livrer les modules avancés, un par release

> **Un module livré = une release.** Dès qu'un module passe la boucle
> d'hygiène, on incrémente la version, on pose le tag `v0.*` et
> `release-go.yml` produit les artefacts + `.sha256`. La numérotation
> ci-dessous est **indicative** (l'ordre de livraison peut être ajusté), le
> principe « un module = une release » ne l'est pas.

##### Lot B — Modules avancés (M12 → M24) + cache (T2) — releases `v0.2.0` → `v0.15.0`

| Release | Module | Points clés |
|---|---|---|
| v0.2.0 | M13 Profils utilisateurs | Itinérant/local/obligatoire par OU ou groupe, variables `%USERNAME%` `%SAM%` `%OU%` |
| v0.3.0 | M16 Heures de connexion | Grille 24h × 7j, préréglages, attribut `logonHours` |
| v0.4.0 | M15 Scripts logon/logoff | Éditeur bat/powershell/vbs, variables, déploiement `scriptPath`/GPO |
| v0.5.0 | M17 Dossiers personnels | Création auto `\\srv\homes\%USERNAME%`, droits NTFS, lettre lecteur |
| v0.6.0 | M14 Quotas de disque (FSRM) | Quota par OU/groupe, application auto, rapport CSV — prérequis serveur Windows à documenter |
| v0.7.0 | M18 Espaces partagés par classe | Partage SMB + NTFS à la création du groupe de classe, sync membres ↔ droits |
| v0.8.0 | T2 Cache AD | Cache SQLite OU/groupes/users, invalidation TTL + `uSNChanged` — fluidité explorateur |
| v0.9.0 | M12 Photos d'identité | Import masse + cadrage, vignettes explorateur, intégration étiquettes |
| v0.10.0 | M22 Import/Export avancés | Import LDAP, export LDIF/vCard, publipostage HTML |
| v0.11.0 | M19 Microsoft 365 / Entra ID | Graph + on-prem/online — tests manuels documentés (pas d'AD de test cloud) |
| v0.12.0 | M20 Exchange | Boîtes aux lettres, aliases/proxy, policies, quotas |
| v0.13.0 | M21 RDS / Bureau à distance | Collections, RemoteApp, UPD/FSLogix |
| v0.14.0 | M23 Modèles de groupes | Instanciation 1-clic, duplication/import |
| v0.15.0 | M24 Étiquettes avancées (Label Studio) | Éditeur WYSIWYG, badges/cartes, trombinoscope A4/A3, envoi par mail |

##### Lot C — Modules v4.0 (M25 → M32) + coffre — releases `v0.16.0` → `v0.24.0`

| Release | Module | Points clés |
|---|---|---|
| v0.16.0 | M25 Multisite | Modèle `Site`, config chiffrée par site, sélecteur header, pool par site, colonne `site_id` au journal |
| v0.17.0 | M26 RBAC (délégation) | 5 rôles, portées OU/groupe, **garde-fou sur chaque écriture AD**, journal par opérateur |
| v0.18.0 | Coffre MDP | AES-256 local (machines admin), jamais sur l'AD |
| v0.19.0 | M32 Sauvegarde / Restauration | Backup ZIP chiffré, restore 1-clic, migrations vN→vN+1 |
| v0.20.0 | M27 Portail auto-service | Serveur web stdlib, code mail, PBKDF2 usage unique, RBAC, audit `utilisateur="portail"` |
| v0.21.0 | M28 API REST + Webhooks | OpenAPI, webhooks SIS, sync bidirectionnelle |
| v0.22.0 | M30 Conformité & sécurité | RGPD + droit à l'oubli, MFA admin (TOTP + codes secours), session unique, PSO, signature exports (HMAC-SHA256) |
| v0.23.0 | M31 DFS / IIS / WebDAV / FTP | Priorité basse, livré en dernier |
| v0.24.0 | M32 Sauvegarde / Restauration | Backup ZIP chiffré, restore 1-clic, migrations vN→vN+1 |

#### Clôture de la Priorité 2 — « logiciel fini » (parité §4, §12)

1. **`edusync-cli migrate --from-python`** : lit config/coffre/journal
   Python, valide, écrit au format Go, sauvegarde l'original en `.bak` (§8).
2. Checklist **§4 complète = ✅** vérifiée dans le code (M1 → M32, T2,
   transverses) — pas dans les intentions.
3. `gofmt -l`, `go vet`, `go test ./...`, `.venv/bin/pytest -q` tous verts.
4. Roadmap + `CLAUDE.md` reflètent le code.

### Priorité 3 — Campagne de tests (intégrée à partir de l'alpha)

> Le socle existe déjà : `internal/testutil/samba.go`, `tests/integration/`,
> workflow `integration.yml`, smoke `ci.yml`. Détail §7. La campagne
> **accompagne les releases** (elle démarre avec l'alpha 1) au lieu d'être
> un chantier final bloquant.

| # | Chantier | Contenu |
|---|---|---|
| P3.1 | Couverture unitaire par module | Un test **qui échouerait sans l'implémentation** par fonctionnalité de §4 (M1 → M32) ; cible > 90 % — **au fil des releases** |
| P3.2 | Intégration container Samba | `AD_TEST=1` + `integration.yml` : connexion, CRUD users, migration, reset MDP, explorateur, journal, multisite (2 containers), RBAC, départs/purge |
| P3.3 | E2E Playwright | Contre l'app Wails : login → chaque module → simulation → exécution → audit |
| P3.4 | Benchmarks vs Python | Import 1500 comptes, démarrage, RAM, navigation 10k objets — avant/après publiés |
| P3.5 | Charge + sécurité | 5k utilisateurs, gros CSV, fuzz des formulaires, vérif « aucun secret en clair » |
| P3.6 | Consolidation → stable | Correctifs issus des tests/retours → `v1.0.0-beta.x` → **Porte 3** = version stable 1.0 (§12) |

---

## 7. Stratégie de tests « IA-ready » — priorité 3

> **Démarre avec l'alpha 1 et s'intensifie à chaque release** (stratégie du
> 15 oct. 2026). À chaque livraison, la boucle d'hygiène reste obligatoire
> (`task lint && task test && task test:python`) ; les chantiers ci-dessous
> montent en puissance au fil des releases, sans bloquer la première alpha.

```
tests/
├── unit/                  # go test ./internal/... (rapide, mocks)
│   ├── ad/  config/  crypto/  csv/  label/  rbac/  multisite/
├── integration/           # AD_TEST=1 go test ./... (container Samba)
│   ├── ad_connection_test.go
│   ├── user_crud_test.go
│   ├── migration_test.go
│   ├── depart_test.go
│   ├── password_reset_test.go
│   ├── explorer_test.go
│   ├── audit_test.go
│   ├── multisite_test.go
│   └── rbac_test.go
├── e2e/                   # Playwright contre Wails (dev ou build)
│   ├── login.spec.ts  create_accounts.spec.ts  migration.spec.ts  ad_explorer.spec.ts
├── bench/                 # go test -bench=. -benchtime=10s
│   ├── ldap_pool_bench_test.go  csv_import_bench_test.go  migration_bench_test.go
└── fixtures/              # CSV exemples, exports LDIF, configs
    ├── eleves_1500.csv  personnels_200.csv  migration_restruct.csv
```

### Boucle IA (loop)

1. Tu donnes la spec d'un module → l'IA génère **les tests d'intégration d'abord**.
2. L'IA implémente jusqu'à ce que `go test` soit vert.
3. `task lint` (golangci-lint, staticcheck) + `task test` + `task build` en CI.
4. Tests E2E Playwright sur le binaire/Wails.
5. Erreurs → l'IA patche → boucle jusqu'à vert.
6. Review humaine → merge.

**Outillage pour l'IA :** `go vet`, `staticcheck`, `golangci-lint`, `go test -race`, benchmarks avec profils CPU/mém. Code Go verbose et explicite = lisible et corrigeable par l'IA.

**Fichiers de contexte IA** : `CLAUDE.md` + `AGENTS.md` (patterns, règles, prompts réutilisables).

---

## 8. Migration des données existantes

> **Zéro perte** : les utilisateurs Python existants doivent pouvoir passer à Go sans réconfiguration.

| Donnée Python | Stratégie Go | Compatibilité |
|---|---|---|
| Config chiffrée (platformdirs + AES) | Même platformdirs, même algo (AES-GCM), clé dérivée PBKDF2 | Lecture directe → réécriture Go au premier save |
| Coffre MDP (AES-256) | Même format, `golang.org/x/crypto` | Lecture directe |
| Journal audit SQLite | Même schéma append-only, même fichier | Réutilisation directe (ALTER TABLE si nouvelles colonnes) |
| Profils / Modèles | JSON/YAML → mêmes structs Go | Import transparent |
| Templates étiquettes | Fichiers compatibles | Migration 1:1 |

- **Script fourni** : `edusync-cli migrate --from-python --config-dir ... --vault-dir ...`
  → lit tout, valide, écrit au format Go, sauvegarde l'original en `.bak`.
  **Livraison** : fin de la Priorité 2 (clôture §6). **Validation aller-retour
  détaillée** (les fichiers Go relus par Python et inversement) : priorité 3.
- Déjà acquis : les tests `internal/config`, `internal/crypto`,
  `internal/audit` lisent des fixtures produites par Python.

---

## 9. Packaging et cross-build

### Une commande par cible

```bash
# Depuis Linux (OS de dev principal)
./build/cross-build.sh cli       # 5 cibles CLI, CGO_ENABLED=0
./build/cross-build.sh desktop   # détecte webkit2gtk / mingw, signale ce qui manque
```

- **CLI 100 % pur Go** (`CGO_ENABLED=0`) → cross-compile partout, sans outil système.
- **Desktop = cgo obligatoire** : Linux exige `libwebkit2gtk-4.1-dev`, Windows
  `mingw-w64` **ou** un runner natif Windows (solution retenue pour
  l'alpha, cf. `release-go.yml`). `cross-build.sh` **signale explicitement**
  les cibles non produites (jamais de « succès » silencieux).

### Cibles de distribution

| Cible | Méthode | Échéance |
|---|---|---|
| **Windows** | Runner `windows-latest` → `wails build` → **InnoSetup** (`build/inno/`) → `EduSyncAD-Setup.exe` + `.sha256` | **Alpha 1 (P2.1)** |
| **Linux (AppImage)** | Runner ubuntu + webkit2gtk → `build/appimage/` → `EduSyncAD-linux.AppImage` + `.sha256` | **Alpha 1 (P2.1)** |
| **Linux (binaire)** | `edusync-ad-linux-amd64.tar.gz` (binaire + `.desktop` + icône) | **Alpha 1 (P2.1)** |
| **Linux (développeurs)** | `go build` direct | ✅ disponible |
| **macOS** | `GOOS=darwin` (nécessite un runner macOS) | Hors alpha (secondaire) |

> **Attention WebKitGTK (Linux/Wails)** : `libwebkit2gtk-4.1` est une
> dépendance système. Solution retenue = **AppImage** (bundle). Fallback =
> Flatpak léger uniquement pour WebKitGTK.
>
> **Taille desktop** : 17,2 MB aujourd'hui (cible < 15 MB) — optimisation
> (`-trimpath`, stripping du glue Wails) au chantier P2.

---

## 10. Prompts IA prêts à l'emploi

> **Pendant la Priorité 1** : les étapes « tests d'intégration d'abord » et
> `AD_TEST=1` deviennent « tests unitaires du paquet
> d'abord » (`internal/<x>/<x>_test.go`) — la forme intégration complète
> s'applique en priorité 3.

### Prompt 1 : nouveau module complet

```
Crée le module "X" pour EduSync-AD Go.

1. Lis internal/ad/ pour comprendre les patterns (pool, simulation, audit).
2. Écris D'ABORD les tests : internal/<x>/<x>_test.go + internal/ui/<page>_test.go
   (en priorité 3 : aussi tests/integration/x_test.go avec
   testutil.NewSambaADContainer()).
3. Implémente internal/<x>/handler.go + types dans internal/models/
   - Suit le pattern : Validate() → Simulate() → Execute() → Audit()
4. Crée les templates HTMX dans web/templates/<x>.html
   - Utilise les partials : table, form, modal, toast, tree
   - Stores Alpine pour l'état client
5. Ajoute les routes UI dans internal/ui/server.go + clés i18n fr/en.
6. Ajoute les commandes CLI dans cmd/edusync-cli/ (sortie JSON).
7. Lance : gofmt, go vet, go test ./... — tout vert.
8. Si échec → analyse, patch, relance. Si succès → commit.
```

### Prompt 2 : optimisation perf

```
L'import CSV de 1500 users prend 45 s. Profile avec
`go test -bench=BenchmarkCSVImport -cpuprofile=cpu.prof`.
Identifie les 3 goulots principaux. Propose des patches : tuning du pool
LDAP, modify LDAP en batch, prepared statements SQLite.
Implémente, re-benchmark, valide < 20 s.
```

### Prompt 3 : feature complexe (multisite)

```
Ajoute le support multisite (multi-domaine/forêt).
1. Modèle Site{Name, Domain, Controller, CredsEncrypted, BaseDN, TLSConfig}.
2. Config chiffrée par site (clé dérivée de la master key).
3. Sélecteur de site dans le header (Alpine store currentSite).
4. Pool de connexions par site (LRU, max 5 simultanées).
5. Journal audit : colonne site_id, filtre UI.
6. Tests (priorité 3) : 2 containers Samba (EDUSYNC.LOCAL + ANNEXE.LOCAL),
   switch de site, opérations croisées.
```

*(Voir `docs/IA_PROMPTS.md` pour la bibliothèque complète.)*

---

## 11. Risques et mitigations

| Risque | Prob. | Impact | Mitigation |
|---|---|---|---|
| **Périmètre complet avant alpha** (M12-M32 = 20 modules) | Faible | Alpha très retardée | **Écarté par la stratégie v3.0** : l'alpha 1 ne couvre que le Core (M1→M11) ; les modules avancés suivent, **un par release** (§6) |
| **Volume du portage** (20 modules avancés) | Haute | Dilution de la qualité | Un module = une release (backend → UI → i18n fr/en → lint/test verts) ; prompts IA ; Python 1.17.1 reste en production |
| **`release.yml` Python cassé par les tags Go** | Moyenne | Perte du canal release Python | Workflows **séparés** : `release.yml` (`v1.*`) intact, `release-go.yml` (`v0.*`) ; validation sur un tag de test pré-alpha |
| WebKitGTK Linux (Wails) | Moyenne | Build AppImage complexe | Runner ubuntu avec `libwebkit2gtk-4.1-dev`, script AppImage maison, test d'installation manuel (P2.5) |
| Dépendance WebView2 (Windows) | Faible | App non démarable | WebView2 présent sur Win 10/11 ; l'installateur InnoSetup détecte et télécharge le redistribuable si absent |
| Samba AD ≠ Windows AD | Faible | Différences schema/GPO | Tests ciblés (schema étendu, GPO, PSO, trusts) en priorité 3, documentation des écarts |
| Kerberos/GSSAPI Linux | Moyenne | Config keytab/SPN | `gokrb5` + docs, fallback NTLM si Kerberos KO, tests des deux voies en P3 |
| Migration config/vault | Faible | Perte de données | Script testé sur configs réelles (déjà : fixtures Python lues par Go), backup auto `.bak`, rollback documenté |
| SQLite sans CGO (modernc) | Faible | Perf SQL | Benchmarks en priorité 3 ; fallback CGO local si nécessaire (perte de cross-compile) |
| Taille desktop 17,2 MB | Faible | Cible 15 MB dépassée | Optimisation au chantier alpha (§6 étape 2.1, §9) |
| Courbe Go + Wails + HTMX | Moyenne | Vitesse initiale | Starter kit complet, patterns documentés, prompts IA, pair programming |

---

## 12. Critères d'acceptation — 3 portes

### Porte 1 — « Alpha 1 publiée » (fin de la Priorité 1 : Core M1 → M11)

- [ ] Core **M1 → M11** câblé de bout en bout (UI), vérifié manuellement
      contre l'AD de test (`task ad:start`).
- [ ] `release-go.yml` déclenché par un tag `v0.1.0-alpha.1` produit :
      `EduSyncAD-Setup.exe`, `EduSyncAD-linux.AppImage`,
      `edusync-ad-linux-amd64.tar.gz` + leurs `.sha256`.
- [ ] `prerelease: true`, notes de version (périmètre, limites connues,
      installation, appel à testeurs).
- [ ] Checklist P2.5 passée (démarrage Windows réel, install/désinstall,
      relecture config Python, LDAPS contre l'AD de test, thème + i18n).
- [ ] `release.yml` (Python, tags `v1.*`) **intact et vérifié**.
- [ ] M10 (mise à jour intégrée) valide contre la release alpha.
- [ ] `gofmt -l cmd internal web` vide ; `go vet ./...` vert ;
      `go test ./...` vert ; `.venv/bin/pytest -q` vert ;
      toutes les clés i18n ajoutées en français **et** en anglais.

### Porte 2 — « Parité fonctionnelle » (fin de la Priorité 2 : M12 → M32)

- [ ] **Inventaire §4 complet = ✅** vérifié dans le code : M1 → M32,
      fondations T2, coffre, transverses — feature parity avec la Python 1.17.1.
- [ ] **Chaque module livré a sa release** (tag `v0.*` → artefacts +
      `.sha256`, §6 étape 2.2).
- [ ] Chaque écriture AD passe par `Validate → Simulate → Execute → Audit`.
- [ ] `edusync-cli migrate --from-python` livré (§8).
- [ ] `gofmt`/`go vet`/`go test`/`pytest` verts ; i18n fr+en.
- [ ] Roadmap + `CLAUDE.md` reflètent le code.

### Porte 3 — « Stable 1.0 » (fin de la Priorité 3)

- [ ] **Zéro perte fonctionnelle** : feature parity vérifiée par les tests.
- [ ] **Tests verts en CI** : unit + intégration (container Samba) + e2e
      (Playwright) à chaque commit ; couverture > 90 %.
- [ ] **Perf** : démarrage < 100 ms ; import 1500 comptes < 30 s ; navigation
      explorateur fluide sur 10k+ objets ; RAM au repos 30-50 MB.
- [ ] **Binaire** : Windows + Linux (+ AppImage) < 15 MB, générés en une
      commande.
- [ ] **Migration données** : un utilisateur Python existant passe à Go sans
      reconfiguration manuelle (aller-retour Python ↔ Go validé).
- [ ] **Documentation** : guide utilisateur, guide AD de test, architecture,
      prompts IA à jour.
- [ ] **Benchmarks** : résultats comparés à la version Python publiés.

---

## Prochaines étapes

**Priorité 1 — finir le Core (M1 → M11) :**

1. **Lot A, A1** : ✅ fait (commit `86414c0`) — Connexion AD (M9).
2. **Lot A, A2** : ✅ fait (commit `f705d80`) — Création comptes (M1).
3. **Lot A, A3** : ✅ fait (commit `7eacd2b`) — Réinitialisation MDP (M4).
4. **Lot A, A4** : ✅ fait (commit `a369b82`) — Migration / restructuration (M2).
5. **Lot A, A5** : ✅ fait (commit `4f96344`) — Explorateur AD (M5).
6. **Lot A, A6** : ✅ fait — Export CSV/PDF (M6) : vrais handlers `ExportCSV`/`ExportLabelsPDF` + gabarit `export.html`.
7. **Lot A, A7** : ✅ fait — Journal (M7) : `auditFilter` réel + gabarits `audit.html`/`audit_results.html`.
8. **Lot A, A8** : ✅ fait — Départs (M3) : `internal/ad/depart.go` + routes + gabarit `departs.html` + CLI `departs` + i18n fr/en.
9. **Lot A, A9** : ✅ fait — Paramètres (M8) : 3 onglets (comptes, mots de passe, apparence) + routes + gabarit `parametres.html` + i18n fr/en.
10. **Lot A, A10** : ✅ fait — Mise à jour intégrée (M10) : `internal/updater` + routes UI + i18n fr/en.
11. **Lot A, A11** : ✅ fait — CLI Core : `users import-simulate`, `password simulate`, `migration plan`, `export fields` (sortie JSON) + tests unitaires.
12. **Lot A, A12** : ✅ fait — Progression/annulation : `internal/progress` (tracker thread-safe, contexte annulable par opération), routes `/app/progress`, `/app/progress/list`, `/app/progress/cancel`, gabarit `progress.html`, intégration dans `usersExecute` avec `context.Context`.
13. **Lot A, A13** : ✅ fait — Revue parity §4.1 : tous les modules M1 → M11 sont ✅ livrés et testés dans le code.

**Lot A terminé : le Core M1 → M11 est câblé de bout en bout.**
**Étape suivante : tag `v0.1.0-alpha.1` → `release-go.yml` → Alpha 1 (Porte 1).**

**Lot B — Premier module avancé (M13 → release v0.2.0) :**

14. **Lot B, B1** : ✅ fait — M13 Profils utilisateurs : `internal/ad/profiles.go` (itinérant/local/obligatoire, variables %USERNAME%/%SAM%/%OU%), routes UI `/app/profils`, gabarit `profils.html`, CLI `profils simulate/execute`, i18n fr/en, tests unitaires.
15. **Lot B, B2** : ✅ fait — M16 Heures de connexion : `internal/ad/logon_hours.go` (grille 7×24, attribut logonHours 21 octets, préréglages cours/admin/personnalisé), routes UI `/app/logon-hours`, gabarit `logon_hours.html`, CLI `logon-hours simulate/execute`, i18n fr/en, tests unitaires.
16. **Lot B, B3** : ✅ fait — M15 Scripts logon/logoff : `internal/ad/logon_scripts.go` (bat/powershell/vbs, variables %USERNAME%/%FULLNAME%/%OU%/%GROUP%/%EMAIL%/%HOMEDIR%, attribut scriptPath), routes UI `/app/logon-scripts`, gabarit `logon_scripts.html`, CLI `logon-scripts simulate/execute`, i18n fr/en, tests unitaires.
17. **Lot B, B4** : ✅ fait — M14 Quotas de disque : `internal/ad/quota.go` (grille quota, attribut msDS-QuotaAmount, seuils alerte/blocage, formatage taille), routes UI `/app/quotas`, gabarit `quotas.html`, CLI `quotas simulate/execute`, i18n fr/en, tests unitaires.
18. **Lot B, B5** : ✅ fait — M17 Dossiers personnels : `internal/ad/homedirs.go` (chemin UNC \\srv\homes\%USERNAME%, lettre de lecteur, attributs homeDirectory/homeDrive), routes UI `/app/homedirs`, gabarit `homedirs.html`, CLI `homedirs simulate/execute`, i18n fr/en, tests unitaires.
19. **Lot B, B6** : ✅ fait — M12 Photos d'identité : `internal/ad/photos.go` (attributs thumbnailPhoto/jpegPhoto, lecture du fichier disque, recadrage 3:4 centré + redimensionnement + ré-encodage JPEG, limite 200 Ko, import en masse `<sAMAccountName>.jpg`), routes UI `/app/photos`, gabarit `photos.html`, CLI `photos simulate/execute`, i18n fr/en, tests unitaires.
20. **Lot B, B7** : ✅ fait — M18 Espaces partagés par classe : `internal/classspaces/classspaces.go` (configuration partagée `\\srv\classes\%GROUP%`, classement profs/élèves par groupe ou OU, plan + diff de synchronisation, génération des scripts PowerShell création/partage SMB + droits NTFS et synchro membres AD ↔ droits, persistance `class_spaces.json`/`class_spaces_state.json` au format Python, hook `NotifierCreationGroupe`), routes UI `/app/class-spaces` (+ config/plan/creation-script/sync-script), gabarit `class_spaces.html`, CLI `class-spaces plan/creation-script/sync-script/notify/config`, i18n fr/en, tests unitaires.
21. **Lot B, B8** : ✅ fait — T2 Cache AD SQLite : `internal/ad/cache.go` (schéma identique à Python `ad_cache.sqlite`, sync complète + incrémentale `uSNChanged`, TTL, méta `last_sync`/`last_usn_changed`, requêtes OUs/groupes/utilisateurs/recherche, intégration `GetSubTree`), routes UI `/app/ad-cache` (+ sync/search/ous), gabarit `ad_cache.html`, CLI `ad-cache stats/ous/groups/users/search/sync`, i18n fr/en, tests unitaires.
22. **Lot B, B9** : ✅ fait — M22 Import/Export avancés : `internal/ad/advanced_io.go` (import LDAP depuis OU/groupe/filtre avec validation de filtre, export CSV/LDIF RFC 2849/vCard 3.0, publipostage HTML + index, import GEP avec association automatique des colonnes et conversion au format d'import EduSync, configuration + lancement de l'outil tiers), routes UI `/app/advanced-io` (+ validate-filter/source/import/gep-mapping/config), gabarit `advanced_io.html`, CLI `advanced-io validate-filter/source/import/export/merge-html/gep/config/run-tool`, i18n fr/en, tests unitaires.
23. **Lot B, B10** : ✅ fait — M21 RDS / Bureau à distance : `internal/rds/rds.go` (configuration broker/domaine/profils avec validation, plans de collections et RemoteApp, génération des scripts Remote PowerShell : collections idempotentes + UPD par collection, publication RemoteApp par groupe AD, registre FSLogix ; persistance `rds.json`/`rds_plans.json` au format Python), CLI `rds config/collection-script/remoteapp-script/fslogix-script/plans`, routes UI `/app/rds` (+ config/collection-script/remoteapp-script/fslogix-script), gabarit `rds.html`, i18n fr/en, tests unitaires.
24. **Lot B, B11** : ✅ fait — M20 Exchange : `internal/exchange/exchange.go` (configuration on-prem/online avec validation, plans de boîtes et de groupes de messagerie, normalisation des adresses et alias, scripts PowerShell sur site et Exchange Online app-only, politique d'adresses, écriture LDAP `mail`/`proxyAddresses` via `Pool.UserSetAttributes`, rapport CSV, persistance `exchange.json` au format Python), CLI `exchange config/plans/onprem-script/online-script/policy-script/report`, routes UI `/app/exchange` (+ config/plans/onprem-script/online-script/policy-script), gabarit `exchange.html`, i18n fr/en, tests unitaires.
25. **Lot B, B12** : ✅ fait — M19 Microsoft 365 / Entra ID : `internal/graph/graph.go` (client Microsoft Graph OAuth2 client_credentials avec secret ou certificat JWT RS256, jeton en cache, pagination, erreurs Graph traduites, secret chiffré AES-GCM dans `m365.json`) + `internal/m365/m365.go` (plans de comptes cloud + licences éducation, plans de groupes Entra/Teams, mapping des photos d'identité, check-list identité hybride PHS/ADFS/PTA), CLI `m365 config/user-plans/group-plans/photo-mapping/hybrid`, routes UI `/app/m365` (+ config/user-plans/group-plans), gabarit `m365.html`, i18n fr/en, tests unitaires.
26. **Coffre MDP** : ✅ fait — coffre-fort local des mots de passe positionnés par l'application : `internal/vault/vault.go` (SQLite `password_vault.sqlite3`, AES-256-GCM, schéma et clé partagés avec Python — aller-retour vérifié dans les deux sens), CLI `vault set/get/has/delete/list/count/clear`, page UI `/app/vault`, i18n fr/en, tests unitaires.
27. **Lot C, C1** : ✅ fait — M25 Multisite : `internal/multisite/multisite.go` (`domaines.json` chiffré AES-256-GCM, permissions 0600, validation/normalisation des profils, migration de la connexion mémorisée M9, chargement jamais bloquant) et `audit.Domains()` (domaines du journal par site) ; CLI `multisite list/add/remove/import-legacy/ensure`, page UI `/app/multisite`, i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens.
28. **Lot C, C2** : ✅ fait — M26 RBAC (délégation) : `internal/rbac/rbac.go` (5 rôles + matrice de permissions, délégués `delegations.json` au format Python, portées par OU/groupe avec sous-arbre, résolution opérateur→rôle : aucun délégué = accès complet, opérateur inconnu = lecture seule, cumul = rôle le plus large + union des portées) ; garde-fou `Pool.SetPolicy`/`verifier` sur chaque écriture AD et `audit.Operators()` ; CLI `rbac roles/list/add/remove/policy`, page UI `/app/rbac`, i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens.
29. **Lot B, B13** : ✅ fait — M24 Étiquettes avancées (Label Studio) : `internal/label/label.go` (planches Avery + badge/carte, éléments positionnés en mm, 4 modèles intégrés, `label_templates.json` au format Python, rendu PDF étiquettes et trombinoscope A4/A3 avec photos/QR/emplacements réservés) + `internal/mailer/mailer.go` (SMTP, `label_mail.json` chiffré) ; CLI `label …`, page UI `/app/label`, i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens.
30. **Lot B, B14** : ✅ fait — M23 Modèles de groupes : `internal/grouptemplates/` (`ModeleGroupe`/`SpecGroupe` + motifs `{champ}`, 4 modèles intégrés, validation, `group_templates.json` au format Python, duplication, export/import JSON **et** XML typé avec base64 pour les caractères de contrôle → CRLF préservé, instanciation idempotente via l'interface `Annuaire` + adaptateur `PoolAnnuaire` (OU et groupes créés une seule fois, résumé/rapport horodaté), configs scopées partagées `profile_configs.json`/`quota_configs.json`/`home_dir_config.json`, artefacts script de connexion `.bat` sans BOM + espaces `.ps1` + quotas FSRM + rapport JSON, notes d'application différée des champs non appliqués) ; `internal/ad.GroupCreateParams.Scope` additif (`global`/`domainlocal`/`universal`) ; CLI `group-templates list/show/validate/duplicate/remove/export/import/builtins/plan`, page UI `/app/group-templates` (+ apercu/instancier/dupliquer/supprimer/export), i18n fr/en, aller-retour Go ↔ Python vérifié dans les deux sens (JSON, XML, configs scopées).
31. **Lot C, C3** : ✅ fait — M27 Portail auto-service : `internal/portal/` (configuration et base SQLite au format Python, code de vérification à 6 chiffres haché en PBKDF2-HMAC-SHA256 60 000 itérations, à usage unique et expire, plafonds d'essais et d'envois, recherche par identifiant ou adresse avec réponse générique contre l'énumération, réinitialisation de mot de passe, consultation d'identifiant, pré-inscription validée par un administrateur avec identifiant unique et notification SMTP) + `portal.PoolAnnuaire` (adaptateur du pool AD) + aide à la saisie des mots de passe dans `internal/config/passwords.go` (`CleanToken`, `RendreMotifMotDePasse`, `GenererMotDePasse`, `ValiderMotDePasse`, messages identiques à `core/passwords.py`) ; `ad.EchapperRDN` (`internal/ad/dn.go`) et échappement du RDN à la création de compte ; CLI `portal config/requests/status/refuse/approve`, page UI `/app/portal` (+ config/demarrer/arreter/approuver/refuser/etat), gabarit `portal.html`, i18n fr/en, aller-retour Go ↔ Python vérifié (`portail.json`, `portail.db`, mot de passe généré).

32. **Lot C, C4** : ✅ fait — M28 API REST et webhooks : `internal/api/` (configuration `api.json`, clés `api_keys.json` — seule l'empreinte SHA-256 stockée, 15 permissions identiques à Python, routeur stdlib à motifs `{param}` enregistré avec et sans préfixe `/api/v1`, enveloppes `success`/`data`/`error`, pagination, corps limité à 1 Mio, en-têtes de sécurité, CORS, limitation de débit **réellement appliquée** par clé, routes utilisateurs/groupes/OU via un adaptateur du pool AD, portail, journal, `/health`, `/info`, `/openapi.json` **public**) + `internal/webhooks/` (24 événements, `webhooks.json` 0600, table `deliveries` au schéma Python, signature HMAC-SHA256, réessais `exponential`/`fixed`/`none`, secrets chiffrés AES-256-GCM avec `secret.key` partagé) ; CLI `api config/keys/routes/spec/webhooks/serve`, page UI `/app/api`, gabarit `api.html`, i18n fr/en, aller-retour Go ↔ Python vérifié (`api.json`, `api_keys.json`, `webhooks.json`, `webhook_deliveries.db`).

33. **Lot C, C5** : ✅ fait — M29 IA / Assistant : moteur d'identifiants réellement porté de `core/identifiers.py` dans `internal/config/identifiers.go` (19 préréglages + camelCase, variables `{P}`/`{N}`/`{pN}`/`{nN}`/`{AN}`, règles prénom composé, 6 règles de doublons avec candidats successifs, plafond sAMAccountName 20 caractères, `StripAccents`/`CleanToken`/`RenderTemplate`/`GenererUnique`) ; **bugs de parité corrigés** (préréglages rendus littéralement, suffixe `002` → `2`, séparateur `.` → `-` dans `ResolveDuplicate`), appels UI/CLI/portail basculés sur le moteur ; `internal/assistant/` (analyses **en lecture seule** sur un inventaire injectable : anomalies — identifiants en double, comptes orphelins, OU vides, groupes sans membre ; nettoyage — archivage/suppression/replacement **suggérés**, jamais automatiques ; sécurité — politiques MDP, rôle RBAC, score /100 ; journal — comptage par type/opérateur, échecs, suppressions, hors horaires) + `CollecteurAD` (OU parcourues récursivement) ; CLI `assistant anomalies/cleanup/smart-id/security/journal`, page UI `/app/assistant`, gabarit `assistant.html`, i18n fr/en ; parité du moteur vérifiée sur **5280 cas identiques octet pour octet** à Python.

34. **Lot C, C6** : ✅ fait — M30 Conformité & sécurité : `internal/compliance/` (`config.go` : `conformite.json` Go-only sans équivalent Python + validation, `totp.go` : RFC 6238 HMAC-SHA1 30s 6 chiffres ±1, `mfa.go` : `VerifierMFAAdmin` (statut complete/partial/none + détails + recommandations), `GenererCleSeche` AES-256-GCM avec `secret.key`, `VerifierSessionUnique` (détection doublons par opérateur+domaine), `AnalyserGranulariteMDP` (score /100, 2 PSO élèves/personnels, script PowerShell idempotent), `SignerExport`/`VerifierExport` HMAC-SHA256 sur JSON canonique — **correction du bug Python** qui incluait l'horodatage dans la signature) + `rgpd.go` : `GenererRapportRGPD` (inventaire comptes/journal/portail/coffre, sections statiques, droit à l'oubli `ChercherDroitOubli` par source, recommandations automatiques, export CSV 0600) ; CLI `compliance mfa/secret/session/pso/sign/verify/rgpd` ; page UI `/app/compliance` (`compliance.html` HTMX : config MFA/session/signature, vérification MFA, initialisation MFA (secret + 10 codes), rapport RGPD, analyse PSO) ; i18n fr/en (`compliance.fr.json`/`compliance.en.json`) ; tests unitaires 100% (`go test ./internal/compliance/` + `cmd/edusync-cli` tests compliance) ; parité Go ↔ Python pour JSON/crypto/schéma SQLite.

**Priorité 2 — releases :**

6. **Étape 2.1** : `release-go.yml` (tags `v0.*`) + `build/inno/` +
   `build/appimage/` ; M10 auto-update ; checklist P2.5 ; tag
   **`v0.1.0-alpha.1`** → **Porte 1 (Alpha 1)**.
7. **Étape 2.2** : livrer **M12 → M32 un par un**, chaque module = **une
   release** (`v0.2.0` → `v0.24.0`) → **Porte 2 (parité)**.

**Priorité 3 — campagne de tests (accompagne les releases) :**

8. Intégration Samba (`AD_TEST=1`), e2e Playwright, couverture par module,
   benchmarks vs Python, charge.
9. Consolidation → `v1.0.0-beta.x` → **Porte 3** = stable 1.0.

---

> **Historique**
> - 12 octobre 2026 — Phase 0 livrée : starter kit, config, crypto, modèles,
>   audit, CLI, UI.
> - 13 octobre 2026 — gabarits UI « reset MDP » et « migration » (routes +
>   i18n fr/en) : amorçage de la Phase 1 côté interface.
> - 14 octobre 2026 — pool AD (TLS/SASL-GSSAPI), `internal/testutil`
>   (testcontainers Samba) et le workflow `integration.yml` livrés ;
>   **v2.0 de la feuille de route** : réécriture selon la décision
>   utilisateur **logiciel complet d'abord → alpha Windows + Linux →
>   campagne de tests ensuite** ; constats de code du câblage Core intégrés
>   au §0, périmètre alpha étendu à la migration complète (§4).
> - 15 octobre 2026 — **A1 (M9) livré** (commit `86414c0`) : `BindPassword`
>   câblé, repli LDAPS→LDAP + avertissement, erreurs fr/en. **v3.0 de la
>   feuille de route** : nouvelle stratégie **Core M1→M11 d'abord → alpha 1
>   → un module avancé = une release (M12→M32)** ; campagne de tests intégrée
>   aux releases ; mesures §0 rafraîchies (gofmt/vet/tests Go + 843 tests
>   Python verts).
