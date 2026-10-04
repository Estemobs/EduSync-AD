# Release EduSync AD 1.16.0

## 🐛 Correction critique

### Fixed: `AttributeError: 'APIPage' object has no attribute '_on_test_api_key'`

**Problème** : L'application crashait au démarrage sur l'onglet "API REST" → "Clés API" avec l'erreur :
```
AttributeError: 'APIPage' object has no attribute '_on_test_api_key'
```

**Cause** : Le Flatpak distribué était basé sur une version du code source antérieure à l'ajout de la méthode `_on_test_api_key` (ajoutée dans le code source mais pas reconstruite dans le Flatpak).

**Solution** : La méthode `_on_test_api_key` est maintenant incluse dans le package (ligne 239 de `src/edusync_ad/ui/api_page.py`). Elle permet de tester une clé API en interrogeant l'endpoint `/api/v1/health` du serveur REST local.

---

## 📦 Artefacts de release

| Fichier | SHA256 | Taille |
|---------|--------|--------|
| `edusync_ad-1.16.0.tar.gz` | `bdc84770a024a1cc774a883c4e101bdd55bbfcf5140b285fb15a5f15f65cf427` | 317 KB |
| `edusync_ad-1.16.0-py3-none-any.whl` | `0e324d02e8da03524dade1b572a4c8ee24c90c81817ef4edaac8b70be1bf372c` | 376 KB |

---

## 🔧 Reconstruction du Flatpak

Pour produire un Flatpak mis à jour avec cette version :

### 1. Mettre à jour le manifeste Flatpak

Dans `org.edusync.AD.yml` (ou votre fichier manifeste) :

```yaml
modules:
  - name: edusync-ad
    buildsystem: simple
    build-commands:
      - pip install --no-index --find-links=/home/builder/sources/edusync-ad-1.16.0 edusync-ad==1.16.0
    sources:
      - type: archive
        url: https://github.com/<VOTRE_REPO>/edusync-ad/archive/refs/tags/v1.16.0.tar.gz
        sha256: bdc84770a024a1cc774a883c4e101bdd55bbfcf5140b285fb15a5f15f65cf427
        dest-filename: edusync-ad-1.16.0.tar.gz
```

### 2. Construire
```bash
flatpak-builder --force-clean build-dir org.edusync.AD.yml
```

### 3. Tester localement
```bash
flatpak-builder --run build-dir org.edusync.AD.yml edusync-ad
```

### 4. Créer le bundle de distribution
```bash
flatpak build-bundle build-dir edusync-ad-1.16.0.flatpak org.edusync.AD
```

### 5. Publier sur Flathub
Soumettre une PR sur https://github.com/flathub/org.edusync.AD avec le manifeste mis à jour.

---

## ✅ Checklist de validation post-release

- [ ] `flatpak run org.edusync.AD` démarre sans erreur
- [ ] Onglet **API REST** → **Clés API** accessible
- [ ] Bouton **"Tester l'API"** fonctionne (ne crash plus)
- [ ] Test de clé valide → affiche "✅ Clé API valide"
- [ ] Test de clé invalide → affiche erreur HTTP appropriée
- [ ] Serveur API non démarré → affiche "Serveur inaccessible" explicite

---

## 📝 Note pour les utilisateurs Windows

Le package wheel (`.whl`) est multiplateforme. Pour installer :
```cmd
pip install edusync_ad-1.16.0-py3-none-any.whl
```

Le correctif s'applique aussi aux installations `pip` / `pipx` / environnements virtuels.

---

## 🔄 Prochaines étapes

1. **Tagger la release** : `git tag v1.16.0 && git push --tags`
2. **Publier sur PyPI** (optionnel) : `twine upload dist/*`
3. **Mettre à jour Flathub** via PR sur le manifeste
4. **Communiquer** la mise à jour aux utilisateurs
