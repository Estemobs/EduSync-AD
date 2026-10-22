package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/estemobs/EduSync-AD/internal/audit"
	"github.com/estemobs/EduSync-AD/internal/config"
	"github.com/estemobs/EduSync-AD/internal/crypto"
	"github.com/estemobs/EduSync-AD/internal/vault"
)

// cmdMigrate gère la commande de migration depuis Python.
func cmdMigrate(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		return &codeSortie{code: 2, err: errors.New("sous-commande manquante : « from-python »")}
	}
	if args[0] != "from-python" {
		return &codeSortie{code: 2, err: fmt.Errorf("sous-commande inconnue %q (attendu : from-python)", args[0])}
	}
	return cmdMigrateFromPython(args[1:], stdout, stderr)
}

// cmdMigrateFromPython migre les données Python vers Go.
func cmdMigrateFromPython(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("migrate from-python", flag.ContinueOnError)
	configDir := fs.String("config-dir", "", "dossier config Python (ex: ~/.config/EduSyncAD)")
	vaultDir := fs.String("vault-dir", "", "dossier données Python (ex: ~/.local/share/EduSyncAD)")
	auditPath := fs.String("audit-path", "", "fichier journal.db Python (ex: ~/.local/share/EduSyncAD/journal.db)")
	multisitePath := fs.String("multisite-path", "", "fichier domaines.json Python (ex: ~/.config/EduSyncAD/domaines.json)")
	force := fs.Bool("force", false, "écraser les fichiers Go existants sans demander")
	dryRun := fs.Bool("dry-run", false, "simuler sans écrire")
	jsonOut := fs.Bool("json", false, "sortie JSON")
	if err := fs.Parse(args); err != nil {
		return &codeSortie{code: 2, err: err}
	}

	// Résoudre les chemins par défaut si non fournis
	if *configDir == "" {
		dir, err := config.ConfigDir()
		if err != nil {
			return fmt.Errorf("config dir par défaut: %w", err)
		}
		*configDir = dir
	}
	if *vaultDir == "" {
		dir, err := config.DataDir()
		if err != nil {
			return fmt.Errorf("data dir par défaut: %w", err)
		}
		*vaultDir = dir
	}
	if *auditPath == "" {
		dir, err := config.DataDir()
		if err != nil {
			return fmt.Errorf("data dir par défaut: %w", err)
		}
		*auditPath = filepath.Join(dir, "journal.db")
	}
	if *multisitePath == "" {
		dir, err := config.ConfigDir()
		if err != nil {
			return fmt.Errorf("config dir par défaut: %w", err)
		}
		*multisitePath = filepath.Join(dir, "domaines.json")
	}

	// Afficher les chemins résolus
	fmt.Fprintf(stderr, "📁 Config Python : %s\n", *configDir)
	fmt.Fprintf(stderr, "📁 Vault Python  : %s\n", *vaultDir)
	fmt.Fprintf(stderr, "📁 Audit Python  : %s\n", *auditPath)
	fmt.Fprintf(stderr, "📁 Multisite Py  : %s\n", *multisitePath)

	// Chemins cibles Go
	goConfigPath, _ := config.DefaultPath()
	goVaultPath, _ := vault.CheminParDefaut()
	goKeyPath, _ := vault.CheminCleParDefaut()
	goAuditPath, _ := audit.DefaultPath()
	goMultisitePath := filepath.Join(filepath.Dir(goConfigPath), "domaines.json")

	fmt.Fprintf(stderr, "\n🎯 Config Go     : %s\n", goConfigPath)
	fmt.Fprintf(stderr, "🎯 Vault Go      : %s\n", goVaultPath)
	fmt.Fprintf(stderr, "🎯 Key Go        : %s\n", goKeyPath)
	fmt.Fprintf(stderr, "🎯 Audit Go      : %s\n", goAuditPath)
	fmt.Fprintf(stderr, "🎯 Multisite Go  : %s\n", goMultisitePath)

	// Vérifier si les fichiers cibles existent déjà
	exists := []string{}
	if _, err := os.Stat(goConfigPath); err == nil {
		exists = append(exists, "config.json")
	}
	if _, err := os.Stat(goVaultPath); err == nil {
		exists = append(exists, "password_vault.sqlite3")
	}
	if _, err := os.Stat(goAuditPath); err == nil {
		exists = append(exists, "journal.db")
	}
	if _, err := os.Stat(goMultisitePath); err == nil {
		exists = append(exists, "domaines.json")
	}
	if _, err := os.Stat(goKeyPath); err == nil {
		exists = append(exists, "secret.key")
	}

	if len(exists) > 0 && !*force && !*dryRun {
		fmt.Fprintf(stderr, "\n⚠️  Fichiers Go existants : %s\n", strings.Join(exists, ", "))
		fmt.Fprint(stderr, "Utilisez --force pour écraser, ou --dry-run pour simuler.\n")
		return &codeSortie{code: 1, err: errors.New("fichiers cibles existants")}
	}

	// Résultat de la migration
	result := struct {
		Config    string   `json:"config"`
		Vault     string   `json:"vault"`
		Audit     string   `json:"audit"`
		Multisite string   `json:"multisite"`
		Key       string   `json:"key"`
		Errors    []string `json:"errors,omitempty"`
	}{
		Config:    "skipped",
		Vault:     "skipped",
		Audit:     "skipped",
		Multisite: "skipped",
		Key:       "skipped",
	}

	// 1. Migrer config.json (déchiffrer avec clé Python → chiffrer avec clé Go)
	if err := migrateConfig(*configDir, goConfigPath, goKeyPath, *dryRun, stderr); err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("config: %v", err))
		result.Config = "error"
	} else {
		result.Config = "migrated"
	}

	// 2. Migrer secret.key (copier telle quelle - format identique)
	if err := migrateKey(*configDir, goKeyPath, *dryRun, stderr); err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("key: %v", err))
		result.Key = "error"
	} else {
		result.Key = "migrated"
	}

	// 3. Migrer vault (password_vault.sqlite3 - rechiffrer avec clé Go)
	if err := migrateVault(*vaultDir, goVaultPath, goKeyPath, *dryRun, stderr); err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("vault: %v", err))
		result.Vault = "error"
	} else {
		result.Vault = "migrated"
	}

	// 4. Migrer audit (journal.db - copier tel quel, schéma identique)
	if err := migrateAudit(*auditPath, goAuditPath, *dryRun, stderr); err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("audit: %v", err))
		result.Audit = "error"
	} else {
		result.Audit = "migrated"
	}

	// 5. Migrer multisite (domaines.json - déchiffrer avec clé Python → chiffrer avec clé Go)
	if err := migrateMultisite(*multisitePath, goMultisitePath, goKeyPath, *dryRun, stderr); err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("multisite: %v", err))
		result.Multisite = "error"
	} else {
		result.Multisite = "migrated"
	}

	if *jsonOut {
		return ecrireJSON(stdout, result)
	}

	fmt.Fprintln(stderr, "\n✅ Migration terminée")
	for k, v := range map[string]string{
		"Config": result.Config, "Vault": result.Vault,
		"Audit": result.Audit, "Multisite": result.Multisite, "Key": result.Key,
	} {
		icon := "✅"
		if v == "error" {
			icon = "❌"
		}
		if v == "skipped" {
			icon = "⏭️"
		}
		fmt.Fprintf(stderr, "  %s %s: %s\n", icon, k, v)
	}
	if len(result.Errors) > 0 {
		fmt.Fprint(stderr, "\n❌ Erreurs:\n")
		for _, e := range result.Errors {
			fmt.Fprintf(stderr, "  - %s\n", e)
		}
		return &codeSortie{code: 1, err: errors.New("migration partielle")}
	}
	return nil
}

// migrateConfig migre config.json chiffré Python → Go
func migrateConfig(pythonConfigDir, goConfigPath, goKeyPath string, dryRun bool, stderr io.Writer) error {
	pythonConfigPath := filepath.Join(pythonConfigDir, "config.json")
	if _, err := os.Stat(pythonConfigPath); os.IsNotExist(err) {
		return fmt.Errorf("fichier absent: %s", pythonConfigPath)
	}

	// Lire la clé Python
	pythonKeyPath := filepath.Join(pythonConfigDir, "secret.key")
	pythonKey, err := os.ReadFile(pythonKeyPath)
	if err != nil {
		return fmt.Errorf("clé Python absente: %s (indispensable pour déchiffrer config.json)", pythonKeyPath)
	}

	// Lire config.json Python (chiffré)
	data, err := os.ReadFile(pythonConfigPath)
	if err != nil {
		return fmt.Errorf("lecture config Python: %w", err)
	}
	encrypted := strings.TrimSpace(string(data))
	if encrypted == "" {
		return fmt.Errorf("config.json Python vide")
	}

	// Déchiffrer avec la clé Python
	plaintext, err := crypto.Decrypt(pythonKey, encrypted)
	if err != nil {
		return fmt.Errorf("déchiffrement config Python échoué (clé incorrecte ou données corrompues): %w", err)
	}

	// Sauvegarder l'original
	if !dryRun {
		backupPath := pythonConfigPath + ".bak"
		if err := copyFile(pythonConfigPath, backupPath); err != nil {
			fmt.Fprintf(stderr, "  ⚠️  Backup config Python échoué: %v\n", err)
		} else {
			fmt.Fprintf(stderr, "  💾 Backup: %s\n", backupPath)
		}
	}

	// Charger/ créer la clé Go
	goKey, err := crypto.LoadOrCreateKey(goKeyPath)
	if err != nil {
		return fmt.Errorf("clé Go: %w", err)
	}

	// Rechiffrer avec la clé Go
	goEncrypted, err := crypto.Encrypt(goKey, plaintext)
	if err != nil {
		return fmt.Errorf("rechiffrement config Go: %w", err)
	}

	// Écrire config.json Go
	if !dryRun {
		if err := os.MkdirAll(filepath.Dir(goConfigPath), 0o700); err != nil {
			return fmt.Errorf("dossier config Go: %w", err)
		}
		if err := os.WriteFile(goConfigPath, []byte(goEncrypted), 0o600); err != nil {
			return fmt.Errorf("écriture config Go: %w", err)
		}
		fmt.Fprintf(stderr, "  📝 Config Go écrite: %s\n", goConfigPath)
	}

	return nil
}

// migrateKey copie secret.key (format identique Python/Go)
func migrateKey(pythonConfigDir, goKeyPath string, dryRun bool, stderr io.Writer) error {
	pythonKeyPath := filepath.Join(pythonConfigDir, "secret.key")
	if _, err := os.Stat(pythonKeyPath); os.IsNotExist(err) {
		return nil // Pas de clé Python = pas de migration nécessaire
	}

	keyData, err := os.ReadFile(pythonKeyPath)
	if err != nil {
		return fmt.Errorf("lecture clé Python: %w", err)
	}

	// Vérifier taille (32 octets = AES-256)
	if len(keyData) != 32 {
		return fmt.Errorf("clé Python taille inattendue: %d octets (attendu 32)", len(keyData))
	}

	if !dryRun {
		if err := os.MkdirAll(filepath.Dir(goKeyPath), 0o700); err != nil {
			return fmt.Errorf("dossier clé Go: %w", err)
		}
		if err := os.WriteFile(goKeyPath, keyData, 0o600); err != nil {
			return fmt.Errorf("écriture clé Go: %w", err)
		}
		fmt.Fprintf(stderr, "  🔑 Clé copiée: %s\n", goKeyPath)
	}
	return nil
}

// migrateVault migre password_vault.sqlite3 (rechiffrement entrées)
func migrateVault(pythonVaultDir, goVaultPath, goKeyPath string, dryRun bool, stderr io.Writer) error {
	pythonVaultPath := filepath.Join(pythonVaultDir, "password_vault.sqlite3")
	if _, err := os.Stat(pythonVaultPath); os.IsNotExist(err) {
		return nil // Pas de vault Python = rien à migrer
	}

	// Lire clé Python - essayer plusieurs emplacements possibles
	possibleKeyPaths := []string{
		filepath.Join(filepath.Dir(pythonVaultDir), "secret.key"),
		filepath.Join(filepath.Dir(filepath.Dir(pythonVaultDir)), "config", "EduSyncAD", "secret.key"),
		filepath.Join(filepath.Dir(filepath.Dir(pythonVaultDir)), ".config", "EduSyncAD", "secret.key"),
	}
	var pythonKey []byte
	for _, kp := range possibleKeyPaths {
		if data, err := os.ReadFile(kp); err == nil && len(data) == 32 {
			pythonKey = data
			break
		}
	}
	if pythonKey == nil {
		return fmt.Errorf("clé Python introuvable pour déchiffrer le vault (cherché: %v)", possibleKeyPaths)
	}

	// Ouvrir vault Python en lecture seule
	pyVault, err := vault.New(pythonVaultPath, "")
	if err != nil {
		return fmt.Errorf("ouverture vault Python: %w", err)
	}
	defer pyVault.Close()

	// Lister les entrées
	entries, err := pyVault.Liste()
	if err != nil {
		return fmt.Errorf("listage vault Python: %w", err)
	}

	if len(entries) == 0 {
		return nil // Vault vide
	}

	// Sauvegarder l'original
	if !dryRun {
		backupPath := pythonVaultPath + ".bak"
		if err := copyFile(pythonVaultPath, backupPath); err != nil {
			fmt.Fprintf(stderr, "  ⚠️  Backup vault Python échoué: %v\n", err)
		} else {
			fmt.Fprintf(stderr, "  💾 Backup: %s\n", backupPath)
		}
	}

	// S'assurer que la clé Go existe
	if _, err := crypto.LoadOrCreateKey(goKeyPath); err != nil {
		return fmt.Errorf("clé Go: %w", err)
	}

	// Ouvrir vault Go
	goVault, err := vault.New(goVaultPath, goKeyPath)
	if err != nil {
		return fmt.Errorf("ouverture vault Go: %w", err)
	}
	defer goVault.Close()

	// Migrer chaque entrée
	migrated := 0
	for _, sam := range entries {
		pwd, ok := pyVault.Get(sam)
		if !ok {
			fmt.Fprintf(stderr, "  ⚠️  Impossible de déchiffrer %s (clé changée ?)\n", sam)
			continue
		}
		if err := goVault.Store(sam, pwd); err != nil {
			fmt.Fprintf(stderr, "  ⚠️  Erreur écriture %s: %v\n", sam, err)
			continue
		}
		migrated++
	}

	fmt.Fprintf(stderr, "  🔐 %d entrées migrées\n", migrated)
	return nil
}

// migrateAudit copie journal.db (schéma identique)
func migrateAudit(pythonAuditPath, goAuditPath string, dryRun bool, stderr io.Writer) error {
	if _, err := os.Stat(pythonAuditPath); os.IsNotExist(err) {
		return nil // Pas d'audit Python
	}

	// Sauvegarder l'original
	if !dryRun {
		backupPath := pythonAuditPath + ".bak"
		if err := copyFile(pythonAuditPath, backupPath); err != nil {
			fmt.Fprintf(stderr, "  ⚠️  Backup audit Python échoué: %v\n", err)
		} else {
			fmt.Fprintf(stderr, "  💾 Backup: %s\n", backupPath)
		}
	}

	// Copier tel quel (schéma identique)
	if !dryRun {
		if err := os.MkdirAll(filepath.Dir(goAuditPath), 0o700); err != nil {
			return fmt.Errorf("dossier audit Go: %w", err)
		}
		if err := copyFile(pythonAuditPath, goAuditPath); err != nil {
			return fmt.Errorf("copie audit: %w", err)
		}
		fmt.Fprintf(stderr, "  📋 Audit copié: %s\n", goAuditPath)
	}
	return nil
}

// migrateMultisite migre domaines.json chiffré Python → Go
func migrateMultisite(pythonMultisitePath, goMultisitePath, goKeyPath string, dryRun bool, stderr io.Writer) error {
	if _, err := os.Stat(pythonMultisitePath); os.IsNotExist(err) {
		return nil // Pas de multisite Python
	}

	// Lire clé Python
	pythonKeyPath := filepath.Join(filepath.Dir(pythonMultisitePath), "secret.key")
	pythonKey, err := os.ReadFile(pythonKeyPath)
	if err != nil {
		return fmt.Errorf("clé Python pour multisite: %w", err)
	}

	// Lire domaines.json Python
	data, err := os.ReadFile(pythonMultisitePath)
	if err != nil {
		return fmt.Errorf("lecture multisite Python: %w", err)
	}
	encrypted := strings.TrimSpace(string(data))
	if encrypted == "" {
		return nil // Fichier vide
	}

	// Déchiffrer
	plaintext, err := crypto.Decrypt(pythonKey, encrypted)
	if err != nil {
		return fmt.Errorf("déchiffrement multisite Python: %w", err)
	}

	// Sauvegarder l'original
	if !dryRun {
		backupPath := pythonMultisitePath + ".bak"
		if err := copyFile(pythonMultisitePath, backupPath); err != nil {
			fmt.Fprintf(stderr, "  ⚠️  Backup multisite Python échoué: %v\n", err)
		} else {
			fmt.Fprintf(stderr, "  💾 Backup: %s\n", backupPath)
		}
	}

	// Clé Go
	goKey, err := crypto.LoadOrCreateKey(goKeyPath)
	if err != nil {
		return fmt.Errorf("clé Go: %w", err)
	}

	// Rechiffrer
	goEncrypted, err := crypto.Encrypt(goKey, plaintext)
	if err != nil {
		return fmt.Errorf("rechiffrement multisite Go: %w", err)
	}

	// Écrire
	if !dryRun {
		if err := os.MkdirAll(filepath.Dir(goMultisitePath), 0o700); err != nil {
			return fmt.Errorf("dossier multisite Go: %w", err)
		}
		if err := os.WriteFile(goMultisitePath, []byte(goEncrypted), 0o600); err != nil {
			return fmt.Errorf("écriture multisite Go: %w", err)
		}
		fmt.Fprintf(stderr, "  🌐 Multisite Go écrit: %s\n", goMultisitePath)
	}
	return nil
}

// copyFile copie un fichier en préservant les permissions
func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}
