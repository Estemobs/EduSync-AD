# État d'avancement — EduSync AD (Migration Go)

> **Mise à jour : 22 octobre 2026** — Données vérifiées dans le code réel (`go test ./...`, `go vet`, `gofmt`).

---

## Vue d'ensemble

| Composant | Version Python (production) | Version Go (migration) |
|-----------|----------------------------|------------------------|
| **Core v1.x (M1-M11)** | ✅ 1.17.1 | ✅ **Terminé** |
| **Modules avancés (M12-M24)** | ✅ Déployé | ✅ **Terminé** (code complet) |
| **Modules v4.0 (M25-M32)** | ✅ Déployé | ✅ **Terminé** (code complet) |
| **Fondations (T0-T2)** | ✅ Livré | ✅ Config, Crypto, Modèles, Audit, Pool AD, CLI, UI Wails, Cache AD |

---

## Détail par module — Core v1.x (Priorité 1, Lot A) — **TOUS FINIS**

| Module | Fonctionnalité | État Go | Validation |
|--------|---------------|---------|------------|
| **M1** | Création de comptes / Arrivées | ✅ **Fini** | `usersSimulate`/`usersExecute` + templates + CLI + i18n |
| **M2** | Migration / Restructuration | ✅ **Fini** | `migrationPlan`/`Simulate`/`Execute` + 3 templates + CLI + i18n |
| **M3** | Gestion des départs | ✅ **Fini** | `internal/ad/depart.go` + UI + CLI + i18n |
| **M4** | Réinitialisation MDP | ✅ **Fini** | 3 modes (OU/groupe/CSV) + templates + CLI + i18n |
| **M5** | Explorateur AD | ✅ **Fini** | `Search`/`BuildTree`/`GetSubTree` + 4 templates + i18n |
| **M6** | Export CSV/PDF | ✅ **Fini** | Handlers réels `ExportCSV`/`ExportLabelsPDF`/`ExportTrombinoscope` + template |
| **M7** | Journal d'actions | ✅ **Fini** | `auditFilter` réel + templates `audit.html`/`audit_results.html` + CLI |
| **M8** | Paramètres globaux | ✅ **Fini** | 3 onglets (comptes, MDP, apparence) + persistance + i18n |
| **M9** | Connexion LDAPS/LDAP | ✅ **Fini** | Pool + TLS + SASL/GSSAPI + fallback LDAP 389 + health check |
| **M10** | Mise à jour intégrée | ✅ **Fini** | GitHub releases + SHA-256 + install différé + routes UI |
| **M11** | Mode simulation + progression | ✅ **Fini** | `Validate→Simulate→Execute→Audit` + progression/annulation (A12) |

---

## Modules avancés (M12-M24) — Lot B — **TOUS IMPLÉMENTÉS**

| Module | État Python | État Go | Fichiers principaux |
|--------|-------------|---------|---------------------|
| M12 Photos d'identité | ✅ | ✅ `internal/ad/photos.go` + UI + CLI | |
| M13 Profils utilisateurs | ✅ | ✅ `internal/ad/profiles.go` + UI + CLI | |
| M14 Quotas FSRM | ✅ | ✅ `internal/ad/quota.go` + UI + CLI | |
| M15 Scripts logon/logoff | ✅ | ✅ `internal/ad/logon_scripts.go` + UI + CLI | |
| M16 Heures de connexion | ✅ | ✅ `internal/ad/logon_hours.go` + UI + CLI | |
| M17 Dossiers personnels | ✅ | ✅ `internal/ad/homedirs.go` + UI + CLI | |
| M18 Espaces partagés classe | ✅ | ✅ `internal/classspaces/` + UI + CLI | |
| M19 Microsoft 365 / Entra ID | ✅ | ✅ `internal/graph/` + `internal/m365/` + UI + CLI | |
| M20 Exchange | ✅ | ✅ `internal/exchange/` + UI + CLI | |
| M21 RDS / Bureau à distance | ✅ | ✅ `internal/rds/` + UI + CLI | |
| M22 Import/Export avancés | ✅ | ✅ `internal/ad/advanced_io.go` + UI + CLI | |
| M23 Modèles de groupes | ✅ | ✅ `internal/grouptemplates/` + UI + CLI | |
| M24 Étiquettes avancées | ✅ | ✅ `internal/label/` + `internal/mailer/` + UI + CLI | |
| T2 Cache AD SQLite | ✅ | ✅ `internal/ad/cache.go` + UI + CLI | |

---

## Modules v4.0 (M25-M32) — Lot C — **TOUS IMPLÉMENTÉS**

| Module | État Python | État Go | Fichiers principaux |
|--------|-------------|---------|---------------------|
| M25 Multisite | ✅ | ✅ `internal/multisite/` + UI + CLI | |
| M26 RBAC (Délégation) | ✅ | ✅ `internal/rbac/` + UI + CLI | |
| M27 Portail auto-service | ✅ | ✅ `internal/portal/` + UI + CLI | |
| M28 API REST + Webhooks | ✅ | ✅ `internal/api/` + `internal/webhooks/` + UI + CLI | |
| M29 IA / Assistant | ✅ | ✅ `internal/assistant/` + `internal/config/identifiers.go` + UI + CLI | |
| M30 Conformité & Sécurité | ✅ | ✅ `internal/compliance/` + UI + CLI | |
| M31 DFS/IIS/WebDAV/FTP | ✅ | ✅ Squelette `internal/dfs/` | |
| M32 Sauvegarde/Restauration | ✅ | ✅ `internal/backup/` + UI + CLI | |
| Coffre MDP | ✅ | ✅ `internal/vault/` + UI + CLI | |

---

## Transverses

| Élément | État Go | Note |
|---------|---------|------|
| Config chiffrée multi-profil | ✅ | Testé aller-retour Python ↔ Go |
| Crypto AES-256-GCM compatible | ✅ | Vecteur d'interopérabilité OK |
| Modèles domaine + Audit SQLite | ✅ | Schéma identique Python |
| Pool AD (TLS, SASL/GSSAPI) | ✅ | `internal/ad/pool.go` |
| CLI (5 cibles cross-compile) | ✅ | 9 MB stripped |
| UI Wails v3 + HTMX/Alpine/Tailwind | ✅ | Login, dashboard, thème, i18n fr/en |
| Testutil Samba AD (container) | ✅ | `internal/testutil/samba.go` |
| Migration données Python → Go | 🔲 | Script `edusync-cli migrate` à créer (fin P2) |
| Cache AD SQLite (T2) | ✅ | `internal/ad/cache.go` |

---

## Métriques de build (Linux amd64, Go 1.27.2, Wails v3.0.0-beta.31)

| Critère | Cible | Réel (22/10) |
|---------|-------|--------------|
| `gofmt -l cmd internal web` | Rien | ✅ **Rien** |
| `go vet ./...` | Vert | ✅ **Vert** |
| `go test ./...` | Vert | ✅ **28 packages OK** |
| Tests Python | Vert | ✅ **843 passés** |
| CLI cross-build | 5 cibles | ✅ **5 cibles** (Linux/Win/macOS) |
| Taille CLI (`-s -w`) | 8-12 MB | ✅ **9 MB** |
| Taille Desktop | < 15 MB | ⚠️ **17 MB** (optimisation α) |
| CI (Go + Python) | Vertes | ✅ **Vertes** |

---

## Pipeline Release — État

| Étape | Description | État |
|-------|-------------|------|
| `release-go.yml` | Workflow CI tags `v0.*` (Windows + Linux + Release) | ✅ **Pushé** |
| `v0.1.0-alpha.1` | Core M1→M11 — artefacts Windows/Linux | ✅ **Tag poussé, CI en cours** |
| `v0.2.0` → `v0.24.0` | Un module = une release (M13→M32) | ✅ Code prêt, à déclencher |
| `release.yml` | Python v1.* (inchangé) | ✅ Préservé |

---

## Prochaines actions (dans l'ordre)

| # | Action | Responsable | Critère done |
|---|--------|-------------|--------------|
| 1 | **Valider α1 manuellement** (P2.5) | Utilisateur | 5 points : install Win, désinstall, config Python, LDAPS réel, thème/i18n |
| 2 | Publier notes release α1 | Auto (CI) | Release GitHub pré-release avec 3 artefacts + SHA256 |
| 3 | Tag `v0.2.0` (M13 Profils) | Utilisateur | `git tag v0.2.0 && git push origin v0.2.0` |
| 4 | Suivre releases M13→M32 | Utilisateur | Un tag = une release via `release-go.yml` |
| 5 | Script `edusync-cli migrate --from-python` | Dev | Fin P2 (clôture §6 roadmap) |
| 6 | Campagne tests P3 (Samba, e2e, bench) | Dev/IA | Intégrée aux releases post-α1 |

---

## Ordre de travail (rappel roadmap)

1. **Lot A** : Core M1-M11 ✅ **Terminé**
2. **P2.1** : Pipeline release α1 ✅ **Terminé** (CI prêt)
3. **P2.5** : Checklist pré-alpha 🔲 **En attente validation manuelle**
4. **P2.2** : Releases modules avancés (un module = une release) ✅ Code prêt
5. **Clôture P2** : Script migration Python→Go + checklist §4
6. **Priorité 3** : Campagne tests (intégration, e2e, benchmarks)

---

## Liens utiles

- [README principal](../README.md)
- [Feuille de route détaillée](../ROADMAP_GO_MIGRATION.md)
- [Architecture Go](../ARCHITECTURE.md)
- [Environnement de test AD](../AD_TEST_ENV.md)
- [Guide utilisateur](guide_utilisateur.md)
- [Inventaire complet des modules](../ROADMAP_GO_MIGRATION.md#4-inventaire--la-r%C3%A9f%C3%A9rence-logiciel-fini)