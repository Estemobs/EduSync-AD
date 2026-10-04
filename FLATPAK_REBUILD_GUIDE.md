# Guide de reconstruction du Flatpak EduSync AD

## Problème identifié
Le Flatpak installé (`org.edusync.AD`) a été construit à partir d'une version du code source **plus ancienne** qui ne contient pas la méthode `_on_test_api_key` dans la classe `APIPage`.

**Erreur observée :**
```
AttributeError: 'APIPage' object has no attribute '_on_test_api_key'
```

## Solution : Reconstruire le Flatpak à partir du code source actuel

### 1. Vérifier que le code source est correct

Le fichier `src/edusync_ad/ui/api_page.py` contient déjà la méthode `_on_test_api_key` (lignes 239-285 dans la version actuelle).

Vérification :
```bash
grep -n "_on_test_api_key" src/edusync_ad/ui/api_page.py
```
Doit retourner deux lignes : la connexion du signal (vers ligne 191) et la définition de la méthode (vers ligne 239).

### 2. Construire le package Python (wheel/sdist)

```bash
# Depuis la racine du projet
pip install build
python -m build
```

Cela crée `dist/edusync_ad-<version>.tar.gz` et `.whl`.

### 3. Mettre à jour le manifeste Flatpak

Le manifeste Flatpak (généralement `org.edusync.AD.yml` ou similaire) doit pointer vers la nouvelle version.

Exemple de section modules dans le manifeste :
```yaml
modules:
  - name: edusync-ad
    buildsystem: simple
    build-commands:
      - pip install --no-index --find-links=/home/builder/sources/edusync-ad-${VERSION} edusync-ad==${VERSION}
    sources:
      - type: archive
        url: https://github.com/<votre-repo>/edusync-ad/archive/refs/tags/v${VERSION}.tar.gz
        sha256: <nouveau_sha256>
        dest-filename: edusync-ad-${VERSION}.tar.gz
```

**Mettre à jour :**
- La version (`${VERSION}` = 1.15.1 ou supérieure)
- Le SHA256 de l'archive source

Calculer le SHA256 :
```bash
sha256sum dist/edusync_ad-1.15.1.tar.gz
```

### 4. Construire le Flatpak localement

```bash
# Installer flatpak-builder si nécessaire
sudo pacman -S flatpak-builder  # Arch/CachyOS
# ou
sudo apt install flatpak-builder  # Debian/Ubuntu

# Construire
flatpak-builder --force-clean build-dir org.edusync.AD.yml

# Tester localement
flatpak-builder --run build-dir org.edusync.AD.yml edusync-ad
```

### 5. Créer un bundle pour distribution

```bash
# Créer un repo local
flatpak build-bundle build-dir edusync-ad.flatpak org.edusync.AD

# Le fichier edusync-ad.flatpak peut être distribué
```

### 6. Publier sur Flathub (si applicable)

```bash
# Soumettre le manifeste mis à jour au repo Flathub
# Via PR sur https://github.com/flathub/org.edusync.AD
```

---

## Alternative rapide : Patch à chaud (pour tests immédiats)

Si vous ne pouvez pas reconstruire immédiatement, vous pouvez patcher le fichier installé :

### Linux (Flatpak utilisateur)
```bash
# Localiser le fichier
FLATPAK_API_PAGE="/home/$USER/.local/share/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py"
# Ou système :
FLATPAK_API_PAGE="/var/lib/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py"

# Backup
cp "$FLATPAK_API_PAGE" "$FLATPAK_API_PAGE.bak"

# Ajouter la méthode manquante après _on_generate_key
# (utilisez le patch fourni dans patch_api_page_fix.patch)
patch -p1 < patch_api_page_fix.patch
```

### Windows (Flatpak via WSL ou MSIX)
```powershell
# Le fichier se trouve généralement dans :
# C:\Users\<user>\AppData\Local\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py

# Appliquer le patch manuellement en éditant le fichier
# Insérez la méthode _on_test_api_key après _on_generate_key
```

---

## Vérification post-build

```bash
# Lancer l'application
flatpak run org.edusync.AD

# Aller dans l'onglet "API REST" → "Clés API"
# Cliquer sur "Tester l'API" → ne doit plus crasher
```

---

## Mise à jour de version

Dans `pyproject.toml` :
```toml
[project]
version = "1.16.0"  # Incrémenter
```

Puis recommencer à l'étape 2.

---

## Fichiers fournis

1. `patch_api_page_fix.patch` - Patch pour ajouter la méthode manquante
2. Ce guide (`FLATPAK_REBUILD_GUIDE.md`)