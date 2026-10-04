# Release EduSync AD 1.17.1

## ✨ Nouvelles fonctionnalités majeures

### Fenêtre adaptative à l'écran
- **Taille automatique** : la fenêtre s'adapte à 85% de l'écran disponible au démarrage
- **Bornes intelligentes** : minimum 1000×650, maximum 1600×1000
- **Centrage automatique** : positionnée au centre de l'écran principal

### Mode plein écran natif
- **Double-clic sur la barre de titre** → bascule plein écran / fenêtre
- **Touche F11** → raccourci clavier pour plein écran
- État mémorisé pendant la session

### Guide de démarrage interactif (Wizard)
Présenté automatiquement au **premier lancement** :
1. **Bienvenue** — présentation rapide du logiciel
2. **Connexion au domaine** — paramètres LDAP/LDAPS, certificats
3. **Barre supérieure** — indicateur connexion, sélecteur domaine, mises à jour, signalement bug
4. **Barre latérale** — tous les modules organisés par catégorie
5. **Premiers pas** — paramètres comptes/mots de passe/apparence, test création comptes
6. **Raccourcis & Astuces** — F11, Ctrl+1..9, fichiers config, logs, mises à jour

### Raccourcis clavier directs
- **Ctrl+1 .. Ctrl+9** → accès direct aux 9 premiers modules de la sidebar

---

## 🐛 Corrections

### Version dynamique (fix updater)
- **Problème** : version hardcodée `1.14.0` dans `updater.py` → l'updater ne détectait jamais les nouvelles versions
- **Fix** : version lue dynamiquement via `importlib.metadata` depuis `pyproject.toml` (package installé) avec fallback développement

### Imports `__import__` cassés dans `api_page.py` (v1.16.5)
- `__import__('edusync_ad.core.webhooks').load_webhooks()` → `AttributeError: module 'edusync_ad' has no attribute 'load_webhooks'`
- Remplacés par des imports explicites en haut de fichier

---

## 📦 Artefacts de release

| Fichier | SHA256 | Taille |
|---------|--------|--------|
| `edusync_ad-1.17.1.tar.gz` | `81d67ca56c7d1725c7b19077a82bc6ff94babe5213e3f6c2eaf40720194e5f76` | 317 KB |
| `edusync_ad-1.17.1-py3-none-any.whl` | `5e7615775f148c26b5947cc63f38d2949f77e0dfaf8586f5c92ef1aba95f2769` | 376 KB |

---

## ✅ Checklist de validation

- [ ] `flatpak run org.edusync.AD` démarre sans erreur
- [ ] Fenêtre s'adapte à l'écran au lancement (85% dispo, centrée)
- [ ] **Double-clic barre de titre** → plein écran / fenêtre
- [ ] **F11** → bascule plein écran
- [ ] **Ctrl+1..9** → change de module directement
- [ ] Guide de démarrage affiché au premier lancement (6 étapes)
- [ ] Guide non réaffiché aux lancements suivants
- [ ] Onglet **API REST → Clés API** : bouton "Tester l'API" fonctionne
- [ ] Onglet **API REST → Endpoints** : bouton "Doc OpenAPI" fonctionne
- [ ] Onglet **API REST → Webhooks** : bouton "Créer webhook" fonctionne
- [ ] Mise à jour détectée correctement (version dynamique)
- [ ] Windows build : installateur .exe généré
- [ ] Linux build : .flatpak généré

---

## 🔧 Reconstruction du Flatpak

```bash
git clone https://github.com/estemobs/EduSync-AD.git
cd EduSync-AD
git checkout v1.17.1
flatpak-builder --force-clean build-dir packaging/org.edusync.AD.yml
flatpak build-bundle build-dir edusync-ad-1.17.1.flatpak org.edusync.AD
```

---

## 📝 Installation directe

```bash
pip install edusync_ad-1.17.1-py3-none-any.whl
# ou
pipx install edusync_ad-1.17.1-py3-none-any.whl
```

---

## 🔄 Prochaines étapes

1. **Tagger** : `git tag v1.17.1 && git push --tags` ✅
2. **Publier PyPI** : `twine upload dist/*`
3. **Mettre à jour Flathub** : PR sur `flathub/org.edusync.AD`
4. **Communiquer** la mise à jour aux utilisateurs