# Release EduSync AD 1.16.1

## 🐛 Corrections critiques (APIPage)

### Fixed: `AttributeError: 'APIPage' object has no attribute '_on_open_doc'`
**Problème** : Crash au démarrage sur l'onglet **API REST → Endpoints** avec l'erreur :
```
AttributeError: 'APIPage' object has no attribute '_on_open_doc'
```
**Cause** : Le bouton "Doc OpenAPI" était connecté à une méthode inexistante.
**Fix** : Méthode `_on_open_doc()` ajoutée (ouvre la doc Swagger sur `http://127.0.0.1:8080/docs`).

---

### Fixed: `AttributeError: 'APIPage' object has no attribute '_on_test_api_key'`
**Problème** : Crash sur l'onglet **API REST → Clés API** en cliquant "Tester l'API" :
```
AttributeError: 'APIPage' object has no attribute '_on_test_api_key'
```
**Cause** : Méthode manquante pour tester une clé API via l'endpoint `/api/v1/health`.
**Fix** : Méthode `_on_test_api_key()` ajoutée avec gestion d'erreurs complète (clé invalide, serveur down, timeout).

---

### Fixed: Mauvaise connexion webhook
**Problème** : Le bouton "Créer webhook" appelait `_on_webhook_create` au lieu de `_on_create_webhook`.
**Fix** : Connexion corrigée vers la bonne méthode existante.

---

## 📦 Artefacts de release

| Fichier | SHA256 | Taille |
|---------|--------|--------|
| `edusync_ad-1.16.1.tar.gz` | `e55e6c4b98c3ff77e688438d9fb85015b26a46ee9f41bab3b593a4ad701b2e82` | 317 KB |
| `edusync_ad-1.16.1-py3-none-any.whl` | `3bc720e61c3e49b2dbe503969e667eb5566ff311ac6028ed1a8ef7fc8482bc22` | 376 KB |

---

## ✅ Checklist de validation

- [ ] `flatpak run org.edusync.AD` démarre sans erreur
- [ ] Onglet **API REST → Clés API** accessible
- [ ] Bouton **"Tester l'API"** fonctionne (ne crash plus)
- [ ] Test clé valide → affiche "✅ Clé API valide"
- [ ] Test clé invalide → affiche erreur HTTP appropriée
- [ ] Serveur API non démarré → affiche "Serveur inaccessible" explicite
- [ ] Onglet **API REST → Endpoints** accessible
- [ ] Bouton **"Doc OpenAPI"** ouvre le navigateur (ne crash plus)
- [ ] Onglet **API REST → Webhooks** : bouton "Créer webhook" fonctionne

---

## 🔧 Reconstruction du Flatpak

Pour produire un Flatpak mis à jour avec cette version :

### 1. Mettre à jour le manifeste Flatpak
Dans `org.edusync.AD.yml` :
```yaml
modules:
  - name: edusync-ad
    buildsystem: simple
    build-commands:
      - pip install --no-index --find-links=/home/builder/sources/edusync-ad-1.16.1 edusync-ad==1.16.1
    sources:
      - type: archive
        url: https://github.com/<VOTRE_REPO>/edusync-ad/archive/refs/tags/v1.16.1.tar.gz
        sha256: <NOUVEAU_SHA256>
        dest-filename: edusync-ad-1.16.1.tar.gz
```

### 2. Construire
```bash
flatpak-builder --force-clean build-dir packaging/org.edusync.AD.yml
```

### 3. Tester localement
```bash
flatpak-builder --run build-dir packaging/org.edusync.AD.yml edusync-ad
```

### 4. Créer le bundle de distribution
```bash
flatpak build-bundle build-dir edusync-ad-1.16.1.flatpak org.edusync.AD
```

### 5. Publier sur Flathub
Soumettre une PR sur https://github.com/flathub/org.edusync.AD avec le manifeste mis à jour.

---

## 📝 Note pour les utilisateurs Windows

Le package wheel (`.whl`) est multiplateforme. Pour installer :
```cmd
pip install edusync_ad-1.16.1-py3-none-any.whl
```

Le correctif s'applique aussi aux installations `pip` / `pipx` / environnements virtuels.

---

## 🔄 Prochaines étapes

1. **Tagger la release** : `git tag v1.16.1 && git push --tags`
2. **Publier sur PyPI** (optionnel) : `twine upload dist/*`
3. **Mettre à jour Flathub** via PR sur le manifeste
4. **Communiquer** la mise à jour aux utilisateurs