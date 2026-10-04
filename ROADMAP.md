# EduSync AD — Feuille de route & Vision Produit

**Version 2.0** — Document de référence pour le développement futur

---

## 🎯 Vision

Devenir l'outil de référence pour la gestion du cycle de vie des comptes Active Directory dans les établissements scolaires, PME et collectivités, **sans jamais nécessiter d'ouvrir une console Microsoft (ADUC, RSAT, PowerShell)**.

> « Import CSV → comptes créés. Fin d'année → classes migrées. Départ → compte archivé. En quelques clics, pas en PowerShell. »

---

## 📊 Avancement (mise à jour : 4 octobre 2026)

| Lot | Modules | État |
|-----|---------|------|
| **Fondations v2.0** | T0 LDAP asynchrone, T1 système de plugins, T2 cache AD SQLite | ✅ Livré |
| **v2.0** | M12 → M18 (photos, profils, quotas, scripts, heures, dossiers perso, espaces de classe) | ✅ Livré |
| **v2.5** | M19 → M22 (Microsoft 365, Exchange, RDS, imports/exports avancés) | ✅ Livré |
| **v3.0** | M23 modèles de groupes ✅ — M24 étiquettes & trombinoscopes ✅ — M25 multisite ✅ | ✅ Livré |
| **v3.5** | M26 délégation RBAC ✅ — M27 portail auto-service ✅ — M28 API REST + Webhooks ✅ | ⬜ À faire |
| **v4.0** | M29 IA / Assistant ✅ — M30 conformité & sécurité ✅ — M31 DFS/IIS ⚡ — M32 sauvegarde | ⬜ À faire |

---

## 📦 Modules actuels (v1.x — Déployés)

| Module | Statut | Description |
|--------|--------|-------------|
| **M1 — Création de comptes / Arrivées** | ✅ Fait | Import CSV, génération identifiants/mots de passe, résolution doublons, vérif AD, export CSV étiquettes |
| **M2 — Migration de classe** | ✅ Fait | Via CSV (prénom/nom/classe) ou interface (sélection OU), groupes de classe auto, move AD |
| **M3 — Gestion des départs** | ✅ Fait | Désactivation immédiate ou suppression différée (archivage + purge planifiée), résolution par prénom/nom |
| **M4 — Réinitialisation MDP** | ✅ Fait | Par OU, groupe AD, ou CSV ; politique MDP configurable ; export CSV |
| **M5 — Explorateur AD** | ✅ Fait | Arborescence OUs + groupes, panneau central unifié, clic droit complet (modif, move, MDP, groupes, suppr), recherche temps réel |
| **M6 — Export (CSV / Étiquettes PDF)** | ✅ Fait | Sélection OU ± sous-OUs, champs cochables (incl. MDP mémorisés), PDF Avery L7160/L7163, couleurs, QR codes |
| **M7 — Journal d'actions** | ✅ Fait | Filtres date/type/résultat/domaine (M25)/opérateur (M26), export CSV, SQLite local |
| **M8 — Paramètres globaux** | ✅ Fait | 4 onglets : Comptes (identifiants, mail, groupes, départs), Mots de passe (politiques élèves/personnels, coffre), Apparence (thème, langue), Délégation (M26, Super-admin) |
| **M9 — Connexion LDAPS/LDAP** | ✅ Fait | LDAPS prioritaire, repli LDAP, validation certif configurable, CA custom, mémorisation chiffrée AES-256 |
| **M10 — Mise à jour intégrée** | ✅ Fait | Vérification, téléchargement, installation, redémarrage auto, somme de contrôle |
| **M11 — Mode simulation** | ✅ Fait | Test import sans écriture AD |

---

## 🏗️ Fondations techniques v2.0 (Critique — Sprint 1-3)

| Module | Description | Pourquoi maintenant |
|--------|-------------|---------------------|
| **T0 — Couche LDAP Asynchrone** | Remplacer `RLock` + threads manuels par queue de jobs (`Job(id, coro, progress_cb, done_cb)`), `QThreadPool` worker, signaux Qt `progress`, `finished`, `error`, `cancelled` | Débloque tous les modules masse (M12-M21) : UI responsive, progression réelle, annulation, pas de "Not Responding" |
| **T1 — Système de plugins** | Chargeur de modules via `entry_points`, interface `IModule {id, name, version, widget, requires_ad, permissions, on_load, on_unload}` | Permet M19-M32 sans toucher au core, distribution modulaire, activation/désactivation runtime |
| **T2 — Cache AD Local (SQLite)** | Cache OU/groupes/users synchronisé en background, invalidation TTL + `uSNChanged` polling, recherche offline | Explorateur instantané, navigation fluide sur gros domaines (>5k objets) |

---

## 🚀 Modules planifiés (v2.x — Court terme)

### M12 — Gestion des photos d'identité ✅
- Import manuel (une par une) via Explorateur AD
- Import en masse depuis CSV + dossier photos (mapping prénom/nom → fichier)
- Import par nommage conventionnel (ex: `prenom_nom.jpg`, `identifiant.jpg`)
- Redimensionnement/cadrage auto (ratio 3:4, max 100 Ko)
- Affichage vignette dans Explorateur AD + étiquettes PDF

### M13 — Profils utilisateurs (itinérant / local / obligatoire) ✅
- Configuration par OU ou groupe : chemin profil itinérant (`\\srv\profil\%USERNAME%`), local, obligatoire (`.man`)
- Application en masse lors création/migration
- Support variables `%USERNAME%`, `%SAM%`, `%OU%`

### M14 — Quotas de disque (FSRM) ✅
- Définition quota par OU/groupe (taille, seuils alerte, blocage dur)
- Application auto à la création de dossier personnel
- Rapport quotas (export CSV)

### M15 — Scripts logon/logoff (GPO-like) ✅
- Éditeur de scripts (bat/powershell/vbs) par OU ou groupe
- Variables : `%USERNAME%`, `%FULLNAME%`, `%OU%`, `%GROUP%`, `%EMAIL%`, `%HOMEDIR%`
- Déploiement auto via attribut `scriptPath` ou GPO liaison

### M16 — Heures de connexion (logonHours) ✅
- Interface visuelle (grille 24h × 7j) par utilisateur ou en masse (OU/groupe)
- Préréglages : "Heures cours", "Heures admin", "Personnalisé"
- Application via `logonHours` attribut AD

### M17 — Dossiers personnels (Home Directory) ✅
- Création auto à la création de compte : `\\srv\homes\%USERNAME%`
- Droits NTFS : utilisateur (Modification), Administrateurs (Contrôle total), SYSTEM
- Quotas FSRM liés (voir M14)
- Lettre de lecteur configurable (ex: H:)

### M18 — Espaces partagés par classe/groupe ✅
- Création auto partage SMB + droits NTFS à la création groupe de classe
- Modèle : `\\srv\classes\%GROUP%` — profs (Lecture/Écriture), élèves (Lecture), admins (Total)
- Synchronisation membres AD ↔ droits partage

---

## 📈 Modules planifiés (v2.5 — Moyen terme)

### M19 — Office 365 / Entra ID (Hybride) ✅
- Connexion Microsoft Graph (app registration, certificat/secret)
- Création utilisateurs cloud + licences (packs éducation A1/A3/A5)
- Groupes de distribution / sécurité / Teams auto depuis groupes AD primaires
- Synchronisation mot de passe (PHS) ou fédération (ADFS/PTA)
- Photos utilisateurs → cloud

### M20 — Microsoft Exchange (On-prem / Online) ✅
- Création boîtes aux lettres utilisateurs/groupes
- Aliases / adresses proxy supplémentaires
- Politiques d'adresses (Email Address Policies)
- Quotas boîte, rétention, archivage

### M21 — RDS / Bureau à distance ✅
- Collections de sessions / applications RemoteApp
- Publication apps par groupe AD
- Profils utilisateurs RDS (disques profil UPD / FSLogix)

### M22 — Import/Export avancés ✅
- **Import LDAP** : depuis OU, groupe, filtre LDAP personnalisé
- **Export LDIF** : paramétrable, pour annuaires LDAP tiers
- **Export vCard** : cartes de visite
- **Publipostage HTML** : chartes, conventions, docs personnalisés
- **Import GEP / Base Education Nationale** : via outil tiers fourni

---

## 🔮 Modules vision (v3.x+ — Long terme / Différenciation)

### M23 — Modèles de groupes (Templates) ✅
- Modèle "Classe élève", "Classe prof", "Personnel admin", "Service technique"…
- Chaque modèle définit : OU parente, groupes auto, scripts, quotas, dossier personnel, partage, profil, heures connexion, politiques MDP, licences O365
- Instanciation 1-clic : saisie nom classe → tout créé
- Duplication/export/import modèles (XML/JSON)

### M24 — Étiquettes & Trombinoscopes avancés ✅
- Éditeur visuel WYSIWYG (positionnement champs, police, couleur, QR code, photo)
- Modèles pré-enregistrés (Avery L7160, L7163, badges, cartes)
- Trombinoscope : grille photos + noms, export PDF A4/A3
- Envoi étiquette individuelle par mail

### M25 — Multisite / Multi-domaine ✅
- Un EduSync AD gère plusieurs domaines/forêts AD
- Fichier `domaines.json` chiffré (liste domaines gérés)
- Sélecteur domaine dans la barre supérieure
- Connexion un seul domaine à la fois
- Journal d'audit séparé par domaine

### M26 — Délégation d'administration (RBAC) ✅
- Rôles : Super-admin, Admin site, Admin classe, Helpdesk, Lecture seule
- Délégation par OU/groupe : qui peut créer/migrer/supprimer/réinitialiser où
- Journal d'audit par opérateur (enrichir M7)
- Fichier `delegations.json` (clair) : opérateur, rôle, site (M25), portées OU/groupes, actif
- Double barrière : garde-fou sur chaque écriture AD (`ADConnection`) + navigation filtrée
- Onglet « Délégation » dans les Paramètres, réservé au Super-admin

### M27 — Auto-service élève/parent (Portail Web) ✅
- Réinitialisation MDP self-service (code par courriel, stockage PBKDF2 à usage unique, anti-énumération)
- Consultation identifiant/mail (même vérification par code)
- Demande création compte (pré-inscription) → validation admin (création compte + envoi identifiants)
- **Hors périmètre** : codes par SMS et validation Microsoft Authenticator (fournisseurs externes requis)
- Portail web 100 % stdlib (`http.server`, `smtplib`, `sqlite3`) — aucune dépendance externe
- Cycle de vie serveur géré par l'onglet, RBAC (`reset_password` / `create_user`), audit `utilisateur="portail"`

### M28 — API REST + Webhooks
- API documentée (OpenAPI/Swagger) pour intégration SIS (PRONOTE, EcoleDirecte, etc.)
- Webhooks : compte créé, migré, départ, MDP reset → notifications externes (Teams, Slack, mail, SI)
- Sync bidirectionnelle : EduSync ↔ SIS

### M29 — Intelligence artificielle / Assistant
- Détection anomalies : doublons non résolus, comptes orphelins, OU vides, groupes sans membres
- Suggestion nettoyage fin d'année
- Génération identifiants "intelligente" (évite homonymes, respecte charte établissement)
- Analyse logs → recommandations sécurité

### M30 — Conformité & Sécurité avancée
- Rapport RGPD : données personnelles stockées, droit à l'oubli (purge coffre MDP, anonymisation journal)
- Durcissement : MFA admin, session unique, verrouillage app par MDP
- Granularité MDP (Fine-Grained Password Policies / PSO)
- Signature numérique exports (horodatage, non-répudiation)

### M31 — DFS / IIS / WebDAV / FTP Isolé
- Espaces partagés via DFS (réplication multi-serveurs)
- Publication WebDAV/IIS auto pour dossiers perso/partagés
- FTP isolé AD — *héritage, priorité basse*

### M32 — Sauvegarde / Restauration / Migration appli
- Backup complet (config, journal, coffre MDP, modèles, certificats) → ZIP chiffré
- Restore 1-clic sur nouvelle machine
- Migration données vN → vN+1 auto

---

## 📋 Transverses & Qualité (Continu)

| Axe | Actions |
|-----|---------|
| **Tests** | Couverture > 90% (unit + intégration AD mock), tests E2E Playwright/Cypress |
| **CI/CD** | Build multi-plateforme (Windows EXE, Linux Flatpak, macOS DMG), signature code, SBOM |
| **Accessibilité** | WCAG 2.1 AA, thèmes haut contraste, navigation clavier, lecteurs d'écran |
| **Internationalisation** | Français (complet), Anglais (complet), Espagnol/Allemand/Italien (communautaire) |
| **Documentation** | Guide utilisateur (✅), Guide admin déploiement, API doc, Tutoriels vidéo |
| **Télémétrie opt-in** | Usage modules, erreurs, performances — anonymisé, local-first |

---

## 🗓️ Jalons suggérés

| Version | Cible | Modules clés |
|---------|-------|--------------|
| **v2.0** | Q1 2026 | T0 Async LDAP, T1 Plugin System, T2 AD Cache, M12, M13, M14, M15, M16, M17, M18 |
| **v2.5** | Q3 2026 | M19, M20, M21, M22 |
| **v3.0** | Q1 2027 | M23, M24, M25 |
| **v3.5** | Q3 2027 | M26, M27 (optionnel) |
| **v4.0** | 2028 | M28, M29, M30, M31, M32 |

> *Les dates sont indicatives. Priorité = valeur utilisateur / effort / rétroaction terrain.*

---

## 📌 Principes directeurs

1. **Zéro connaissance AD** — L'admin saisit prénoms/noms/équipes. Jamais de DN, OU, GUID, SID.
2. **Simulation par défaut** — Toute écriture AD passe par prévisualisation modifiable.
3. **Traçabilité totale** — Chaque action journalisée, exportable, non effaçable (append-only).
4. **Sécurité par défaut** — LDAPS obligatoire, coffre MDP chiffré, pas de secret en clair, MFA admin.
5. **Hors-ligne first** — Aucune donnée ne sort du poste admin sans action explicite (pas de cloud forcé).
6. **Modulaire** — Chaque module activable/désactivable via plugin system. Installation minimale = M1+M9+M10.
7. **Standards ouverts** — CSV (`;` UTF-8), LDIF, vCard, JSON, OpenAPI, SQLite, AES-256, Flatpak/MSIX.
8. **100% Desktop** — Pas de backend web requis. PyQt6 natif, binaires autonomes.

---

## 🔗 Références

- **Guide utilisateur** : `docs/guide_utilisateur.md`
- **Exemples CSV** : `exemples/`
- **Code source** : `src/edusync_ad/`
- **Tests** : `tests/core/`
- **Build** : `packaging/` (PyInstaller, Flatpak, Inno Setup)
- **Icône** : `assets/icon.svg` → `tools/generate_icon.py`

---

*Document vivant — à faire évoluer au fil des retours utilisateurs et des versions.*