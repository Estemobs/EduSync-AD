# Guide utilisateur — EduSync AD

**Version 1.1 — Français**

EduSync AD est un outil de gestion du cycle de vie des comptes utilisateurs
dans un Active Directory, conçu pour les administrateurs réseau des
établissements scolaires.

---

## Sommaire

1. [Première connexion](#1-première-connexion)
2. [Mode simulation](#2-mode-simulation)
3. [Module 1 — Création de comptes](#3-module-1--création-de-comptes)
4. [Module 2 — Migration de classe](#4-module-2--migration-de-classe)
5. [Module 3 — Gestion des départs](#5-module-3--gestion-des-départs)
6. [Module 4 — Arrivées en cours d'année](#6-module-4--arrivées-en-cours-dannée)
7. [Module 5 — Réinitialisation de mot de passe](#7-module-5--réinitialisation-de-mot-de-passe)
8. [Module 6 — Explorateur AD](#8-module-6--explorateur-ad)
9. [Journal d'actions](#9-journal-dactions)
10. [Paramètres globaux](#10-paramètres-globaux)
11. [Dépannage — Erreur de certificat LDAPS](#11-dépannage--erreur-de-certificat-ldaps)

---

## 1. Première connexion

Au lancement, l'écran de connexion s'affiche.

| Champ | Description | Exemple |
|-------|-------------|---------|
| Nom de domaine | Nom DNS complet du domaine | `lycee-victor-hugo.local` |
| Contrôleur de domaine | Adresse IP ou nom du serveur AD | `10.0.0.5` |
| Nom d'utilisateur | Compte administrateur du domaine | `admin` |
| Mot de passe | Mot de passe du compte (jamais stocké) | — |

L'indicateur coloré reflète l'état de la connexion :
- **Rouge** — Déconnecté
- **Orange** — Connexion en cours
- **Vert** — Connecté

La connexion chiffrée **LDAPS** (port 636) est tentée en priorité. Si elle
est indisponible, un repli sur LDAP non chiffré est effectué avec un
avertissement.

Cochez **Mémoriser la connexion** pour sauvegarder le domaine et
l'utilisateur (chiffrés AES-256). Le mot de passe n'est jamais conservé.

### Vérification du certificat LDAPS

Deux champs supplémentaires contrôlent la validation du certificat présenté
par le contrôleur de domaine en LDAPS :

| Champ | Rôle |
|-------|------|
| **Vérifier le certificat du contrôleur en LDAPS** (coché par défaut) | Vérifie que le certificat présenté est valide et correspond bien au contrôleur — protège contre une interception du trafic. |
| **Certificat CA (optionnel)** | Chemin vers le certificat racine de l'autorité qui a émis le certificat du contrôleur (fichier `.pem`/`.crt`), avec un bouton **Parcourir…**. |

Si la connexion échoue avec un message du type *« Le certificat présenté par
le contrôleur de domaine en LDAPS n'a pas pu être validé »*, voir la section
[11. Dépannage — Erreur de certificat LDAPS](#11-dépannage--erreur-de-certificat-ldaps).

---

## 2. Mode simulation

Le bouton **Mode simulation** dans le bandeau supérieur permet de tester
toutes les opérations sans aucune écriture réelle dans l'AD.

- En mode actif, le bandeau devient orange et toutes les actions affichent
  **(simulé)** dans leur résultat.
- Les lectures AD (résolution des utilisateurs, vérification des doublons)
  restent réelles même en simulation.
- Le journal d'actions enregistre les opérations simulées avec l'indicateur
  « Simulation : Oui ».

> **Conseil** : activez toujours le mode simulation avant la rentrée pour
> vérifier vos imports sans risque.

---

## 3. Module 1 — Création de comptes

**Usage typique** : créer les comptes élèves ou personnels en début d'année
à partir d'un export de votre logiciel de gestion scolaire (PRONOTE, etc.).

### Flux de travail

**Étape 1 — Importer le fichier CSV**

Cliquez sur **Choisir un fichier CSV…** et sélectionnez votre fichier.
Un aperçu des 5 premières lignes s'affiche pour vérification.

Format attendu (délimiteur `;` ou `,`) :

```
prenom;nom;ou;email_perso;date_naissance;numero
Thomas;Martin;OU=3emeA,OU=Eleves,DC=lycee,DC=local;;2010-03-15;
```

Colonnes **obligatoires** : `prenom`, `nom`, `ou`
Colonnes **facultatives** : `email_perso`, `date_naissance`, `numero`

Si vos en-têtes sont différents, utilisez les menus déroulants de mapping
pour associer chaque colonne.

**Étape 2 — Choisir le type de compte et le format d'identifiant**

- **Type** : Élève ou Personnel (détermine la politique de mot de passe)
- **Format** : choisissez un format prédéfini ou saisissez un format libre

Formats prédéfinis disponibles : `prenom.nom`, `p.nom`, `pnom`, `nom.pp`,
`prenomNom`, `NomPrenom`, etc.

Format personnalisé avec variables :

| Variable | Résultat (Thomas Martin) |
|----------|--------------------------|
| `{P}` | `thomas` |
| `{N}` | `martin` |
| `{p1}` | `t` |
| `{p2}` | `th` |
| `{n3}` | `mar` |
| `{AN}` | `25` |
| `{ANNEE}` | `2025` |

Exemple : `{p1}{p2}.{N}` → `th.martin`

L'aperçu en temps réel (Thomas Martin) s'affiche à droite du champ.

**Étape 3 — Générer la prévisualisation**

Cliquez sur **Générer la prévisualisation**. Le tableau affiche pour chaque
ligne : identifiant, nom complet, mot de passe, adresse mail, OU cible,
groupe de classe, état.

- Les **doublons résolus** sont signalés (⚠ Doublon résolu) — la règle de
  résolution configurée dans les Paramètres a été appliquée automatiquement.
- Vous pouvez **modifier individuellement** identifiant et mot de passe en
  double-cliquant sur la cellule.

**Étape 4 — Valider**

Cliquez sur **Valider**. L'outil crée les comptes dans l'AD et propose
automatiquement un export CSV contenant : prénom, nom, identifiant, mot de
passe en clair, adresse mail. Ce fichier est destiné à l'impression et à la
distribution.

---

## 4. Module 2 — Migration de classe

**Usage typique** : passage de 4ème A vers 3ème A en fin d'année scolaire.

Deux modes disponibles (onglets) :

### Via CSV

Format attendu — **prénom, nom et noms de classe en clair**, jamais de
chemin AD à connaître ou saisir :
```
prenom;nom;classe_source;classe_destination
Thomas;Martin;4emeA;3emeA
```

`classe_source` et `classe_destination` sont résolues automatiquement en OU
complète : réglage **"OU parente pour les classes"** des Paramètres si
configuré, sinon racine du domaine connecté (voir
[10. Paramètres globaux](#10-paramètres-globaux)). L'élève est ensuite
retrouvé par prénom+nom dans l'OU source résolue — aucun identifiant AD à
connaître.

D'autres noms de colonnes courants sont reconnus automatiquement pour
`classe_source`/`classe_destination` : `ancienne_classe`/`nouvelle_classe`,
`classe_actuelle`/`nouvelle_classe`. Sinon, associez-les manuellement dans
le mapping.

1. Importez le fichier CSV
2. Vérifiez le mapping des colonnes
3. Cliquez **Résoudre dans l'AD** — les utilisateurs non trouvés sont signalés
4. Cliquez **Valider la migration**

### Via l'interface

Sans fichier CSV, saisissez directement :
- **OU source** : DN de l'OU de départ
- **OU destination** : DN de l'OU d'arrivée

Cliquez **Charger les utilisateurs de l'OU source** pour lister tous les
comptes de l'OU, puis **Valider la migration**.

### Ce que fait la migration

- Déplace chaque utilisateur vers l'OU destination
- Si **Groupes de classe automatiques** est activé dans les Paramètres :
  retire l'utilisateur du groupe de l'OU source et l'ajoute au groupe de
  l'OU destination (crée le groupe si nécessaire)
- Les utilisateurs non trouvés génèrent un avertissement sans bloquer le reste

---

## 5. Module 3 — Gestion des départs

**Usage typique** : traitement des élèves ou personnels quittant
l'établissement.

### Modes de traitement

| Mode | Ce qui se passe |
|------|-----------------|
| **Désactivation immédiate** | Retire l'utilisateur de tous ses groupes et désactive le compte (reste dans l'AD) |
| **Suppression différée** | Retire des groupes, déplace vers l'OU d'archivage, programme la suppression après le délai configuré |

### Flux de travail

1. Importez un CSV — deux formats possibles :
   - **`prenom;nom`** (recommandé, aucun identifiant AD à connaître) —
     recherche par nom dans tout l'annuaire.
   - **`identifiant`** — si vous disposez déjà des identifiants AD (recherche
     exacte, prioritaire sur prénom+nom si les deux colonnes sont présentes).
2. Associez les colonnes si le mapping automatique ne les a pas détectées
3. Choisissez le mode d'action
4. Cliquez **Résoudre dans l'AD**
5. Vérifiez la prévisualisation (groupes détectés, statut)
6. Cliquez **Valider**

### Suppressions en attente

Le panneau **Suppressions en attente** liste les comptes en cours de délai
avec leur date d'archivage et leur date d'échéance.

- **Supprimer les comptes échus** : supprime définitivement tous les comptes
  dont le délai est écoulé
- **Annuler la suppression programmée** : sélectionnez une ligne et cliquez
  ce bouton pour retirer le compte de la file d'attente (le compte reste
  dans l'OU d'archivage)

---

## 6. Module 4 — Arrivées en cours d'année

Identique au Module 1 — Création de comptes, avec une vérification
préalable dans l'AD : si un utilisateur avec le même prénom et nom existe
déjà dans l'OU cible, la ligne est marquée **⚠ Doublon AD** et ignorée lors
de la validation.

Cette vérification permet d'éviter les doublons lors d'inscriptions tardives
ou de retours d'élèves.

---

## 7. Module 5 — Réinitialisation de mot de passe

**Usage typique** : réinitialiser les mots de passe de toute une classe ou
d'un groupe en début d'année.

### Sources disponibles

| Source | Description |
|--------|-------------|
| **OU entière** | Tous les utilisateurs d'une OU (et ses sous-OUs) |
| **Groupe AD** | Membres d'un groupe (cliquez **Actualiser** pour charger la liste) |
| **Fichier CSV** | Colonne `identifiant` ou `login` |

### Flux de travail

1. Choisissez la source et configurez-la
2. Cliquez **Charger les utilisateurs**
3. Ajustez la politique de mot de passe si nécessaire (longueur, complexité)
4. Cochez **Forcer le changement à la prochaine connexion** si souhaité
5. Cliquez **Générer les mots de passe** — les nouveaux mots de passe
   apparaissent dans le tableau
6. Cliquez **Valider la réinitialisation**
7. Utilisez **Exporter CSV** pour récupérer le fichier identifiants/mots de passe

---

## 8. Module 6 — Explorateur AD

L'Explorateur AD permet de naviguer dans l'annuaire et d'agir directement
sur les comptes sans passer par un import CSV.

### Navigation

**Panneau gauche — Onglet OUs** : arborescence des Unités Organisationnelles.
Cliquez sur une OU pour lister ses utilisateurs.

**Panneau gauche — Onglet Groupes** : liste de tous les groupes AD.
Cliquez sur un groupe pour lister ses membres.

**Barre de recherche** : filtrez les utilisateurs affichés par nom complet
ou identifiant (filtre en temps réel).

### Actions sur un compte sélectionné

Sélectionnez un utilisateur dans la liste centrale pour activer le panneau
de droite :

| Action | Description |
|--------|-------------|
| **Modifier un attribut** | Modifie displayName, description, téléphone, département, titre ou adresse mail |
| **Changer d'OU** | Déplace le compte vers une autre OU (dialogue de sélection arborescente) |
| **Réinitialiser le mot de passe** | Génère un nouveau mot de passe affiché avant confirmation |
| **Activer / Désactiver** | Bascule l'état du compte (userAccountControl) |
| **Gérer les groupes** | Liste tous les groupes avec cases à cocher pour ajouter/retirer |

Toutes ces actions respectent le mode simulation et sont journalisées.

---

## 9. Journal d'actions

Le journal enregistre chaque opération : type d'action, compte concerné,
OU source et destination, résultat (succès/échec), identifiant de session,
indicateur simulation.

### Filtres

| Filtre | Options |
|--------|---------|
| **Du / Au** | Plage de dates (sélecteur calendrier) |
| **Type d'action** | Création, migration, désactivation, réinitialisation MDP, etc. |
| **Résultat** | Tous / Succès / Échec |

Cliquez **Appliquer les filtres** pour rafraîchir, **Réinitialiser** pour
revenir aux 30 derniers jours.

### Export

Cliquez **Exporter en CSV** pour télécharger toutes les entrées filtrées.
Le fichier contient : horodatage, type, compte, OUs, résultat, simulation, détail.

Le journal est stocké localement sur la machine de l'administrateur
(base SQLite dans le répertoire de données utilisateur).

---

## 10. Paramètres globaux

Accessibles via **Paramètres** dans la sidebar.

### Nomenclature des identifiants

- **Format élèves / personnels** : format prédéfini ou personnalisé
- **Règle de résolution des doublons** : que faire si `thomas.martin` existe déjà

| Règle | Exemple |
|-------|---------|
| Suffixe numérique direct | `thomas.martin2` |
| Suffixe numérique avec séparateur | `thomas.martin-2` |
| Préfixe numérique | `2.thomas.martin` |
| Lettres supplémentaires du prénom | `tho.martin` → `thor.martin` |
| Lettres supplémentaires du nom | `thomas.mart` → `thomas.marti` |
| Année en suffixe | `thomas.martin2025` |

- **Prénoms composés** : Premier prénom / Concaténation / Troncature au tiret

### Adresses mail

- **Domaine mail** : domaine des adresses générées (ex. `lycee-victor-hugo.fr`)
- **Format** : utilise les mêmes variables que les identifiants (`{P}`, `{N}`, `{p1}`…)

### Groupes de classe

Activez **Création automatique des groupes de classe** pour qu'un groupe
portant le nom de l'OU soit créé automatiquement et que les utilisateurs y
soient ajoutés lors de la création ou de la migration.

**OU parente pour les classes** : l'OU sous laquelle les OU de classe
(colonne `classe` d'un import Module 1/4) sont recherchées ou créées, ex.
`OU=eleves,DC=lycee,DC=local`. Ce réglage est mémorisé une fois pour toutes
— inutile de le ressaisir à chaque import (il peut toujours être remplacé
ponctuellement via "OU parente pour les classes" dans le Module 1).
**Laissé vide, la racine du domaine connecté est utilisée automatiquement**
: une simple colonne `classe` dans le CSV suffit donc à créer/remplir la
bonne OU sans aucune configuration préalable.

### Gestion des départs

- **OU d'archivage** : DN de l'OU vers laquelle les comptes en suppression
  différée sont déplacés (ex. `OU=Archives,DC=lycee,DC=local`)
- **Délai avant suppression** : nombre de jours entre l'archivage et la
  suppression définitive (valeur libre, 1 à 3650 jours)

### Politiques de mot de passe

Configurées séparément pour les élèves et les personnels :

| Option | Description |
|--------|-------------|
| Longueur | 6 à 32 caractères |
| Majuscules | Inclusion obligatoire d'au moins une majuscule |
| Chiffres | Inclusion obligatoire d'au moins un chiffre |
| Caractères spéciaux | Inclusion de `!@#$%^&*-_+=?` |
| Mot de passe identique | Même mot de passe pour tous les comptes du lot |
| Pattern fixe | Ex. `Ecole{AN}!` → `Ecole25!` |

### Apparence

- **Thème** : Clair / Sombre
- **Langue** : Français / English

---

## 11. Dépannage — Erreur de certificat LDAPS

### Pourquoi cette erreur apparaît

Un Active Directory interne n'utilise presque jamais un certificat LDAPS
émis par une autorité publique reconnue (comme le sont les certificats des
sites web classiques) — il utilise un certificat émis par **sa propre
autorité de certification** (AD CS), que votre poste ne connaît pas et ne
peut donc pas valider par défaut. C'est un comportement normal, pas une
anomalie du contrôleur de domaine.

Le message affiché par EduSync AD :

> *Le certificat présenté par le contrôleur de domaine en LDAPS n'a pas pu
> être validé (autorité inconnue ou nom d'hôte ne correspondant pas). Un AD
> interne utilise presque toujours un certificat émis par sa propre autorité
> (AD CS) : renseignez son certificat racine (.pem/.crt) dans les paramètres
> de connexion, ou désactivez explicitement la vérification si vous
> acceptez le risque.*

Deux solutions : réparer LDAPS proprement (recommandé), ou contourner la
vérification (dépannage rapide / labo de test uniquement).

### Solution recommandée — installer une autorité de certification (AD CS)

Si LDAPS ne répond même pas du tout (le port 636 refuse la connexion, pas
seulement le certificat), c'est qu'aucun certificat LDAPS n'est installé sur
le contrôleur de domaine. Il faut d'abord lui en fournir un :

**Sur le contrôleur de domaine**, via le Gestionnaire de serveur :

1. **Gérer** → **Ajouter des rôles et fonctionnalités**.
2. Passer les écrans jusqu'à **Rôles de serveurs** : cocher **Services de
   certificats Active Directory (AD CS)**. Accepter l'ajout des
   fonctionnalités requises proposées automatiquement.
3. À l'écran **Services de rôle**, cocher **Autorité de certification**.
4. Terminer l'assistant et lancer l'installation.
5. Une fois l'installation terminée, cliquer sur le lien **« Configurer les
   services de certificats Active Directory sur ce serveur »** (ou l'icône
   ⚑ en haut du Gestionnaire de serveur si la fenêtre a été fermée).
6. Dans l'assistant de configuration :
   - **Services de rôle** : Autorité de certification.
   - **Type d'installation** : Autorité de certification d'entreprise.
   - **Type d'AC** : AC racine.
   - **Clé privée** : Créer une nouvelle clé privée.
   - Chiffrement, nom et validité : laisser les valeurs par défaut.
   - Cliquer **Configurer**.

Le contrôleur de domaine reçoit alors automatiquement (autoenrollment) un
certificat adapté à LDAPS. Ça peut prendre quelques minutes à se propager ;
**si LDAPS ne répond toujours pas après 5-10 minutes, redémarrez le
contrôleur de domaine** pour forcer la reprise du certificat.

### Renseigner le certificat de cette autorité dans EduSync AD

Une fois LDAPS actif, EduSync AD refusera quand même de valider le
certificat tant qu'il ne connaît pas cette autorité interne — c'est
attendu. Il faut lui fournir le certificat racine :

**Sur le contrôleur de domaine**, ouvrir une invite de commandes en
administrateur et exécuter :

```
certutil -ca.cert C:\ca_root.cer
certutil -encode C:\ca_root.cer C:\ca_root_pem.cer
```

La première commande exporte le certificat de l'autorité en binaire ; la
seconde produit le fichier texte au format PEM (`-----BEGIN CERTIFICATE-----`)
qu'il faut réellement utiliser — c'est **`ca_root_pem.cer`**, pas
`ca_root.cer`.

Transférez ce fichier vers le poste qui exécute EduSync AD (clé USB,
partage réseau…), puis dans l'écran de connexion :

1. Champ **Certificat CA (optionnel)** → **Parcourir…** → sélectionnez le
   fichier `ca_root_pem.cer`.
2. Laissez la case **Vérifier le certificat du contrôleur en LDAPS** cochée.
3. Connectez-vous — la connexion devrait maintenant passer en LDAPS avec
   une vérification d'identité réelle du contrôleur.

### Solution rapide (dépannage / labo de test) — désactiver la vérification

Si vous ne pouvez pas récupérer le certificat immédiatement (test rapide,
labo isolé sans accès administrateur au contrôleur), décochez **Vérifier le
certificat du contrôleur en LDAPS** et laissez le champ certificat vide. La
connexion reste chiffrée (LDAPS actif), mais l'identité du contrôleur de
domaine n'est plus vérifiée — un tiers capable d'intercepter le trafic
réseau entre le poste et le contrôleur pourrait alors se faire passer pour
lui.

> **N'utilisez cette option que sur un réseau de confiance** (labo isolé,
> réseau local sans tiers non fiable). Sur le réseau d'un établissement
> scolaire en production, privilégiez toujours la solution recommandée
> ci-dessus.
