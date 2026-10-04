#!/usr/bin/env python3
"""
Hotfix script to patch the installed Flatpak version of EduSync AD
Adds the missing _on_test_api_key method to APIPage class
"""
import sys
import os
import shutil
from pathlib import Path


def find_flatpak_api_page():
    """Find the api_page.py in the flatpak installation."""
    possible_paths = [
        # User installation (Linux)
        Path.home() / ".local/share/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
        # System installation (Linux)
        Path("/var/lib/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py"),
        # Windows (if running via WSL or similar)
        Path.home() / "AppData/Local/Flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
        # Alternative Windows path
        Path("/mnt/c/Users") / os.getenv("USERNAME", "") / "AppData/Local/Flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
    ]
    
    for path in possible_paths:
        if path.exists():
            return path
    return None


def has_method(content, method_name):
    """Check if method exists in the file."""
    return f"def {method_name}" in content


def patch_api_page(file_path):
    """Add the missing _on_test_api_key method after _on_generate_key."""
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if has_method(content, '_on_test_api_key'):
        print(f"✅ Méthode _on_test_api_key déjà présente dans {file_path}")
        return False
    
    # Find the position after _on_generate_key method
    # Look for the end of _on_generate_key (the line with "QMessageBox.warning(self, \"Erreur\", str(exc))" followed by blank line and "def _on_revoke_key")
    import re
    
    # Pattern: end of _on_generate_key followed by _on_revoke_key
    pattern = r'(            QMessageBox\.warning\(self, "Erreur", str\(exc\)\)\n        )(\n    def _on_revoke_key)'
    
    replacement = r'''\1
    
    def _on_test_api_key(self) -> None:
        """Teste la clé API saisie en faisant une requête simple vers l'endpoint /health."""
        test_key = self.test_key.text().strip()
        if not test_key:
            QMessageBox.warning(self, "Clé manquante", "Entrez une clé API à tester.")
            return

        # Tenter de joindre le serveur API local (port par défaut 8080)
        import urllib.request
        import urllib.error
        import json

        url = "http://127.0.0.1:8080/api/v1/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {test_key}"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8")
                    QMessageBox.information(
                        self,
                        "Test réussi",
                        f"✅ Clé API valide\n\nRéponse du serveur :\n{body}",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Échec du test",
                        f"Code HTTP {resp.status}\nLa clé pourrait être invalide ou expirée.",
                    )
        except urllib.error.HTTPError as exc:
            QMessageBox.warning(
                self,
                "Échec du test",
                f"Erreur HTTP {exc.code} : {exc.reason}\n\n"
                f"Vérifiez que le serveur API est démarré (onglet OpenAPI → Générer le spec)\n"
                f"et que la clé est correcte.",
            )
        except urllib.error.URLError as exc:
            QMessageBox.warning(
                self,
                "Serveur inaccessible",
                f"Impossible de joindre le serveur API :\n{exc.reason}\n\n"
                f"Le serveur API REST (port 8080) doit être démarré pour tester la clé.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", f"Erreur inattendue : {exc}")
\2'''
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content == content:
        print("⚠️  Pattern non trouvé, tentative alternative...")
        # Alternative: insert after _on_generate_key definition
        alt_pattern = r'(    def _on_generate_key\(self\) -> None:.*?QMessageBox\.warning\(self, "Erreur", str\(exc\)\))'
        match = re.search(alt_pattern, content, re.DOTALL)
        if match:
            insert_pos = match.end()
            method_code = '''

    def _on_test_api_key(self) -> None:
        """Teste la clé API saisie en faisant une requête simple vers l'endpoint /health."""
        test_key = self.test_key.text().strip()
        if not test_key:
            QMessageBox.warning(self, "Clé manquante", "Entrez une clé API à tester.")
            return

        # Tenter de joindre le serveur API local (port par défaut 8080)
        import urllib.request
        import urllib.error
        import json

        url = "http://127.0.0.1:8080/api/v1/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {test_key}"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8")
                    QMessageBox.information(
                        self,
                        "Test réussi",
                        f"✅ Clé API valide\\n\\nRéponse du serveur :\\n{body}",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Échec du test",
                        f"Code HTTP {resp.status}\\nLa clé pourrait être invalide ou expirée.",
                    )
        except urllib.error.HTTPError as exc:
            QMessageBox.warning(
                self,
                "Échec du test",
                f"Erreur HTTP {exc.code} : {exc.reason}\\n\\n"
                f"Vérifiez que le serveur API est démarré (onglet OpenAPI → Générer le spec)\\n"
                f"et que la clé est correcte.",
            )
        except urllib.error.URLError as exc:
            QMessageBox.warning(
                self,
                "Serveur inaccessible",
                f"Impossible de joindre le serveur API :\\n{exc.reason}\\n\\n"
                f"Le serveur API REST (port 8080) doit être démarré pour tester la clé.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", f"Erreur inattendue : {exc}")'''
            new_content = content[:insert_pos] + method_code + content[insert_pos:]
        else:
            print("❌ Impossible de trouver l'emplacement d'insertion")
            return False
    
    # Backup
    backup_path = file_path.with_suffix('.py.bak')
    shutil.copy2(file_path, backup_path)
    print(f"📦 Backup créé: {backup_path}")
    
    # Write patched file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ Patch appliqué avec succès à {file_path}")
    return True


def main():
    print("🔧 Hotfix Flatpak EduSync AD - Ajout de _on_test_api_key")
    print("=" * 60)
    
    api_page_path = find_flatpak_api_page()
    
    if not api_page_path:
        print("❌ Fichier api_page.py introuvable dans l'installation Flatpak")
        print("\nChemins recherchés:")
        paths = [
            "~/.local/share/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
            "/var/lib/flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
            "~/AppData/Local/Flatpak/app/org.edusync.AD/current/active/files/lib/python3.11/site-packages/edusync_ad/ui/api_page.py",
        ]
        for p in paths:
            print(f"  - {p}")
        print("\n💡 Vous pouvez spécifier le chemin manuellement:")
        print("   python3 hotfix_flatpak_api_page.py /chemin/vers/api_page.py")
        sys.exit(1)
    
    print(f"📍 Fichier trouvé: {api_page_path}")
    
    # If argument provided, use it
    if len(sys.argv) > 1:
        api_page_path = Path(sys.argv[1])
        if not api_page_path.exists():
            print(f"❌ Fichier non trouvé: {api_page_path}")
            sys.exit(1)
    
    success = patch_api_page(api_page_path)
    
    if success:
        print("\n✅ Hotfix appliqué avec succès!")
        print("🔄 Relancez l'application: flatpak run org.edusync.AD")
    else:
        print("\n❌ Échec du patch")
        sys.exit(1)


if __name__ == "__main__":
    main()