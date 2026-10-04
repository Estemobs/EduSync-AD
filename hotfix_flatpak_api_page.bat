@echo off
REM Hotfix for Flatpak EduSync AD on Windows
REM Adds missing methods and fixes wrong connections in APIPage class

echo 🔧 Hotfix Flatpak EduSync AD - Correction APIPage
echo ============================================================

REM Possible paths for Windows Flatpak installation
set "FLATPAK_PATHS="
set "FLATPAK_PATHS=%FLATPAK_PATHS% %LOCALAPPDATA%\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py"
set "FLATPAK_PATHS=%FLATPAK_PATHS% %APPDATA%\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py"
set "FLATPAK_PATHS=%FLATPAK_PATHS% C:\ProgramData\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py"

set "API_PAGE="
for %%P in (%FLATPAK_PATHS%) do (
    if exist "%%P" (
        set "API_PAGE=%%P"
        goto :FOUND
    )
)

:FOUND
if "%API_PAGE%"=="" (
    echo ❌ Fichier api_page.py introuvable dans l'installation Flatpak
    echo.
    echo Chemins recherchés:
    echo   %LOCALAPPDATA%\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py
    echo   %APPDATA%\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py
    echo   C:\ProgramData\Flatpak\app\org.edusync.AD\current\active\files\lib\python3.11\site-packages\edusync_ad\ui\api_page.py
    echo.
    echo 💡 Vous pouvez spécifier le chemin manuellement:
    echo    hotfix_flatpak_api_page.bat "C:\chemin\vers\api_page.py"
    pause
    exit /b 1
)

echo 📍 Fichier trouvé: %API_PAGE%

REM If argument provided, use it
if not "%~1"=="" (
    set "API_PAGE=%~1"
    if not exist "%API_PAGE%" (
        echo ❌ Fichier non trouvé: %API_PAGE%
        pause
        exit /b 1
    )
)

REM Create backup
echo 📦 Création du backup...
copy "%API_PAGE%" "%API_PAGE%.bak" >nul
if errorlevel 1 (
    echo ❌ Erreur lors de la création du backup
    pause
    exit /b 1
)
echo ✅ Backup créé: %API_PAGE%.bak

REM Use Python to do the patching
echo 🔧 Application des patches...
python -c "
import re
import sys

with open(r'%API_PAGE%', 'r', encoding='utf-8') as f:
    content = f.read()

original = content
changes = []

# Fix 1: _on_webhook_create -> _on_create_webhook connection
if 'btn_wh_create.clicked.connect(self._on_webhook_create)' in content:
    content = content.replace(
        'btn_wh_create.clicked.connect(self._on_webhook_create)',
        'btn_wh_create.clicked.connect(self._on_create_webhook)'
    )
    changes.append('Fixé connexion webhook: _on_webhook_create -> _on_create_webhook')

# Fix 2: Add _on_open_doc method after _on_clear_spec
if 'def _on_open_doc' not in content:
    pattern = r'(    def _on_clear_spec\(self\) -> None:\n        self\.spec_viewer\.clear\(\)\n        self\.spec_status\.setText\(\"Aucun spec généré\"\))'
    replacement = r'''\1

    def _on_open_doc(self) -> None:
        \"\"\"Ouvre la documentation OpenAPI dans le navigateur.\"\"\"
        try:
            import webbrowser
            webbrowser.open(\"http://127.0.0.1:8080/docs\")
        except Exception as exc:
            QMessageBox.warning(self, \"Erreur\", f\"Impossible d'ouvrir la doc : {exc}\")'''
    content = re.sub(pattern, replacement, content)
    if content != original:
        changes.append('Ajouté méthode _on_open_doc')

# Fix 3: Add _on_test_api_key method after _on_generate_key
if 'def _on_test_api_key' not in content:
    pattern = r'(            QMessageBox\.warning\(self, \"Erreur\", str\(exc\)\)\n        )(\n    def _on_revoke_key)'
    replacement = r'''\1
    
    def _on_test_api_key(self) -> None:
        \"\"\"Teste la clé API saisie en faisant une requête simple vers l'endpoint /health.\"\"\"
        test_key = self.test_key.text().strip()
        if not test_key:
            QMessageBox.warning(self, \"Clé manquante\", \"Entrez une clé API à tester.\")
            return

        import urllib.request
        import urllib.error
        import json

        url = \"http://127.0.0.1:8080/api/v1/health\"
        req = urllib.request.Request(url, headers={\"Authorization\": f\"Bearer {test_key}\"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode(\"utf-8\")
                    QMessageBox.information(
                        self,
                        \"Test réussi\",
                        f\"✅ Clé API valide\\n\\nRéponse du serveur :\\n{body}\",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        \"Échec du test\",
                        f\"Code HTTP {resp.status}\\nLa clé pourrait être invalide ou expirée.\",
                    )
        except urllib.error.HTTPError as exc:
            QMessageBox.warning(
                self,
                \"Échec du test\",
                f\"Erreur HTTP {exc.code} : {exc.reason}\\n\\n\"
                f\"Vérifiez que le serveur API est démarré (onglet OpenAPI → Générer le spec)\\n\"
                f\"et que la clé est correcte.\",
            )
        except urllib.error.URLError as exc:
            QMessageBox.warning(
                self,
                \"Serveur inaccessible\",
                f\"Impossible de joindre le serveur API :\\n{exc.reason}\\n\\n\"
                f\"Le serveur API REST (port 8080) doit être démarré pour tester la clé.\",
            )
        except Exception as exc:
            QMessageBox.critical(self, \"Erreur\", f\"Erreur inattendue : {exc}\")\2'''
    content = re.sub(pattern, replacement, content)
    
    if content == original:
        # Alternative pattern
        alt_pattern = r'(    def _on_generate_key\(self\) -> None:.*?QMessageBox\.warning\(self, \"Erreur\", str\(exc\)\))'
        match = re.search(alt_pattern, content, re.DOTALL)
        if match:
            insert_pos = match.end()
            method_code = '''

    def _on_test_api_key(self) -> None:
        \"\"\"Teste la clé API saisie en faisant une requête simple vers l'endpoint /health.\"\"\"
        test_key = self.test_key.text().strip()
        if not test_key:
            QMessageBox.warning(self, \"Clé manquante\", \"Entrez une clé API à tester.\")
            return

        import urllib.request
        import urllib.error
        import json

        url = \"http://127.0.0.1:8080/api/v1/health\"
        req = urllib.request.Request(url, headers={\"Authorization\": f\"Bearer {test_key}\"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode(\"utf-8\")
                    QMessageBox.information(
                        self,
                        \"Test réussi\",
                        f\"✅ Clé API valide\\n\\nRéponse du serveur :\\n{body}\",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        \"Échec du test\",
                        f\"Code HTTP {resp.status}\\nLa clé pourrait être invalide ou expirée.\",
                    )
        except urllib.error.HTTPError as exc:
            QMessageBox.warning(
                self,
                \"Échec du test\",
                f\"Erreur HTTP {exc.code} : {exc.reason}\\n\\n\"
                f\"Vérifiez que le serveur API est démarré (onglet OpenAPI → Générer le spec)\\n\"
                f\"et que la clé est correcte.\",
            )
        except urllib.error.URLError as exc:
            QMessageBox.warning(
                self,
                \"Serveur inaccessible\",
                f\"Impossible de joindre le serveur API :\\n{exc.reason}\\n\\n\"
                f\"Le serveur API REST (port 8080) doit être démarré pour tester la clé.\",
            )
        except Exception as exc:
            QMessageBox.critical(self, \"Erreur\", f\"Erreur inattendue : {exc}\")'''
            content = content[:insert_pos] + method_code + content[insert_pos:]
            changes.append('Ajouté méthode _on_test_api_key')
        else:
            print('Impossible de trouver l\'emplacement d\'insertion pour _on_test_api_key')
            sys.exit(1)
    else:
        changes.append('Ajouté méthode _on_test_api_key')

if content == original:
    print('✅ Aucune modification nécessaire - toutes les méthodes sont présentes')
    sys.exit(0)

with open(r'%API_PAGE%', 'w', encoding='utf-8') as f:
    f.write(content)

print('✅ Patches appliqués:')
for change in changes:
    print('  - ' + change)
"

if errorlevel 1 (
    echo ❌ Échec du patch
    pause
    exit /b 1
)

echo ✅ Hotfix appliqué avec succès!
echo 🔄 Relancez l'application: flatpak run org.edusync.AD
pause