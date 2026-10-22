# 🧪 Environnement de test Active Directory (Samba AD DC)

> **Référence** : `ROADMAP_GO_MIGRATION.md` — §5 « Environnement de test AD » et
> §3 « Architecture cible ».
> **Fichiers** : `Dockerfile.samba-ad` et `provision-ad.sh` (racine du dépôt),
> ce guide dans `docs/`.
>
> Cet AD de test sert aux tests d'intégration Go (`testcontainers-go`), aux
> tests manuels CLI/UI et au développement quotidien. C'est un **vrai
> Active Directory** (Samba 4) : LDAP, LDAPS, Kerberos, DNS, GPO,
> politiques de mots de passe granulaires, schéma AD.

---

## 1. Ce que contient l'image

| Élément | Détail |
|---|---|
| Base | Ubuntu 24.04 (LTS) |
| Moteur | Samba 4.19 (rôle *domain controller*), Winbind, Kerberos, BIND9 (outillage), `ldap-utils`, OpenSSL |
| Domaine principal | **`EDUSYNC.LOCAL`** (realm identique, NetBIOS `EDUSYNC`) |
| Domaine secondaire | **`ANNEXE.LOCAL`** (optionnel, variable `PROVISION_ANNEXE=1`) |
| Provisioning | `provision-ad.sh` (ENTRYPOINT), au **premier** démarrage uniquement |
| Portées exposées | 389, 636, 88/tcp+udp, 53/tcp+udp, 445 |
| Santé | `HEALTHCHECK` = `samba-tool domain level show` **et** port 389 joignable |

---

## 2. Prérequis

* Docker installé et daemon actif (`docker version` répond).
* **`--cap-add SYS_ADMIN` est obligatoire** (voir §11) : Samba écrit des ACL
  NT (`xattr security.*`) sur le partage SYSVOL lors de la provision.

---

## 3. Construire l'image

```bash
docker build -f Dockerfile.samba-ad -t edusync-ad-samba .
```

* Première construction : 2 à 5 min (téléchargement des paquets Samba).
* Constructions suivantes : quelques secondes (couche apt en cache).

---

## 4. Démarrer le domaine de test

```bash
docker run -d \
  --name edusync-ad-test \
  --hostname edusync-dc \
  --cap-add SYS_ADMIN \
  -p 10389:389   \
  -p 10636:636   \
  -p 10888:88/udp \
  -p 10088:88/tcp \
  -p 10445:445   \
  -p 10053:53/udp \
  -p 10053:53/tcp \
  edusync-ad-samba
```

Attendre la fin du provisioning :

```bash
docker logs -f edusync-ad-test | grep --line-buffered "Samba AD DC ready"
# ou simplement :
docker logs edusync-ad-test | tail -20
```

**Le provisioning est terminé quand la dernière ligne vaut exactement :**

```
Samba AD DC ready
```

| Passage | Durée mesurée (voir §13) |
|---|---|
| Premier démarrage (provision + 50 comptes + GPO + PSO) | **45 à 50 s** |
| Relance / `docker restart` (déjà provisionné) | **2 à 5 s** |

| Port hôte | Port conteneur | Service |
|---|---|---|
| 10389 | 389 | LDAP (clair + StartTLS) |
| 10636 | 636 | **LDAPS** (certificat auto-signé) |
| 10088 / 10888 | 88/tcp / 88/udp | Kerberos (KDC) |
| 10053 | 53/tcp + 53/udp | DNS du domaine (zone `EDUSYNC.LOCAL`) |
| 10445 | 445 | SMB/CIFS (SYSVOL, NETLOGON) |

> Les numéros de port hôte (10389, 10636…) sont libres de choix : seule la
> colonne « port conteneur » est imposée.

---

## 5. Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `AD_REALM` | `EDUSYNC.LOCAL` | Realm/domaine à provisionner |
| `AD_ADMIN_PASSWORD` | `TestPass123` | Mot de passe du compte `Administrator` |
| `AD_DOMAIN` | 1ᵉʳ composant du realm | Nom NetBIOS (`EDUSYNC`) |
| `PROVISION_ANNEXE` | `0` | `1` → provisionne le **second domaine** `ANNEXE.LOCAL` (voir §8) |
| `PROVISION_FORCE` | `0` | `1` → recrée les objets de test même s'ils existent |
| `DNS_FORWARDER` | *(celui détecté à la provision)* | Force `dns forwarder` dans `smb.conf` (ex. `1.1.1.1`) |

Exemple :

```bash
docker run -d --name ad-custom --hostname ad-dc --cap-add SYS_ADMIN \
  -e AD_ADMIN_PASSWORD='Admin2026!' -e DNS_FORWARDER=1.1.1.1 \
  -p 10389:389 -p 10636:636 edusync-ad-samba
```

> ⚠️ Si `AD_ADMIN_PASSWORD` change, **le healthcheck reste valable** (il ne
> dépend d'aucun identifiant), mais toutes les commandes d'exemple de ce guide
> doivent utiliser le nouveau mot de passe.

---

## 6. Vérifications

### 6.1 État de santé

```bash
docker inspect -f '{{.State.Health.Status}}' edusync-ad-test   # → healthy
docker inspect -f '{{.State.Health.Log}}'   edusync-ad-test    # derniers checks
```

### 6.2 LDAP clair (port 389)

L'accès **anonyme est refusé** : il faut toujours passer un identifiant.

```bash
# 25 élèves
ldapsearch -x -H ldap://localhost:10389 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" "(objectClass=user)" dn | grep -c "^dn: CN=eleve"

# 25 personnels
ldapsearch -x -H ldap://localhost:10389 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" "(objectClass=user)" dn | grep -c "^dn: CN=perso"

# 5 OU de test (+ « Domain Controllers » créée par Samba)
ldapsearch -x -H ldap://localhost:10389 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" -s one "(objectClass=organizationalUnit)" ou
```

Résultat attendu : `25`, `25`, puis

```
ou: Groupes
ou: Domain Controllers
ou: Personnels
ou: Services
ou: Eleves
ou: Archive
```

DN d'un compte (prévisible, `CN` = `sAMAccountName`) :

```
CN=eleve01,OU=Eleves,DC=EDUSYNC,DC=LOCAL
CN=perso01,OU=Personnels,DC=EDUSYNC,DC=LOCAL
CN=Classe_6e_A,OU=Groupes,DC=EDUSYNC,DC=LOCAL
```

Depuis **dans** le conteneur, utilisez `ldap://127.0.0.1:389` (ou
`ldaps://localhost:636`) :

```bash
docker exec edusync-ad-test ldapsearch -x -H ldap://127.0.0.1:389 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" "(sAMAccountName=eleve01)" dn givenName surname
```

### 6.3 Accès anonyme (refusé — comportement voulu)

```bash
ldapsearch -x -H ldap://localhost:10389 \
  -b "DC=EDUSYNC,DC=LOCAL" "(sAMAccountName=eleve01)" dn
```

```
result: 1 Operations error
text: 00002020: Operation unavailable without authentication
```

### 6.4 LDAPS (port 636)

```bash
docker exec edusync-ad-test ldapsearch -x -H ldaps://localhost:636 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" "(objectClass=user)" dn | grep -c "^dn: CN=eleve"
# → 25   (le conteneur fait déjà confiance à son propre CA, cf. §7)
```

### 6.5 `samba-tool`

```bash
docker exec edusync-ad-test samba-tool user list | sort | head
docker exec edusync-ad-test samba-tool ou list
docker exec edusync-ad-test samba-tool group list | grep -E 'Classe_|RBAC_|^(IT|Enseignants|Administration)$'
docker exec edusync-ad-test samba-tool group listmembers Classe_6e_A
docker exec edusync-ad-test samba-tool gpo listall
docker exec edusync-ad-test samba-tool gpo getlink "OU=Eleves,DC=EDUSYNC,DC=LOCAL"
docker exec edusync-ad-test samba-tool domain passwordsettings show
docker exec edusync-ad-test samba-tool domain passwordsettings pso list
docker exec edusync-ad-test samba-tool domain passwordsettings pso show-user eleve01
docker exec edusync-ad-test samba-tool dbcheck
```

> `samba-tool` s'exécute en local sur la base AD du conteneur : aucune
> identification n'est nécessaire à l'intérieur du conteneur.

### 6.6 Kerberos

```bash
docker exec -it edusync-ad-test bash -c \
  'echo TestPass123 | kinit Administrator@EDUSYNC.LOCAL && klist'
```

```
Default principal: Administrator@EDUSYNC.LOCAL
krbtgt/EDUSYNC.LOCAL@EDUSYNC.LOCAL
```

---

## 7. LDAPS : certificat auto-signé

| Élément | Valeur |
|---|---|
| Certificat | `/var/lib/samba/private/tls/cert.pem` (auto-signé, RSA 2048, 10 ans) |
| Clé privée | `/var/lib/samba/private/tls/key.pem` |
| **CA** | `/var/lib/samba/private/tls/ca.pem` (copie du certificat lui-même) |
| Émis pour | `CN=<hostname>` + `subjectAltName` : `DNS:<hostname>`, `DNS:localhost`, `DNS:edusync-dc`, `DNS:EDUSYNC.LOCAL`, `DNS:edusync.local`, `IP:127.0.0.1`, `IP:<IP du conteneur>` |
| Paramètres | `tls enabled = yes` + les 3 chemins ci-dessus dans `smb.conf` `[global]` |
| Magasin système | `/usr/local/share/ca-certificates/edusync-ad.crt` (le conteneur fait confiance à son CA) |

### 7.1 Extraire le CA pour les clients extérieurs

```bash
docker cp edusync-ad-test:/var/lib/samba/private/tls/ca.pem ./edusync-ad-ca.pem
```

### 7.2 Faire confiance au CA

**OpenLDAP (`ldapsearch`, `ldapmodify`) :**

```bash
# méthode 1 : CA explicite (recommandée)
LDAPTLS_CACERT=./edusync-ad-ca.pem ldapsearch -x -H ldaps://localhost:10636 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 \
  -b "DC=EDUSYNC,DC=LOCAL" "(objectClass=user)" dn | grep -c "^dn: CN=eleve"

# méthode 2 : désactiver la vérification (tests jetables uniquement)
LDAPTLS_REQCERT=never ldapsearch -x -H ldaps://localhost:10636 \
  -D "Administrator@EDUSYNC.LOCAL" -w TestPass123 -b "DC=EDUSYNC,DC=LOCAL" dn

# méthode 3 : le faire confiance au système (Linux)
sudo cp ./edusync-ad-ca.pem /usr/local/share/ca-certificates/edusync-ad-ca.crt
sudo update-ca-certificates
```

**Go (go-ldap v3) :**

```go
pool := x509.NewCertPool()
pem, err := os.ReadFile("edusync-ad-ca.pem")
if err != nil {
    return fmt.Errorf("ad: charger le CA: %w", err)
}
if !pool.AppendCertsFromPEM(pem) {
    return fmt.Errorf("ad: CA invalide")
}
tlsCfg := &tls.Config{
    RootCAs:    pool,
    ServerName: "edusync-dc", // ou "localhost" si connexion via port publié
}
// LDAP := fmt.Sprintf("ldaps://localhost:%d", mappedPort)
```

> En phase de démarrage des tests, `InsecureSkipVerify: true` est acceptable
> **uniquement** pour valider le chemin LDAP avant le chargement du CA.

### 7.3 Vérifier le certificat depuis l'hôte

```bash
echo | openssl s_client -connect 127.0.0.1:10636 -CAfile ./edusync-ad-ca.pem 2>/dev/null | grep "Verify return code"
# → Verify return code: 0 (ok)
```

---

## 8. Second domaine `ANNEXE.LOCAL` (tests multisite)

Le second domaine tourne dans **un deuxième conteneur** (un AD DC ne peut pas
héberger deux domaines) :

```bash
docker run -d \
  --name edusync-annexe \
  --hostname annexe-dc \
  --cap-add SYS_ADMIN \
  -e PROVISION_ANNEXE=1 \
  -p 20389:389 -p 20636:636 \
  edusync-ad-samba
```

* Realm : `ANNEXE.LOCAL`, base DN : `DC=ANNEXE,DC=LOCAL`
* Identiques : `Administrator` / `TestPass123`, arborescence OU, 50 comptes,
  groupes, GPO, PSO (le jeu de données complet est donc dupliqué).
* Les deux conteneurs cohabitent sans conflit (chaque réseau de conteneur est
  isolé).

Vérification :

```bash
docker exec edusync-annexe ldapsearch -x -H ldap://127.0.0.1:389 \
  -D "Administrator@ANNEXE.LOCAL" -w TestPass123 \
  -b "DC=ANNEXE,DC=LOCAL" "(objectClass=user)" dn | grep -c "^dn: CN=eleve"   # → 25
```

---

## 9. Relance, réinitialisation, nettoyage

| Besoin | Commande |
|---|---|
| Relance propre (idempotent, ~3 s) | `docker restart edusync-ad-test` |
| Recréer les objets de test sans re-provisionner | `docker rm -f edusync-ad-test` puis relancer, ou `-e PROVISION_FORCE=1` |
| Repartir de zéro (domaine neuf) | `docker rm -f edusync-ad-test` puis relancer la commande §4 |
| Voir les logs | `docker logs -f edusync-ad-test` (les logs Samba sont en stdout) |
| Shell dans le conteneur | `docker exec -it edusync-ad-test bash` |
| Libérer l'espace (image + conteneurs orphelins) | `docker rm -f edusync-ad-test edusync-annexe; docker rmi edusync-ad-samba; docker image prune -f` |

Les données du domaine vivent dans la couche écrite du conteneur : **`docker rm`
efface définitivement le domaine**. Pour conserver un domaine entre deux
sessions, montez un volume sur `/var/lib/samba` :

```bash
docker volume create edusync-ad-data
docker run -d --name edusync-ad-test --cap-add SYS_ADMIN \
  -v edusync-ad-data:/var/lib/samba -p 10389:389 -p 10636:636 edusync-ad-samba
```

---

## 10. Utilisation depuis les tests Go (`testcontainers-go`)

Éléments d'attente à coder dans `internal/testutil/samba.go` (la roadmap
prévoit `NewSambaADContainer(ctx, t)`) :

```go
req := testcontainers.ContainerRequest{
    FromDockerfile: testcontainers.FromDockerFile{
        Dockerfile: "Dockerfile.samba-ad",
        Repo:       ".",
    },
    Hostname:     "edusync-dc",
    CapAdd:       []string{"SYS_ADMIN"}, // indispensable
    ExposedPorts: []string{"389/tcp", "636/tcp", "88/udp", "53/udp", "445/tcp"},
    WaitingFor: wait.ForLog("Samba AD DC ready").
        WithStartupTimeout(3 * time.Minute),
}
```

À retourner dans l'`ADConfig` de test :

| Champ | Valeur |
|---|---|
| Domaine | `EDUSYNC.LOCAL` |
| Base DN | `DC=EDUSYNC,DC=LOCAL` |
| Contrôleur | `edusync-dc` (ou `localhost` + port publié) |
| LDAP / LDAPS | `ldap://…:1389` / `ldaps://…:1366` (ports publiés) |
| Compte | `Administrator` / `TestPass123` (bind `Administrator@EDUSYNC.LOCAL`) |
| CA | `ca.pem` récupéré par `container.CopyFileFromContainer` |

---

## 11. Dépannage (troubleshooting)

### `set_nt_acl … NT_STATUS_ACCESS_DENIED` pendant la provision

```
Provision failed … setsysvolacl … fset_nt_acl returned NT_STATUS_ACCESS_DENIED.
```

**Cause** : `CAP_SYS_ADMIN` manquant → les xattr `security.*` sont refusés.
**Correction** : `--cap-add SYS_ADMIN` (ou `--privileged`). Le script détecte
un provisioning interrompu et repart proprement au démarrage suivant.

### `ldap_bind: Strong(er) authentication required (8)`

```
additional info: BindSimple: Transport encryption required.
```

**Cause** : le paramètre `ldap server require strong auth` vaut `Yes`
(défaut Samba). **Correction** : le script écrit
`ldap server require strong auth = no` dans `smb.conf` (aligné sur le défaut
Windows « LDAP server signing = None »). Si vous modifiez `smb.conf` à la main,
redémarrez : `docker restart edusync-ad-test`.

### Port 53 ou 389 déjà utilisés sur l'hôte

```
Bind for 0.0.0.0:53 failed: port is already allocated
```

* `53` : souvent **systemd-resolved** (`sudo ss -ulnp | grep :53` →
  `127.0.0.53:53`). Ne publiez pas le 53 hôte, ou utilisez un autre numéro :
  `-p 10053:53` (c'est ce que fait le §4).
* `389` : un autre annuaire/AD tourne déjà → changez le port hôte
  (`-p 20389:389`) ou arrêtez-le.
* Aucun de ces ports n'est requis **à l'intérieur** du conteneur : ils ne
  conflictent que lors de la publication (`-p`).

Pour vérifier ce qui écoute sur l'hôte :

```bash
sudo ss -tlnp | grep -E ':(53|389|636|445)\b'
```

### Résolution DNS et découverte du DC

* À chaque démarrage, `provision-ad.sh` réécrit `/etc/resolv.conf` vers
  `127.0.0.1` (le DNS intégré de Samba) : la zone `EDUSYNC.LOCAL` (enregistrements
  SRV `_ldap._tcp`, `_kerberos._tcp`) n'est résolue nulle part ailleurs.
* Les commandes `samba-tool gpo …` utilisent la découverte CLDAP : sans cette
  résolution elles échouent avec `The object name is not found`.
* Depuis l'hôte, interrogez le DNS publié : `dig @127.0.0.1 -p 10053 SRV _ldap._tcp.edusync.local`.

### `samba_dnsupdate: ERROR … Record already exists`

**Bénin** : Samba tente d'actualiser les enregistrements DNS créés à la
provision (`WERR_DNS_ERROR_RECORD_ALREADY_EXISTS`). Cela n'empêche ni le
démarrage ni les tests.

### Le conteneur reste `unhealthy` pendant le premier démarrage

Normal : le `HEALTHCHECK` a un `start-period` de 120 s, le provisioning prend
~45 s. Vérifiez plutôt l'apparition de `Samba AD DC ready`.

### Relance propre après un provisioning raté

```bash
docker rm -f edusync-ad-test       # le domaine partiel est dans le conteneur
docker run ...                     # repart de zéro (§4)
```

Le script nettoie aussi de lui-même un provisioning interrompu (fichiers
`/var/lib/samba/private` sans le marqueur `.edusync_provision_ok`).

### Libérer de l'espace disque

```bash
docker ps -a                       # voir ce qui traîne
docker rm -f edusync-ad-test edusync-annexe
docker rmi edusync-ad-samba
docker image prune -f              # couches orphelines
docker system df                   # bilan
```

---

## 12. Récapitulatif

| Élément | Valeur |
|---|---|
| Domaine (realm) | **`EDUSYNC.LOCAL`** (NetBIOS `EDUSYNC`) |
| Second domaine (optionnel) | **`ANNEXE.LOCAL`** (`PROVISION_ANNEXE=1`) |
| Base DN | **`DC=EDUSYNC,DC=LOCAL`** |
| Contrôleur (hostname) | `edusync-dc` |
| Administrateur | **`Administrator@EDUSYNC.LOCAL`** / **`TestPass123`** |
| Comptes de test | **50** : `eleve01`…`eleve25` (OU=Eleves), `perso01`…`perso25` (OU=Personnels), mdp `TestPass123`, `userPrincipalName` `…@EDUSYNC.LOCAL`, mail `…@edusync.local` |
| Arborescence OU | `OU=Eleves`, `OU=Personnels`, `OU=Groupes`, `OU=Services`, `OU=Archive` |
| Groupes de classes | `Classe_6e_A`, `Classe_6e_B`, `Classe_5e_A`, `Classe_4e_A`, `Classe_3e_A` (5 élèves + 2 enseignants chacun) |
| Groupes de service | `Enseignants` (12), `Administration` (8), `IT` (5) |
| Délégations RBAC | `RBAC_Direction_Tous` (dont `RBAC_Scolarite_Eleves` et `RBAC_RH_Personnels` en imbrication), `RBAC_Scolarite_Eleves`, `RBAC_RH_Personnels`, `RBAC_IT_Services`, `RBAC_Lecture_Archive` |
| GPO | `EduSync - Baseline test` (liée à `OU=Eleves`) + 2 GPO par défaut Windows |
| PSO | `PSO_Eleves_Standard` (priorité 10, 8 car. min, 90 j, lockout 5) sur les 5 classes ; `PSO_Personnels_Standard` (priorité 20, 12 car. min, 180 j) sur `Enseignants`, `Administration`, `IT` |
| Ports LDAP / LDAPS | **389** / **636** (certificat auto-signé) |
| Ports SMB / DNS / Kerberos | **445** / **53** (tcp+udp) / **88** (tcp+udp) |
| Anonyme | **Refusé** (`Operation unavailable without authentication`) |
| Politique domaine | complexité **ON**, historique 24, longueur min 7, âge min 1 j, **expiration désactivée (0 j)**, lockout désactivé |
| Message de fin de provisioning | `Samba AD DC ready` |

### Journal attendu au premier démarrage (extrait réel)

```
[provision-ad] Provisionnement du domaine EDUSYNC.LOCAL (domaine NetBIOS EDUSYNC, serveur edusync-dc) ...
[provision-ad] Domaine EDUSYNC.LOCAL provisionné (Administrator / TestPass123)
[provision-ad]   OU=[Eleves] créée
...
[provision-ad]   Compte [CN=eleve24,OU=Eleves,DC=EDUSYNC,DC=LOCAL] créé (Éloïse Barthélémy)
...
[provision-ad] Génération du certificat auto-signé LDAPS (10 ans, RSA 2048)
[provision-ad] LDAPS activé : port 636 (tls enabled = yes)
[provision-ad] Liaison simple LDAP autorisée en clair (ldap server require strong auth = no)
[provision-ad] LDAP répond sur le port 389
[provision-ad] LDAPS répond sur le port 636
[provision-ad] Résolution DNS : 10.2.0.1 -> 127.0.0.1 (DNS Samba, zone EDUSYNC.LOCAL)
[provision-ad] GPO créée : [EduSync - Baseline test] {2C5ACEDC-…}
[provision-ad]   liée à OU=Eleves
[provision-ad] PSO créé : [PSO_Eleves_Standard] (priorité 10, 8 car. min, lockout 5)
[provision-ad] OUs de test ........ 5/5 (Eleves, Personnels, Groupes, Services, Archive)
[provision-ad] Comptes élèves ..... 25/25 (CN=eleveNN,OU=Eleves,DC=EDUSYNC,DC=LOCAL)
[provision-ad] Comptes personnels . 25/25 (CN=persoNN,OU=Personnels,DC=EDUSYNC,DC=LOCAL)
[provision-ad] Provisionnement terminé en 46s
Samba AD DC ready
```

---

## 13. État connu

Validé le **12 octobre 2026** avec `ubuntu:24.04` + `samba 4.19.5`, Docker 29.

**Contrôles passés** (à l'identique des exemples ci-dessus) :

| Contrôle | Résultat |
|---|---|
| `docker build -f Dockerfile.samba-ad` | OK (1ʳᵉ tentative) |
| Premier démarrage → `Samba AD DC ready` | OK en **49 s** |
| `ldapsearch` LDAP 389 (élèves / personnels / OU) | **25 / 25 / 5** |
| `ldapsearch` LDAPS 636 (élèves / personnels) | **25 / 25** |
| Accès anonyme | refusé (comportement voulu) |
| Port 636 : certificat + `Verify return code: 0` avec le CA extrait | OK |
| Ports hôte 10389 / 10636 / 10445 joignables | OK |
| `HEALTHCHECK` | `healthy` |
| Relance (`docker restart`) | OK en **2 à 5 s**, aucun objet recréé |
| GPO créée + liée à `OU=Eleves` | OK |
| 2 PSO créés + appliqués (`pso show-user eleve01`) | OK |
| Kerberos (`kinit` + SRV `_kerberos._tcp`, `_ldap._tcp`) | OK |
| 2ᵉ domaine `ANNEXE.LOCAL` (`PROVISION_ANNEXE=1`) | OK en 46 s, 25/25 comptes, LDAPS OK |

**Écarts assumés par rapport à un Windows Server / au défaut Samba :**

1. **`ldap server require strong auth = no`** : Samba exige par défaut TLS ou
   SASL (erreur 8 sur une simple bind en clair) ; Windows accepte la liaison
   simple en clair. Sans ce réglage, le test « repli LDAP 389 » de la roadmap
   serait impossible.
2. **Expiration des mots de passe désactivée** (`max-pwd-age = 0` au niveau
   domaine, contre 42 j par défaut) : un conteneur conservé en cache CI ne
   doit jamais voir ses mots de passe expirer. Les PSO gardent des durées
   réelles (90 j / 180 j).
3. **GPO `EduSync - Baseline test` vide** : `samba-tool gpo create` crée
   l'objet et sa structure SYSVOL mais aucun réglage ; utilisez
   `samba-tool gpo load` si un test doit lire un vrai contenu ADMX.
4. **`/etc/resolv.conf` réécrit** à chaque démarrage vers `127.0.0.1`
   (DNS Samba) ; le forwarder externe reste celui détecté à la provision
   (`dns forwarder` dans `smb.conf`, surchargeable par `DNS_FORWARDER`).
5. **`ldbsearch`/`ldbadd` ne sont pas installés** (paquet `ldb-utils`) :
   utilisez `ldapsearch` ou `samba-tool`.
6. **Bruit de logs bénin** : `samba_dnsupdate … Record already exists` au
   démarrage (les enregistrements existent déjà depuis la provision).
7. **`task ad:start` / `task ad:reset` / `task ad:shell`** sont câblés dans
   `Taskfile.yml` (section AD de test). Les commandes Docker brutes de ce
   guide restent la référence pour le détail de ce qu'elles exécutent.
8. Le réseau `--network` par défaut (bridge) convient ; sur un réseau
   *user-defined*, le DNS intégré de Docker (`127.0.0.11`) et celui de Samba
   coexistent (vérifié : le bind du port 53 fonctionne dans les deux cas).
