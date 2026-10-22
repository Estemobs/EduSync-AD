# Migration Python → Go — Résumé

> **Version** : 3.0 — 22 octobre 2026
> **Objectif** : Réécrire **toutes** les fonctionnalités d'EduSync-AD en Go (backend + UI desktop Wails) **sans perte de fonctionnalité**, publier une **release alpha Windows + Linux**, puis déclencher la **campagne de tests**.
>
> **Avancement détaillé par module, métriques et prochaines actions : [`docs/STATUS.md`](../STATUS.md)** (source de vérité). Ce document décrit le plan de migration et ses critères d'acceptation.

---

## Les 3 priorités dans l'ordre (décision du 15/10/2026)

| Priorité | Objectif | État |
|----------|----------|------|
| **1. Finir le logiciel en Go** | Migration complète : Core v1.x (M1-M11) + Modules avancés (M12-M24) + Modules v4.0 (M25-M30, M32) + Fondations | ✅ **Terminée** (M31 priorité basse non porté) |
| **2. Sortir l'alpha** | Windows `EduSyncAD-Setup.exe` + Linux `AppImage` + `.tar.gz` | ✅ Pipeline CI prêt — 🔲 **En attente validation manuelle** |
| **3. Campagne de tests** | Intégration Samba, e2e Playwright, benchmarks vs Python, couverture > 90% | ⏳ Démarre post-α1 |

> **Règle** : La boucle d'hygiène `task lint && task test && task test:python` reste obligatoire à **chaque livraison** (voir `AGENTS.md`). Les phases de tests dédiées (campagne complète) sont décalées **après l'alpha**.

---

## Phases détaillées — État actuel

| Phase | Fichier | Contenu | État |
|-------|---------|---------|------|
| **0. Fondations** | `01-core.md` | Starter kit, config, crypto, modèles, audit, pool AD, CLI, UI Wails, testutil Samba | ✅ **Terminée** |
| **1. Core v1.x (M1-M11)** | `01-core.md` (suite) | Câblage bout en bout : création, migration, départs, MDP, explorateur, export, journal, paramètres, simulation | ✅ **Terminée** |
| **2. Modules avancés (M12-M24)** | `02-modules-avances.md` | Photos, profils, quotas, scripts, heures, dossiers, partages, M365, Exchange, RDS, imports avancés, modèles groupes, Label Studio, Cache T2 | ✅ **Terminée** |
| **3. Modules v4.0 (M25-M32)** | `03-modules-v4.md` | Multisite, RBAC, portail auto-service, API REST, IA, conformité, DFS, sauvegarde, coffre MDP | ✅ **Terminée** (M31 non porté, priorité basse) |
| **4. Alpha & Packaging** | `04-alpha.md` | `release-go.yml`, InnoSetup Windows, AppImage Linux, M10 auto-update, checklist pré-alpha | ✅ **CI prête** — validation manuelle en attente |
| **5. Tests post-alpha** | `05-tests.md` | Couverture unitaire, intégration Samba, e2e, benchmarks, charge, sécurité, beta → stable 1.0 | ⏳ Prêt à démarrer |

> **Détails par module** : voir `docs/modules/MXX-*.md` (une fiche par module).

---

## Critères d'acceptation (3 portes)

### Porte 1 — « Logiciel fini » (fin Priorité 1) ✅ **ATTEINTE**

- ✅ Inventaire §4 vérifié dans le code (M1→M30, M32, T2, coffre, transverses — M31 non porté)
- ✅ Chaque écriture AD passe par `Validate → Simulate → Execute → Audit`
- ✅ `gofmt`, `go vet`, `go test ./...`, `pytest -q` tous verts
- ✅ Toutes les clés i18n ajoutées existent en français **et** anglais
- ✅ `edusync-cli migrate from-python` livré (config, vault, audit, multisite, clé)
- ✅ Roadmap + `CLAUDE.md` reflètent le code

### Porte 2 — « Alpha publiée » (fin Priorité 2) 🔲 **EN ATTENTE VALIDATION**

- ✅ `release-go.yml` (tags `v0.*`) produit : `Setup.exe`, `AppImage`, `.tar.gz` + `.sha256`
- ✅ `prerelease: true`, notes de version (périmètre, limites, installation, appel testeurs)
- 🔲 Checklist P2.5 : démarrage Windows réel, install/désinstall, relecture config Python, LDAPS, thème+i18n
- ✅ `release.yml` (Python, tags `v1.*`) **intact**
- 🔲 M10 auto-update validé contre l'alpha

### Porte 3 — « Stable 1.0 » (fin Priorité 3) ⏳ **POST-ALPHA**

- [ ] Zéro perte fonctionnelle : feature parity vérifiée par les tests
- [ ] Tests verts en CI : unit + intégration (Samba) + e2e (Playwright) ; couverture > 90%
- [ ] Perf : démarrage < 100 ms ; import 1500 comptes < 30 s ; navigation fluide 10k+ objets ; RAM 30-50 MB
- [ ] Binaires Windows + Linux < 15 MB, générés en une commande
- [ ] Migration données : utilisateur Python passe à Go sans reconfiguration manuelle
- [ ] Documentation à jour (guide utilisateur, AD test, architecture, prompts IA)
- [ ] Benchmarks publiés (comparatif Python)

> Prochaines actions concrètes : voir [`docs/STATUS.md`](../STATUS.md).

---

## Commandes de référence

```bash
# Développement
task dev              # App desktop (Wails v3)
task test             # Tests Go (28 packages OK)
task test:python      # Tests Python (843 passés)
task lint             # gofmt (lecture) + go vet
task fmt              # gofmt -w
task build:all        # Cross-build → dist/ (5 cibles CLI)

# AD de test (si Docker dispo)
task ad:start         # Démarrer Samba AD (docs/AD_TEST_ENV.md)
task ad:reset         # Recréer le container et les données
task ad:shell         # Shell dans le container

# Migration données (✅ Implémentée)
edusync-cli migrate from-python [--config-dir PATH] [--vault-dir PATH] [--audit-path PATH] [--multisite-path PATH] [--dry-run] [--force] [--json]
```

---

## Contexte pour l'IA

- `CLAUDE.md` — Architecture, règles d'or, conventions, prompts réutilisables
- `AGENTS.md` — Boucle de travail : spec → test d'abord → implémentation → gofmt/vet/test → lint CI → revue → commit
- `ROADMAP_GO_MIGRATION.md` §10 — Bibliothèque de prompts (nouveau module, optimisation, multisite, etc.)

---

## Documents de référence

| Document | Rôle |
|----------|------|
| `docs/STATUS.md` | **Source de vérité** : avancement réel, métriques, releases, prochaines actions |
| `ROADMAP_GO_MIGRATION.md` | Plan de migration, inventaire fonctionnel, architecture cible, risques, prompts IA |
| `docs/modules/` | Une fiche par module : objectif, Python ref, Go à faire, avancement, validation |
| `docs/python/` | Documentation de la version Python actuelle (référence parité) |
| `docs/ARCHITECTURE.md` | Architecture Go cible (ce qui existe dans le code) |
| `docs/AD_TEST_ENV.md` | Environnement de test AD (Samba en container) |
