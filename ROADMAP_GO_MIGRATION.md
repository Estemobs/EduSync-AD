# 🗺️ Feuille de route — Migration EduSync-AD : Python/PyQt6 → Go/Wails

> **Version** : 1.0 — 10 octobre 2026
> **Objectif** : Réécrire entièrement EduSync-AD en Go (backend + UI desktop via Wails) **sans perte de fonctionnalité**, avec une architecture testable et "IA-ready" (l'IA peut tester, détecter les erreurs et patcher).
> **Timeline** : ~14 semaines, qualité > vitesse.

---

## Table des matières

1. [Contexte et motivations](#1-contexte-et-motivations)
2. [Décisions validées](#2-décisions-validées)
3. [Architecture cible](#3-architecture-cible)
4. [Inventaire des fonctionnalités à migrer](#4-inventaire-des-fonctionnalités-à-migrer)
5. [Environnement de test AD](#5-environnement-de-test-ad)
6. [Roadmap par phases](#6-roadmap-par-phases)
7. [Stratégie de tests "IA-ready"](#7-stratégie-de-tests-ia-ready)
8. [Migration des données existantes](#8-migration-des-données-existantes)
9. [Packaging et cross-build](#9-packaging-et-cross-build)
10. [Prompts IA prêts à l'emploi](#10-prompts-ia-prêts-à-lemploi)
11. [Risques et mitigations](#11-risques-et-mitigations)
12. [Critères d'acceptation global](#12-critères-dacceptation-global)

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
| OS de dev principal | **Linux** | Cross-compile Go natif, containers Docker/Podman, Wails dev mode |
| Environnement de test AD | **Samba AD DC en container** (`testcontainers-go`) | Vrai AD (LDAP, LDAPS, Kerberos, DNS, GPO, schema) — se rapproche d'un Windows Server, démarre en 30 s, isolé, reproductible, gratuit |
| Timeline | **~14 semaines, qualité > vitesse** | Pas de deadline dure, focus robustesse + IA-ready |
| Ordre des modules | **Core v1.x d'abord** (création, migration, départs, MDP, explorateur, export, audit, simulation) → puis v4.0 (multisite, RBAC, portail, API, IA, RGPD) | Fondations solides, valeur immédiate, zéro perte fonctionnelle |
| Frontend | **HTMX 2.0 + Alpine.js + Tailwind CSS** (embedded via `//go:embed`) | Zéro build JS, HTML-first (l'IA génère du HTML), < 50 KB JS, reactif, parfait avec Wails, pas de node_modules |
| SQLite | `modernc.org/sqlite` (pure Go, **sans CGO**) | Garde le cross-compile trivial (`CGO_ENABLED=0`) |
| Licence | MIT (identique à l'existant) | Compatibilité |
| Tests d'intégration | `testcontainers-go` + image Samba AD maison | Un vrai AD dans chaque test |

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
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── modules/           # Un fichier HTML par module
│   │   └── partials/          # Composants réutilisables (table, form, tree, toast, modal)
│   └── static/
│       ├── css/tailwind.css
│       ├── js/alpine.stores.js
│       └── assets/
├── build/
│   ├── cross-build.sh         # Matrice GOOS/GOARCH + ldflags
│   ├── appimage/              # AppImage Linux
│   └── inno/                  # InnoSetup Windows (.iss)
├── .github/workflows/
│   ├── ci.yml                 # Test, lint, build multi-arch
│   ├── integration.yml        # Tests AD réels (container Samba)
│   └── release.yml            # Tag → build → artefacts → release notes
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

## 4. Inventaire des fonctionnalités à migrer

> **Règle : zéro perte fonctionnelle.** Chaque ligne ci-dessous doit exister en Go avant de déclarer la migration terminée.

### Phase Core v1.x

| Module | Fonctionnalités | Statut |
|---|---|---|
| **Connexion AD** | LDAPS par défaut (636), fallback LDAP explicite avec avertissement, certificat CA custom, pool de connexions, Kerberos/GSSAPI, auto-discovery DC | ⬜ |
| **Création de comptes / Arrivées** | Import CSV (encodage + séparateur auto), génération identifiants/MDP/mail, résolution doublons (incrément), vérification AD existants, export CSV étiquettes | ⬜ |
| **Migration (restructuration)** | Via CSV (prénom/nom/équipe) ou UI (sélection OU), déplacement AD + groupes auto, résolution par prénom/nom | ⬜ |
| **Gestion des départs** | Désactivation immédiate ou archivage + purge planifiée (délai configurable, 30 j par défaut) | ⬜ |
| **Réinitialisation MDP** | Par OU, groupe AD ou CSV, politiques différées (élèves/personnels), forçage changement à la prochaine connexion | ⬜ |
| **Explorateur AD** | Arborescence OUs/groupes, panneau central unifié, clic droit complet (modif, move, MDP, groupes, suppr), recherche temps réel | ⬜ |
| **Export (CSV / Étiquettes PDF)** | Sélection OU ± sous-OU, champs cochables (incl. MDP mémorisés), PDF Avery L7160/L7163, QR codes, couleurs pastel | ⬜ |
| **Journal d'actions** | Filtres date/type/résultat/domaine/opérateur, export CSV, SQLite append-only | ⬜ |
| **Mode simulation** | Toute écriture AD = prévisualisation modifiable avant validation | ⬜ |

### Phase v4.0 (livrée en Python)

| Module | Fonctionnalités | Statut |
|---|---|---|
| **Profils / Modèles** | Profils de config réutilisables, modèles groupes/OU | ⬜ |
| **Étiquettes avancées** | Éditeur WYSIWYG, badges/cartes, trombinoscope photo A4/A3, envoi étiquette par mail | ⬜ |
| **Multisite** | Plusieurs domaines/forêts, profils chiffrés, sélecteur de site, journal par domaine | ⬜ |
| **RBAC (délégation)** | 5 rôles, portées par OU/groupe, garde-fou sur chaque écriture AD, journal filtrable par opérateur | ⬜ |
| **Portail auto-service** | Réinit MDP par code mail, consultation identifiant/mail, demande création compte avec validation admin (serveur web stdlib) | ⬜ |
| **API REST + Webhooks** | Intégration SIS (PRONOTE), notifications (Teams, Slack, mail, SIG), sync bidirectionnelle | ⬜ |
| **IA / Assistant** | Détection anomalies (doublons, orphelins), suggestion nettoyage fin d'année, identifiants "intelligents", analyse logs → recommandations sécurité | ⬜ |
| **Conformité RGPD** | Rapport, droit à l'oubli, chiffrement exports | ⬜ |
| **Sécurité avancée** | MFA admin, session unique, granularité MDP (PSO), signature numérique exports | ⬜ |
| **Sauvegarde & restauration** | Backup ZIP, restore 1-clic, migration vN→vN+1 | ⬜ |
| **Coffre MDP** | AES-256 local (machines admin), jamais sur l'AD | ⬜ |
| **Mise à jour intégrée** | Vérification, téléchargement, installation, redémarrage auto, somme de contrôle | ⬜ |
| **Home dirs / Quotas / Logon hours / Logon scripts / Class spaces / M365 / Exchange / RDS / Advanced IO / Group templates / Label Studio** | Modules v4.x existants (voir `pyproject.toml` entry-points) | ⬜ |
| **Portails modules complémentaires** | `advanced_io`, `templates`, `rds`, `exchange`, `m365`, `class_spaces`, `logon_scripts`, `logon_hours`, `quotas`, `homedirs`, `profiles`, `label_studio` | ⬜ |

### Fonctionnalités transverses

| Fonctionnalité | Statut |
|---|---|
| Configuration chiffrée (domaine, utilisateur) via platformdirs + AES | ⬜ |
| Thème dark/light, i18n fr/en | ⬜ |
| Historique/mode démo VM (protocole de test) | ⬜ |

---

## 5. Environnement de test AD

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
Les tests d'intégration couvrent explicitement les points où Samba ≠ Windows :
- Schema étendu (attributs personnalisés).
- GPO (lecture/écriture basique).
- Fine-grained password policies (PSO).
- LDAPS + certificat CA custom.
- Kerberos/GSSAPI (keytab, SPN).
- Recherche paginée, contrôle de référence (paged results, server-side sort).
- Fallback LDAP (389) avec avertissement.

---

## 6. Roadmap par phases

### Phase 0 : Fondations (Semaines 1-2)

| Jour | Livrable | Critère de passage |
|---|---|---|
| 1-2 | **Starter kit complet** (repo, CI, Taskfile, structure, CLAUDE.md) | `task dev` lance Wails en dev, `task test` passe, `task build:all` sort les binaires multi-cibles |
| 3-4 | **Config chiffrée multi-profil** (lecture/écriture compatible Python) | Importe config Python existante, chiffre/déchiffre, switch de profil |
| 5-6 | **Core AD : pool de connexions + TLS + SASL/GSSAPI** | Connexion LDAPS + Kerberos OK, pool géré, retry/backoff, health check |
| 7-8 | **Modèles domaine + audit SQLite** | User/Group/OU/Config/AuditEntry sérialisables, migrations SQL, index |
| 9-10 | **Testutil container Samba + fixtures** | `go test -tags=integration ./internal/ad/...` passe à 100 % |
| 11-12 | **CLI squelette** (`edusync-cli connect`, `edusync-cli test-ad`) | Connexion AD réelle, liste OU/users, sortie JSON |
| 13-14 | **Wails v3 + HTMX/Alpine/Tailwind embed** | `wails dev` → login fonctionnel, thème dark/light, i18n fr/en |

**Sortie Phase 0** : base technique solide, AD de test automatisé, cycle dev < 2 s (hot reload Wails).

### Phase 1 : Modules core v1.x (Semaines 3-7)

*Méthode par module : tests d'intégration d'abord → implémentation → UI HTMX → bindings CLI → validation manuelle.*

| Semaine | Module | Points clés à tester |
|---|---|---|
| **3** | **Création de comptes** | 1500 users < 30 s, doublons gérés, politiques MDP respectées, PDF valide |
| **4** | **Migration / restructuration** | 500 users cross-OU < 60 s, groupes mis à jour, dry-run exact |
| **5** | **Gestion des départs** | 200 users archivés < 10 s, purge planifiée idempotente |
| **6** | **Réinitialisation MDP** | 1000 resets < 45 s, politiques appliquées, MDP conformes ANSSI |
| **7** | **Explorateur AD** | Navigation fluide 10k+ objets, recherche < 500 ms, actions atomiques |

**En parallèle (semaines 5-7)** : **Export CSV/PDF**, **Journal d'actions**, **Mode simulation** (transverses à tous les modules).

### Phase 2 : Modules avancés (Semaines 8-11)

| Semaine | Module | Points clés |
|---|---|---|
| **8** | **Profils / Modèles / Étiquettes avancées** | Label Studio WYSIWYG, badges/cartes, trombinoscope A4/A3, envoi mail, QR codes |
| **9** | **Multisite** | 2 domaines simultanés, sélecteur de site, journal par domaine, pools séparés |
| **10** | **RBAC** | 5 rôles, portées OU/groupe, garde-fou sur chaque écriture, journal par opérateur |
| **11** | **Portail auto-service + API REST** | Serveur stdlib, réinit par code mail, validation admin, OpenAPI, webhooks |

### Phase 3 : IA / Conformité / Finitions (Semaines 12-14)

| Semaine | Module | Points clés |
|---|---|---|
| **12** | **IA Assistant** | Détection doublons/orphelins/MDP faibles, nettoyage fin d'année, analyse logs |
| **13** | **Conformité RGPD + sécurité** | Rapport RGPD, droit à l'oubli, MFA TOTP, session unique, PSO, signature, backup/restore, migration vN→vN+1 |
| **14** | **Polish + packaging + docs** | Matrice cross-build, AppImage, InnoSetup, signatures, release notes auto, guide utilisateur, tests charge (5k users), benchmarks vs Python |

---

## 7. Stratégie de tests "IA-ready"

```
tests/
├── unit/                  # go test ./internal/... (rapide, mocks)
│   ├── ad/  config/  crypto/  csv/  label/  rbac/  multisite/
├── integration/           # go test -tags=integration ./... (container Samba)
│   ├── ad_connection_test.go
│   ├── user_crud_test.go
│   ├── migration_test.go
│   ├── password_reset_test.go
│   ├── explorer_test.go
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

**Script fourni** : `edusync-cli migrate --from-python --config-dir ... --vault-dir ...`
→ lit tout, valide, écrit au format Go, sauvegarde l'original en `.bak`.

---

## 9. Packaging et cross-build

### Une commande par cible

```bash
# Depuis Linux (OS de dev principal)
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="-s -w" -o dist/edusync-ad.exe ./cmd/edusync-ad
GOOS=linux   GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="-s -w" -o dist/edusync-ad          ./cmd/edusync-ad
GOOS=linux   GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="-s -w" -o dist/edusync-ad-arm64    ./cmd/edusync-ad
GOOS=darwin  GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="-s -w" -o dist/edusync-ad-mac      ./cmd/edusync-ad
```

- **`CGO_ENABLED=0`** → cross-compile 100 % natif, aucun outil système requis.
- **Wails** : `wails build -platform windows/amd64,linux/amd64,darwin/arm64` gère le backend Go + le bundle frontend automatiquement.

### Cibles de distribution

| Cible | Méthode | Notes |
|---|---|---|
| **Windows** | `go build` → `.exe` → **InnoSetup** (`build/inno/`) → `EduSyncAD-Setup.exe` | WebView2 présent sur Win 10/11 ; installateur peut le télécharger si absent. Signature code (certificat) optionnelle. |
| **Linux (générique)** | Binaire + `.desktop` + icône, ou `.tar.gz` | Remplace l'ancien Flatpak quand possible |
| **Linux (AppImage)** | `build/appimage/` | Bundlé WebKitGTK → un fichier unique, zéro dépendance |
| **Linux (développeurs)** | `go build` direct | Un seul fichier exécutable |
| **macOS** | `GOOS=darwin` (Wails) | Secondaire |

> **Attention WebKitGTK (Linux/Wails)** : `libwebkit2gtk-4.1` est une dépendance système. Solution principale = **AppImage** (bundle). Fallback = Flatpak léger uniquement pour WebKitGTK.

---

## 10. Prompts IA prêts à l'emploi

### Prompt 1 : nouveau module complet

```
Crée le module "X" pour EduSync-AD Go.

1. Lis internal/ad/ pour comprendre les patterns (pool, simulation, audit).
2. Écris D'ABORD les tests d'intégration dans tests/integration/x_test.go
   - Utilise testutil.NewSambaADContainer()
   - Couvre : succès, erreurs, simulation, edge cases
3. Implémente internal/x/handler.go + types dans internal/models/
   - Suit le pattern : Validate() → Simulate() → Execute() → Audit()
4. Ajoute les bindings Wails dans cmd/edusync-ad/main.go (wails.Bind).
5. Crée les templates HTMX dans web/templates/modules/x.html
   - Utilise les partials : table, form, modal, toast, tree
   - Stores Alpine pour l'état client
6. Ajoute les commandes CLI dans cmd/edusync-cli/x.go
7. Lance : go test -tags=integration ./tests/integration/... -run TestX
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
6. Tests : 2 containers Samba (EDUSYNC.LOCAL + ANNEXE.LOCAL),
   switch de site, opérations croisées.
```

*(Voir `docs/IA_PROMPTS.md` pour la bibliothèque complète.)*

---

## 11. Risques et mitigations

| Risque | Prob. | Impact | Mitigation |
|---|---|---|---|
| WebKitGTK Linux (Wails) | Moyenne | Build AppImage complexe | Script AppImage inclus, testé en CI, fallback Flatpak |
| Samba AD ≠ Windows AD | Faible | Différences schema/GPO | Tests ciblés (schema étendu, GPO, PSO, trusts), documentation des écarts |
| Kerberos/GSSAPI Linux | Moyenne | Config keytab/SPN | `gokrb5` + docs, fallback NTLM si Kerberos KO, tests des deux voies |
| Migration config/vault | Faible | Perte de données | Script testé sur configs réelles, backup auto, rollback documenté |
| Courbe Go + Wails + HTMX | Moyenne | Vitesse initiale | Starter kit complet, patterns documentés, prompts IA, pair programming |
| SQLite sans CGO (modernc) | Faible | Perf SQL | Benchmarks intégrés ; fallback CGO local si nécessaire (perte de cross-compile) |
| Dépendance WebView2/WebKit | Moyenne | Installateur Windows | WebView2 redistributable, détection + téléchargement auto |

---

## 12. Critères d'acceptation global

La migration est terminée quand **tous** ces critères sont réunis :

- [ ] **Zéro perte fonctionnelle** : chaque module de l'inventaire (§4) fonctionne en Go avec feature parity vérifiée.
- [ ] **Tests verts** : unit + integration (container Samba) + e2e (Playwright) en CI à chaque commit.
- [ ] **Perf** : démarrage < 100 ms ; import 1500 comptes < 30 s ; navigation explorateur fluide sur 10k+ objets ; RAM au repos 30-50 MB.
- [ ] **Binaire** : Windows `.exe` + Linux (+ AppImage) < 15 MB, générés par une commande.
- [ ] **Migration données** : un utilisateur Python existant passe à Go sans reconfiguration manuelle (script de migration validé).
- [ ] **Documentation** : guide utilisateur mis à jour, guide de l'AD de test, architecture, prompts IA.
- [ ] **Benchmarks** : résultats comparés à la version Python publiés (avant/après).
- [ ] **Mode simulation** : toute écriture AD passe par `Validate → Simulate → Execute → Audit`.

---

## Prochaines étapes

1. **Générer le starter kit** : structure du repo (§3), `go.mod`, `Taskfile.yml`, CI/CD, `Dockerfile.samba-ad` + `provision-ad.sh`, helpers testutil, `internal/config` + `crypto` + `models` + `audit`, squelette Wails + HTMX/Alpine/Tailwind (login + dashboard), `CLAUDE.md`/`AGENTS.md`, scripts cross-build, `docs/AD_TEST_ENV.md`.
2. **Lancer la Phase 0** (semaines 1-2) : fondations + AD de test + premier module.
3. **Attaquer la Phase 1** (semaines 3-7) : modules core v1.x, un par un, tests d'abord.
