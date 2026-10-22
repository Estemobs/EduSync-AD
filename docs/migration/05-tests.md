# Priorité 3 — Campagne de tests (Post-Alpha)

> **Déclenchée après la release alpha** (décision 14/10/2026)
> **Source** : `ROADMAP_GO_MIGRATION.md` §7, §12 « Porte 3 »

---

## Objectif

Atteindre **Porte 3 — Stable 1.0** :
- Zéro perte fonctionnelle (feature parity Python ↔ Go vérifiée par les tests)
- Tests verts en CI : unit + intégration (container Samba) + e2e (Playwright) à chaque commit
- Couverture > 90%
- Perf : démarrage < 100 ms, import 1500 comptes < 30 s, navigation fluide 10k+ objets, RAM 30-50 MB
- Binaires Windows + Linux < 15 MB, générés en une commande
- Migration données : utilisateur Python passe à Go sans reconfiguration manuelle
- Documentation à jour, benchmarks publiés

---

## Architecture de tests

```
tests/
├── unit/                      # go test ./internal/... (rapide, mocks)
│   ├── ad/                    # pool, user, group, ou, depart, migration, password, explorer, simulation, cache
│   ├── config/
│   ├── crypto/
│   ├── csv/
│   ├── label/
│   ├── rbac/
│   ├── multisite/
│   ├── portal/
│   ├── api/
│   ├── ai/
│   ├── compliance/
│   └── backup/
├── integration/               # AD_TEST=1 go test ./... (container Samba)
│   ├── ad_connection_test.go
│   ├── user_crud_test.go
│   ├── migration_test.go
│   ├── depart_test.go
│   ├── password_reset_test.go
│   ├── explorer_test.go
│   ├── audit_test.go
│   ├── multisite_test.go
│   ├── rbac_test.go
│   ├── portal_test.go
│   ├── api_test.go
│   ├── backup_restore_test.go
│   └── migrate_python_test.go
├── e2e/                       # Playwright contre Wails (dev ou build)
│   ├── login.spec.ts
│   ├── create_accounts.spec.ts
│   ├── migration.spec.ts
│   ├── departs.spec.ts
│   ├── password_reset.spec.ts
│   ├── ad_explorer.spec.ts
│   ├── export.spec.ts
│   ├── audit.spec.ts
│   ├── settings.spec.ts
│   ├── multisite.spec.ts
│   ├── rbac.spec.ts
│   ├── portal.spec.ts
│   └── api.spec.ts
├── bench/                     # go test -bench=. -benchtime=10s
│   ├── ldap_pool_bench_test.go
│   ├── csv_import_bench_test.go
│   ├── migration_bench_test.go
│   ├── explorer_bench_test.go
│   └── startup_bench_test.go
└── fixtures/                  # CSV exemples, exports LDIF, configs
    ├── eleves_1500.csv
    ├── personnels_200.csv
    ├── migration_restruct.csv
    ├── config_python_v1.json
    ├── secret_python.key
    └── journal_python.db
```

---

## Boucle IA (Test-First)

```
1. Spec d'un module → IA génère les TESTS d'intégration d'abord
2. IA implémente jusqu'à `go test` vert
3. `task lint` (golangci-lint, staticcheck) + `task test` + `task build` en CI
4. Tests E2E Playwright sur le binaire/Wails
5. Erreurs → IA patche → boucle jusqu'à vert
6. Review humaine → merge
```

**Outillage pour l'IA** : `go vet`, `staticcheck`, `golangci-lint`, `go test -race`, benchmarks avec profils CPU/mém.

**Fichiers de contexte IA** : `CLAUDE.md` + `AGENTS.md` + prompts du `ROADMAP_GO_MIGRATION.md` §10

---

## Chantiers détaillés (P3.1 → P3.6)

### P3.1 — Couverture unitaire par module
**Cible** : > 90% sur `internal/...`

| Module | Tests unitaires requis (exemples) |
|--------|-----------------------------------|
| `internal/ad/pool` | Pool LRU, health check, retry/backoff, TLS config, SASL/GSSAPI auth |
| `internal/ad/user` | CRUD, enable/disable, move, password reset, unlock, groups |
| `internal/ad/migration` | Plan (CSV→plan), Simulate (dry-run), Execute (move+groupes), rollback partiel |
| `internal/ad/depart` | Disable, Archive, Purge (idempotent), pending_deletions table |
| `internal/ad/password` | Policies élèves/personnels, génération, history, force change |
| `internal/ad/explorer` | Search (pagination, filtres), BuildTree (cache + fallback) |
| `internal/ad/cache` (T2) | Full sync, incremental (uSNChanged), TTL invalidation, search offline |
| `internal/config` | Load/save, chiffré, migration Python→Go, validation, atomic write |
| `internal/crypto` | AES-256-GCM encrypt/decrypt, vecteur interop Python, secret.key 0600 |
| `internal/audit` | Record, Filter (date/type/résultat/domaine/opérateur), Export CSV, append-only |
| `internal/label` | CSV export, PDF Avery L7160/L7163, QR, trombinoscope, WYSIWYG template |
| `internal/rbac` | Evaluator (5 rôles, portées OU/groupe), middleware garde-fou, audit operator |
| `internal/multisite` | Site config chiffré, pool par site, switch site, journal site_id |
| `internal/portal` | Codes PBKDF2 usage unique, rate limit, SMTP, audit utilisateur="portail" |
| `internal/api` | OpenAPI conformance, webhooks HMAC, retry, SIS sync bidir |
| `internal/ai` | Anomaly detection rules, cleanup suggestions, idgen anti-homonymes, log analysis |
| `internal/compliance` | RGPD export/purge, MFA TOTP, session unique, PSO read/write, signature RFC3161 |
| `internal/backup` | ZIP chiffré (config+secret+journal+modèles), restore 1-clic, migrations vN→vN+1 |

**Règle** : Chaque test doit **échouer sans l'implémentation** (pas de test de présence de commentaire).

---

### P3.2 — Intégration container Samba (`AD_TEST=1`)

**Socle existant** : `internal/testutil/samba.go`, `Dockerfile.samba-ad`, `provision-ad.sh`, `integration.yml`

| Test | Description | Données test |
|------|-------------|--------------|
| `ad_connection_test.go` | LDAPS 636, LDAP 389+StartTLS, SASL/GSSAPI, fallback, erreurs | 2 domaines (EDUSYNC.LOCAL, ANNEXE.LOCAL) |
| `user_crud_test.go` | Create/Read/Update/Delete, enable/disable, move, groups, unlock | 50 comptes pré-existants + nouveaux |
| `migration_test.go` | Plan CSV→plan, Simulate (dry-run), Execute (move+groupes auto), rollback | CSV 5 classes, 25 élèves/classe |
| `depart_test.go` | Disable, Archive (move Archive OU), Purge (délai 30j), idempotence | 10 comptes départ |
| `password_reset_test.go` | Reset par OU/groupe/CSV, politiques élèves/personnels, force change | 20 comptes reset |
| `explorer_test.go` | Search paginée, filtres, BuildTree, cache hit/miss, 10k objets | Injection 5000 users + 100 groupes |
| `audit_test.go` | Record, Filtres combinés, Export CSV, Append-only (pas de DELETE/UPDATE) | Journal 1000 entrées |
| `multisite_test.go` | 2 containers Samba, switch site, opérations croisées, journal filtré | EDUSYNC.LOCAL + ANNEXE.LOCAL |
| `rbac_test.go` | Helpdesk hors portée → 403, Admin site plein → 200, Audit operator/role | 5 utilisateurs test, rôles variés |
| `portal_test.go` | Code mail PBKDF2, usage unique, anti-énumération, rate limit, audit | 5 demandes reset, 3 lookups |
| `api_test.go` | OpenAPI endpoints, webhooks signature HMAC, retry, SIS sync | Mock SIS endpoint |
| `backup_restore_test.go` | Backup ZIP chiffré → restore machine vierge → config/secret/journal intacts | Config Python + Go |
| `migrate_python_test.go` | `edusync-cli migrate --from-python` : lit Python, valide, écrit Go, backup .bak | Fixtures Python réelles |

**Commande CI** :
```yaml
# .github/workflows/integration.yml
name: Integration Tests
on: [push, pull_request]
jobs:
  integration:
    runs-on: ubuntu-latest
    services:
      samba:
        image: edusync-ad-samba  # built from Dockerfile.samba-ad
        ports: [389, 636, 88, 53, 445]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
      - name: Run integration tests
        run: AD_TEST=1 go test ./tests/integration/... -v -count=1
```

---

### P3.3 — E2E Playwright

**Cible** : Binaire Wails buildé (`task build`) ou mode dev (`task dev`)

| Spec | Scénario |
|------|----------|
| `login.spec.ts` | Connexion LDAPS ok, fallback LDAP avertissement, config mémorisée, erreur cert |
| `create_accounts.spec.ts` | Import CSV 50 → simulation → exécution → vérif AD + journal + étiquettes |
| `migration.spec.ts` | CSV restructuration 5 classes → plan → simulation → exécution → groupes auto |
| `departs.spec.ts` | 10 départs → désactivation → archivage → purge (dry-run) → journal |
| `password_reset.spec.ts` | Reset 20 élèves OU → simulation (MDP générés) → exécution → force change |
| `ad_explorer.spec.ts` | Navigation arborescence, recherche temps réel, clic droit actions, 5000 objets |
| `export.spec.ts` | Export CSV champs cochables, PDF étiquettes L7160/L7163, QR, couleurs |
| `audit.spec.ts` | Filtres date/type/domaine/opérateur, pagination, export CSV |
| `settings.spec.ts` | 4 onglets (comptes, MDP, apparence, délégation), persistance, thème/langue |
| `multisite.spec.ts` | Switch site header, pool par site, opérations croisées, journal filtré |
| `rbac.spec.ts` | Matrice rôles × portées, garde-fou écriture, audit par opérateur |
| `portal.spec.ts` | Reset MDP par code mail, consultation identifiant, demande création compte |
| `api.spec.ts` | OpenAPI endpoints, webhook reçu signé, sync SIS bidir |

---

### P3.4 — Benchmarks vs Python

| Benchmark | Cible Go | Référence Python (1.17.1) | Méthode |
|-----------|----------|---------------------------|---------|
| Démarrage à froid | < 100 ms | 2-5 s | `go test -bench=BenchmarkStartup -benchtime=10s` |
| Import CSV 1500 comptes | < 30 s | ~45 s | `BenchmarkCSVImport` (pool LDAP tuning, batch modify) |
| Migration 500 comptes | < 20 s | ~35 s | `BenchmarkMigration` |
| Explorateur 10k objets | < 200 ms (recherche) | ~2 s (UI freeze) | `BenchmarkExplorerSearch` (cache T2) |
| RAM au repos | 30-50 MB | 80-150 MB | `BenchmarkMemory` (pprof heap) |
| Binaire CLI | 8-12 MB | 80-200 MB (PyInstaller) | `task build:all` |
| Binaire Desktop | < 15 MB | 80-200 MB | `task build:desktop` (optimisation P2) |

**Publication** : Résultats comparés dans `docs/benchmarks/results.md` + graphiques.

---

### P3.5 — Charge + Sécurité

| Test | Description |
|------|-------------|
| **Charge 5k utilisateurs** | Import 5000 comptes CSV → migration 2000 → reset 1000 → explorateur navigation |
| **Gros CSV** | 10k lignes CSV import → simulation → exécution (mémoire, streaming) |
| **Fuzz formulaires** | `go-fuzz` sur tous handlers HTTP (validation entrées, injection, XSS) |
| **Aucun secret en clair** | `grep -r "password\|secret\|key" --include="*.go" | grep -v test | grep -v "_test"` → vide |
| **Race detector** | `go test -race ./...` vert |
| **Static analysis** | `golangci-lint run`, `staticcheck ./...` verts |

---

### P3.6 — Correctifs → Beta → Stable 1.0

| Version | Critères |
|---------|----------|
| `v0.2.0-beta.1` | Corrections issues alpha, tests P3.1-P3.5 verts, docs à jour |
| `v0.2.0-beta.x` | Itérations correctifs (max 3 betas) |
| **Porte 3 — Stable 1.0** (`v1.0.0`) | Tous critères §12 `ROADMAP_GO_MIGRATION.md` validés |

---

## CI/CD Pipeline final (après P3)

```yaml
# .github/workflows/ci.yml (final)
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
      - run: gofmt -l cmd internal web  # fail si non vide
      - run: go vet ./...
      - run: go test -race ./...        # unitaires + race
      - run: golangci-lint run
      - run: staticcheck ./...
      - run: go test ./...              # unitaires
      - run: go test -bench=. ./internal/... -benchtime=1s  # bench smoke
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: .venv/bin/pytest -q
  integration:
    needs: [go]
    runs-on: ubuntu-latest
    services: { samba: ... }
    steps:
      - run: AD_TEST=1 go test ./tests/integration/... -v
  e2e:
    needs: [go]
    runs-on: ubuntu-latest
    steps:
      - run: task build
      - run: npx playwright install --with-deps
      - run: npx playwright test tests/e2e/
  release-go:
    needs: [go, python, integration, e2e]
    if: startsWith(github.ref, 'refs/tags/v0.')
    uses: ./.github/workflows/release-go.yml
  release-python:
    needs: [go, python]
    if: startsWith(github.ref, 'refs/tags/v1.')
    uses: ./.github/workflows/release.yml  # inchangé
```

---

## Migration données Python → Go (Validation aller-retour)

**Script** : `edusync-cli migrate --from-python --config-dir ... --vault-dir ... --journal-dir ...`

**Tests aller-retour** (dans `tests/integration/migrate_python_test.go`) :
1. Générer fixtures Python réelles (`config.json`, `secret.key`, `journal.db`, modèles, templates)
2. Lancer `migrate --from-python` → produit fichiers Go
3. Lancer app Go → lire config, ouvrir vault, lire journal → **OK**
4. Modifier config depuis Go → sauvegarder
5. Lancer app Python (1.17.1) → lire config modifiée → **OK**
6. Même chose pour `secret.key` (chiffrement/déchiffrement) et `journal.db` (nouveaux enregistrements)

**Critère** : Zéro perte, zéro corruption, formats identiques.

---

## Documentation à mettre à jour (P3)

- [ ] `docs/guide_utilisateur.md` : reflète version Go (UI, raccourcis, nouvelles fonctionnalités)
- [ ] `docs/AD_TEST_ENV.md` : commandes `task ad:start/reset/shell` câblées
- [ ] `docs/ARCHITECTURE.md` : packages M12-M32, RBAC, Multisite, API, Portail, IA, Compliance
- [ ] Bibliothèque de prompts : consolidée dans `ROADMAP_GO_MIGRATION.md` §10 (nouveau module, optimisation, multisite, etc.)
- [ ] `CLAUDE.md` : état migration final, patterns modules avancés
- [ ] `ROADMAP_GO_MIGRATION.md` : statuts §4 = ✅ partout, roadmap reflète code

---

## Critères Porte 3 — Stable 1.0 (Rappel §12)

- [ ] **Zéro perte fonctionnelle** : feature parity vérifiée par les tests (P3.1-P3.3)
- [ ] **Tests verts en CI** : unit + intégration (Samba) + e2e (Playwright) à chaque commit ; couverture > 90%
- [ ] **Perf** : démarrage < 100 ms ; import 1500 comptes < 30 s ; navigation fluide 10k+ objets ; RAM 30-50 MB
- [ ] **Binaire** : Windows + Linux (+ AppImage) < 15 MB, générés en une commande
- [ ] **Migration données** : utilisateur Python existant passe à Go sans reconfiguration manuelle (aller-retour validé)
- [ ] **Documentation** : guide utilisateur, guide AD test, architecture, prompts IA à jour
- [ ] **Benchmarks** : résultats comparés à la version Python publiés (`docs/benchmarks/results.md`)

---

## Prochaine étape après Porte 3

→ **Maintenance** : versions mineures, correctifs sécurité, retours utilisateurs
→ **Nouvelles fonctionnalités** : basées sur feedback terrain (issues GitHub)