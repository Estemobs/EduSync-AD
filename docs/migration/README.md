# Migration Python → Go — Résumé

> **Version** : 3.0 — 22 octobre 2026
> **Objectif** : Réécrire **toutes** les fonctionnalités d'EduSync-AD en Go (backend + UI desktop Wails) **sans perte de fonctionnalité**, publier une **release alpha Windows + Linux**, puis déclencher la **campagne de tests**.

---

## Les 3 priorités dans l'ordre (décision du 15/10/2026)

| Priorité | Objectif | État |
|----------|----------|------|
| **1. Finir le logiciel en Go** | Migration complète : Core v1.x (M1-M11) + Modules avancés (M12-M24) + Modules v4.0 (M25-M30, M32) + Fondations | ✅ **Terminée** (M31 priorité basse non porté, code + tests verts) |
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

## État d'implémentation des modules

### Lot A — Core v1.x (M1-M11) — **TOUS FINIS**

| # | Module | Travail principal | Commit / État |
|---|--------|-------------------|---------------|
| A1 | Connexion AD (M9) | `BindPassword` depuis config, erreurs UI, avertissement repli LDAP | ✅ `86414c0` |
| A2 | Création comptes (M1) | `usersSimulate`/`Execute` + 2 gabarits, doublons, export étiquettes | ✅ `f705d80` |
| A3 | Réinit MDP (M4) | `passwordSimulate`/`Execute` + 2 gabarits, politiques élèves/personnels | ✅ `7eacd2b` |
| A4 | Migration (M2) | `migrationPlan`/`Simulate`/`Execute` + 3 gabarits, groupes auto | ✅ Fini |
| A5 | Explorateur (M5) | 4 gabarits, `pool.Search`/`BuildTree`/`GetSubTree` | ✅ Fini |
| A6 | Export (M6) | Gabarit `export.html`, vrais handlers CSV/PDF/Trombinoscope | ✅ Fini |
| A7 | Journal (M7) | 2 gabarits, filtres `auditFilter` réels, export CSV | ✅ Fini |
| A8 | **Départs (M3)** | `internal/ad/depart.go` + routes + gabarit + CLI + i18n | ✅ Fini |
| A9 | **Paramètres (M8)** | Page 3 onglets + routes + gabarit + i18n | ✅ Fini |
| A10 | Simulation (M11) | `Validate→Simulate→Execute→Audit` généralisé + progression/annulation (A12) | ✅ Fini |
| A11 | CLI Core | Commandes JSON pour chaque module Core | ✅ Fini |
| A12 | Progression/Annulation | Barre progression + `context.Context` annulable | ✅ Fini |
| A13 | Revue parity Core | Checklist §4.1 = ✅ vérifiée dans le code, i18n fr+en | ✅ Fini |

### Lot B — Modules avancés (M12-M24) + Cache T2 — **TOUS IMPLÉMENTÉS**

| # | Modules | Points clés | État |
|---|---------|-------------|------|
| B1 | M13 Profils, M16 Heures, M15 Scripts | Attributs AD classiques (profil, heures, scripts) | ✅ Code complet |
| B2 | M17 Home dirs, M14 Quotas, M18 Class spaces | Partages/NTFS/FSRM — prérequis serveur Windows documentés | ✅ Code complet |
| B3 | **T2 Cache AD** | Cache SQLite OU/groupes/users, invalidation TTL + `uSNChanged` | ✅ Code complet |
| B4 | M12 Photos | Import masse + cadrage, vignettes explorateur, intégration étiquettes | ✅ Code complet |
| B5 | M22 Imports/Exports | Import LDAP, export LDIF/vCard, publipostage HTML | ✅ Code complet |
| B6 | M19 M365, M20 Exchange | Graph + On-prem — tests manuels documentés | ✅ Code complet |
| B7 | M21 RDS | Collections, RemoteApp, UPD/FSLogix | ✅ Code complet |
| B8 | M24 Label Studio + M23 Modèles groupes | WYSIWYG, trombinoscope A4/A3, instanciation 1-clic | ✅ Code complet |

### Lot C — Modules v4.0 (M25-M32) + Coffre — **TOUS IMPLÉMENTÉS**

| # | Modules | Points clés | État |
|---|---------|-------------|------|
| C1 | M25 Multisite | Modèle `Site`, config chiffrée par site, sélecteur header, pool par site, `site_id` journal | ✅ Code complet |
| C2 | M26 RBAC | 5 rôles, portées OU/groupe, **garde-fou sur chaque écriture AD**, journal par opérateur | ✅ Code complet |
| C3 | Coffre MDP + M32 Sauvegarde | AES-256 local, backup ZIP chiffré, restore 1-clic, migrations vN→vN+1 | ✅ Code complet |
| C4 | M27 Portail auto-service | Serveur web stdlib, code mail, PBKDF2 usage unique, RBAC, audit `utilisateur="portail"` | ✅ Code complet |
| C5 | M28 API REST + Webhooks | OpenAPI, webhooks SIS, sync bidirectionnelle | ✅ Code complet |
| C6 | M29 IA + M30 Conformité | Anomalies, nettoyage fin d'année, RGPD/MFA/PSO/signature | ✅ Code complet |
| C7 | M31 DFS/IIS/WebDAV/FTP | Priorité basse | ⬜ Non porté (squelette Python uniquement) |

---

## Critères d'acceptation (3 portes)

### Porte 1 — « Logiciel fini » (fin Priorité 1) ✅ **ATTEINTE**

- ✅ Inventaire §4 complet = ✅ vérifié dans le code (M1→M32, T2, coffre, transverses)
- ✅ Chaque écriture AD passe par `Validate → Simulate → Execute → Audit`
- ✅ `gofmt`, `go vet`, `go test ./...`, `pytest -q` tous verts
- ✅ Toutes les clés i18n ajoutées existent en français **et** anglais
- ✅ `edusync-cli migrate from-python` livré (implémenté: config, vault, audit, multisite, clé)
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

---

## Prochaines actions immédiates (dans l'ordre)

| # | Action | Responsable | Critère done |
|---|--------|-------------|--------------|
| 1 | **Valider α1 manuellement** (P2.5) | Utilisateur | 5 points : install Win, désinstall, config Python, LDAPS réel, thème/i18n |
| 2 | Publier notes release α1 | Auto (CI) | Release GitHub pré-release avec 3 artefacts + SHA256 |
| 3 | Tag `v0.2.0` (M13 Profils) | Utilisateur | `git tag v0.2.0 && git push origin v0.2.0` |
| 4 | Suivre releases M13→M32 | Utilisateur | Un tag = une release via `release-go.yml` |
| 5 | Campagne tests P3 (Samba, e2e, bench) | Dev/IA | Intégrée aux releases post-α1 |

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
| `ROADMAP_GO_MIGRATION.md` | Feuille de route officielle complète (source de vérité) |
| `docs/STATUS.md` | Vue rapide de l'avancement réel (ce qu'il faut consulter en premier) |
| `docs/modules/` | Une fiche par module : objectif, Python ref, Go à faire, avancement, validation |
| `docs/python/` | Documentation de la version Python actuelle (référence parité) |
| `docs/ARCHITECTURE.md` | Architecture Go cible (ce qui existe dans le code) |
| `docs/AD_TEST_ENV.md` | Environnement de test AD (Samba en container) |