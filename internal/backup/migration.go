// Package backup implémente la migration des données vN → vN+1.
package backup

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/estemobs/EduSync-AD/internal/config"
)

// Constantes pour les noms de fichiers (partagées avec backup.py/migration.py).
const (
	NomAppConfig           = "appconfig.json"
	NomConfig              = "config.json"
	NomJournal             = "journal.db"
	NomCoffre              = "password_vault.sqlite3"
	NomCle                 = "secret.key"
	CheminRapportRGPD      = "rapports/rapport_rgpd.json"
	RegistreMigrations     = "migrations.json"
	FormatRegistre         = "edusync-migrations"
	VersionInitialeDonnees = 1
)

// DataVersionCurrent est la version de schéma supportée par cette version de l'application.
// Doit correspondre à la dernière migration dans la table MIGRATIONS.
var DataVersionCurrent = 4

// MigrationError est une erreur de migration.
type MigrationError struct {
	msg string
}

func (e *MigrationError) Error() string { return e.msg }

func newMigrationError(format string, args ...any) *MigrationError {
	return &MigrationError{msg: fmt.Sprintf(format, args...)}
}

// Registre représente le registre de migrations.
type Registre struct {
	Format     string            `json:"format"`
	Version    int               `json:"version"`
	Historique []HistoriqueEntry `json:"historique"`
}

type HistoriqueEntry struct {
	Identifiant string   `json:"identifiant"`
	Version     int      `json:"version"`
	Horodatage  string   `json:"horodatage"`
	Elements    []string `json:"elements"`
}

// RegistrePath retourne le chemin du registre.
func RegistrePath(repertoireDonnees string) string {
	return filepath.Join(repertoireDonnees, RegistreMigrations)
}

// LireRegistre charge le registre (registre absent = version initiale).
func LireRegistre(chemin string) (*Registre, error) {
	if chemin == "" {
		dir, _ := config.DataDir()
		chemin = filepath.Join(dir, RegistreMigrations)
	}

	if _, err := os.Stat(chemin); os.IsNotExist(err) {
		return &Registre{
			Format:     FormatRegistre,
			Version:    VersionInitialeDonnees,
			Historique: nil,
		}, nil
	}

	data, err := os.ReadFile(chemin)
	if err != nil {
		return nil, newMigrationError("lecture registre: %v", err)
	}

	var reg Registre
	if err := json.Unmarshal(data, &reg); err != nil {
		return nil, newMigrationError("JSON invalide: %v", err)
	}
	if reg.Format != FormatRegistre {
		return nil, newMigrationError("format registre inconnu: %s", reg.Format)
	}
	if reg.Version < VersionInitialeDonnees {
		return nil, newMigrationError("version registre invalide: %d", reg.Version)
	}
	return &reg, nil
}

// EcrireRegistre écrit le registre de façon atomique.
func EcrireRegistre(reg *Registre, chemin string) error {
	if chemin == "" {
		dir, _ := config.DataDir()
		chemin = filepath.Join(dir, RegistreMigrations)
	}
	if err := os.MkdirAll(filepath.Dir(chemin), 0o700); err != nil {
		return err
	}
	tmp := chemin + ".tmp"
	data, _ := json.MarshalIndent(reg, "", "  ")
	data = append(data, '\n')
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, chemin)
}

// DetecterVersionDonnees retourne la version actuelle.
func DetecterVersionDonnees(chemin string) (int, error) {
	reg, err := LireRegistre(chemin)
	if err != nil {
		return 0, err
	}
	return reg.Version, nil
}

// ContexteMigration fournit les chemins pour une migration.
type ContexteMigration struct {
	RepertoireConfig  string
	RepertoireDonnees string
}

// ResultatMigration est le bilan d'une migration.
type ResultatMigration struct {
	Migres   []string
	Nouveaux []string
}

// Migration représente une étape vN → vN+1.
type Migration struct {
	Identifiant string
	Version     int
	Description string
	Cibles      []string
	Appliquer   func(ContexteMigration) (ResultatMigration, error)
}

// --- Migrations individuelles ---

// Migration 0001: scission identifiant_format → identifiant_format_eleve / personnel
func migrerFormatIdentifiant(ctx ContexteMigration) (ResultatMigration, error) {
	var res ResultatMigration
	configPath := filepath.Join(ctx.RepertoireConfig, NomConfig)

	data, err := os.ReadFile(configPath)
	if err != nil {
		if os.IsNotExist(err) {
			res.Migres = append(res.Migres, fmt.Sprintf("%s absent — rien à migrer", NomConfig))
			return res, nil
		}
		return res, newMigrationError("lecture config: %v", err)
	}

	var cfg map[string]any
	if err := json.Unmarshal(data, &cfg); err != nil {
		return res, newMigrationError("config JSON invalide: %v", err)
	}

	historique, ok := cfg["identifiant_format"].(string)
	if !ok || historique == "" {
		res.Migres = append(res.Migres, fmt.Sprintf("%s déjà au format courant", NomConfig))
		return res, nil
	}

	for _, champ := range []string{"identifiant_format_eleve", "identifiant_format_personnel"} {
		if _, ok := cfg[champ].(string); !ok {
			cfg[champ] = historique
			res.Nouveaux = append(res.Nouveaux, "config."+champ)
		}
	}
	delete(cfg, "identifiant_format")
	res.Migres = append(res.Migres, fmt.Sprintf("%s: champ historique scindé en eleve/personnel", NomConfig))

	newData, _ := json.MarshalIndent(cfg, "", "  ")
	if err := os.WriteFile(configPath, newData, 0o600); err != nil {
		return res, newMigrationError("écriture config: %v", err)
	}
	return res, nil
}

// Migration 0002: colonnes audit manquantes (utilisateur, domaine, delai_jours)
func migrerJournal(ctx ContexteMigration) (ResultatMigration, error) {
	var res ResultatMigration
	journalPath := filepath.Join(ctx.RepertoireDonnees, NomJournal)

	if _, err := os.Stat(journalPath); os.IsNotExist(err) {
		res.Migres = append(res.Migres, fmt.Sprintf("%s absent — rien à migrer", NomJournal))
		return res, nil
	}

	db, err := sql.Open("sqlite", "file:"+journalPath+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return res, newMigrationError("ouverture journal: %v", err)
	}
	defer db.Close()

	// Vérifier tables
	tables := make(map[string]bool)
	rows, _ := db.Query("SELECT name FROM sqlite_master WHERE type='table'")
	for rows.Next() {
		var name string
		rows.Scan(&name)
		tables[name] = true
	}
	rows.Close()

	for _, table := range []string{"actions", "pending_deletions"} {
		if !tables[table] {
			res.Migres = append(res.Migres, fmt.Sprintf("%s: table %s absente", NomJournal, table))
			continue
		}

		// Colonnes attendues
		attendues := map[string]string{}
		if table == "actions" {
			attendues["utilisateur"] = "TEXT"
			attendues["domaine"] = "TEXT"
		} else {
			attendues["delai_jours"] = "INTEGER"
		}

		// Colonnes existantes
		existantes := make(map[string]bool)
		rows2, _ := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", table))
		for rows2.Next() {
			var cid int
			var name, ctype string
			var notnull, pk int
			var dflt any
			rows2.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk)
			existantes[name] = true
		}
		rows2.Close()

		for nom, typ := range attendues {
			if !existantes[nom] {
				_, err := db.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, nom, typ))
				if err != nil {
					return res, newMigrationError("ALTER TABLE %s: %v", table, err)
				}
				res.Nouveaux = append(res.Nouveaux, table+"."+nom)
			}
		}
	}

	if len(res.Nouveaux) == 0 && len(res.Migres) == 0 {
		res.Migres = append(res.Migres, fmt.Sprintf("%s déjà au schéma courant", NomJournal))
	}
	return res, nil
}

// Migration 0003: normalisation SAM du coffre en minuscules
func migrerCoffre(ctx ContexteMigration) (ResultatMigration, error) {
	var res ResultatMigration
	coffrePath := filepath.Join(ctx.RepertoireDonnees, NomCoffre)

	if _, err := os.Stat(coffrePath); os.IsNotExist(err) {
		res.Migres = append(res.Migres, fmt.Sprintf("%s absent — rien à migrer", NomCoffre))
		return res, nil
	}

	db, err := sql.Open("sqlite", "file:"+coffrePath+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return res, newMigrationError("ouverture coffre: %v", err)
	}
	defer db.Close()

	// Vérifier table
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mots_de_passe'").Scan(&count)
	if err != nil || count == 0 {
		return res, newMigrationError("table mots_de_passe absente")
	}

	// Lire toutes les entrées
	rows, err := db.Query("SELECT rowid, sam, secret, horodatage FROM mots_de_passe ORDER BY rowid")
	if err != nil {
		return res, newMigrationError("lecture coffre: %v", err)
	}
	defer rows.Close()

	type entree struct {
		sam, secret, horodatage string
	}
	var lignes []entree
	for rows.Next() {
		var e entree
		if err := rows.Scan(&e.sam, &e.secret, &e.horodatage); err != nil {
			return res, newMigrationError("scan: %v", err)
		}
		lignes = append(lignes, e)
	}

	// Regrouper par SAM minuscule (dernière entrée gagne)
	regroupes := make(map[string]entree)
	casse := 0
	for _, l := range lignes {
		if l.sam != "" && l.sam != strings.ToLower(l.sam) {
			casse++
		}
		regroupes[strings.ToLower(l.sam)] = l
	}

	if casse == 0 && len(regroupes) == len(lignes) {
		res.Migres = append(res.Migres, fmt.Sprintf("%s déjà normalisé", NomCoffre))
		return res, nil
	}

	// Réécrire
	_, err = db.Exec("DELETE FROM mots_de_passe")
	if err != nil {
		return res, newMigrationError("DELETE: %v", err)
	}

	stmt, err := db.Prepare("INSERT INTO mots_de_passe (sam, secret, horodatage) VALUES (?, ?, ?)")
	if err != nil {
		return res, newMigrationError("prepare: %v", err)
	}
	defer stmt.Close()

	for sam, e := range regroupes {
		if _, err := stmt.Exec(sam, e.secret, e.horodatage); err != nil {
			return res, newMigrationError("INSERT: %v", err)
		}
	}

	res.Nouveaux = append(res.Nouveaux, NomCoffre+".sam normalisés en minuscules")
	res.Migres = append(res.Migres, fmt.Sprintf("%s: %d recasé(s), %d doublon(s) fusionné(s)",
		NomCoffre, casse, len(lignes)-len(regroupes)))
	return res, nil
}

// Table des migrations (doit être contiguë à partir de VersionInitialeDonnees+1).
var MIGRATIONS = []Migration{
	{
		Identifiant: "0001_config_format_identifiant",
		Version:     2,
		Description: "Scission du champ unique identifiant_format (élèves / personnels)",
		Cibles:      []string{"config"},
		Appliquer:   migrerFormatIdentifiant,
	},
	{
		Identifiant: "0002_journal_colonnes_audit",
		Version:     3,
		Description: "Ajout des colonnes d'audit utilisateur / domaine / delai_jours",
		Cibles:      []string{"journal"},
		Appliquer:   migrerJournal,
	},
	{
		Identifiant: "0003_coffre_sam_minuscules",
		Version:     4,
		Description: "Normalisation des sam du coffre MDP en minuscules",
		Cibles:      []string{"coffre"},
		Appliquer:   migrerCoffre,
	},
}

// MigrationResult est le résultat complet de la migration.
type MigrationResult struct {
	Reussite             bool
	VersionDepart        int
	VersionArrivee       int
	VersionCible         int
	DejaMigre            bool
	ElementsMigres       []string
	ElementsNouveaux     []string
	MigrationsAppliquees []string
	MigrationsEnAttente  []string
	Rapport              []map[string]any
	Avertissements       []string
	Erreurs              []string
}

// MigrerDonneesVnVn1 migre les données vers la version cible.
func MigrerDonneesVnVn1(repertoireConfig, repertoireDonnees string, opts ...MigrateOption) (*MigrationResult, error) {
	o := migrateOptions{
		registrePath: "",
		versionCible: DataVersionCurrent,
		modeTest:     false,
	}
	for _, opt := range opts {
		opt(&o)
	}

	registrePath := o.registrePath
	if registrePath == "" {
		registrePath = RegistrePath(repertoireDonnees)
	}

	reg, err := LireRegistre(registrePath)
	if err != nil {
		return &MigrationResult{Erreurs: []string{err.Error()}}, err
	}

	depart := reg.Version
	res := &MigrationResult{
		VersionDepart: depart,
		VersionCible:  o.versionCible,
	}

	if depart > DataVersionCurrent {
		res.Erreurs = append(res.Erreurs, fmt.Sprintf(
			"Version de données inconnue: %d (max supporté: %d) — migration refusée.",
			depart, DataVersionCurrent))
		return res, nil
	}
	if o.versionCible > DataVersionCurrent {
		res.Erreurs = append(res.Erreurs, fmt.Sprintf(
			"Version cible inconnue: %d (max supporté: %d).",
			o.versionCible, DataVersionCurrent))
		return res, nil
	}
	if o.versionCible < depart {
		res.Erreurs = append(res.Erreurs, fmt.Sprintf(
			"Rétrogradation refusée: cible %d < courante %d.", o.versionCible, depart))
		return res, nil
	}

	// Vérifier cohérence table
	for i, m := range MIGRATIONS {
		expected := VersionInitialeDonnees + 1 + i
		if m.Version != expected {
			res.Erreurs = append(res.Erreurs, fmt.Sprintf(
				"Table migrations incohérente: %s vise v%d, attendu v%d.",
				m.Identifiant, m.Version, expected))
			return res, nil
		}
	}

	enAttente := []Migration{}
	for _, m := range MIGRATIONS {
		if depart < m.Version && m.Version <= o.versionCible {
			enAttente = append(enAttente, m)
		}
	}
	res.MigrationsEnAttente = make([]string, len(enAttente))
	for i, m := range enAttente {
		res.MigrationsEnAttente[i] = m.Identifiant
	}

	if len(enAttente) == 0 {
		res.Reussite = true
		res.DejaMigre = true
		res.VersionArrivee = depart
		return res, nil
	}

	if o.modeTest {
		res.Reussite = true
		res.VersionArrivee = depart
		for _, m := range enAttente {
			res.Rapport = append(res.Rapport, map[string]any{
				"identifiant": m.Identifiant,
				"version":     m.Version,
				"description": m.Description,
				"cibles":      m.Cibles,
				"statut":      "à appliquer",
				"elements":    []string{},
			})
		}
		res.Avertissements = append(res.Avertissements, "Mode test: aucune donnée modifiée.")
		return res, nil
	}

	ctx := ContexteMigration{
		RepertoireConfig:  repertoireConfig,
		RepertoireDonnees: repertoireDonnees,
	}
	versionCourante := depart
	interrompue := false

	for _, m := range enAttente {
		entree := map[string]any{
			"identifiant": m.Identifiant,
			"version":     m.Version,
			"description": m.Description,
			"cibles":      m.Cibles,
			"statut":      "appliquée",
			"elements":    []string{},
		}

		resultat, err := m.Appliquer(ctx)
		if err != nil {
			entree["statut"] = "échec"
			entree["erreur"] = err.Error()
			res.Rapport = append(res.Rapport, entree)
			res.Erreurs = append(res.Erreurs, fmt.Sprintf("Échec %s: %v", m.Identifiant, err))
			interrompue = true
			break
		}

		entree["elements"] = append(resultat.Migres, resultat.Nouveaux...)
		res.ElementsMigres = append(res.ElementsMigres, resultat.Migres...)
		res.ElementsNouveaux = append(res.ElementsNouveaux, resultat.Nouveaux...)
		res.MigrationsAppliquees = append(res.MigrationsAppliquees, m.Identifiant)
		res.Rapport = append(res.Rapport, entree)

		versionCourante = m.Version
		reg.Version = versionCourante
		reg.Historique = append(reg.Historique, HistoriqueEntry{
			Identifiant: m.Identifiant,
			Version:     m.Version,
			Horodatage:  time.Now().UTC().Format(time.RFC3339),
			Elements:    append(resultat.Migres, resultat.Nouveaux...),
		})
		if err := EcrireRegistre(reg, registrePath); err != nil {
			res.Erreurs = append(res.Erreurs, fmt.Sprintf("Registre non écrit: %v", err))
			interrompue = true
			break
		}
	}

	res.VersionArrivee = versionCourante
	res.MigrationsEnAttente = []string{}
	for _, m := range MIGRATIONS {
		if versionCourante < m.Version && m.Version <= o.versionCible {
			res.MigrationsEnAttente = append(res.MigrationsEnAttente, m.Identifiant)
		}
	}
	res.Reussite = !interrompue
	if !interrompue {
		res.DejaMigre = false
	}
	return res, nil
}

type migrateOptions struct {
	registrePath string
	versionCible int
	modeTest     bool
}

type MigrateOption func(*migrateOptions)

func WithRegistrePath(path string) MigrateOption {
	return func(o *migrateOptions) { o.registrePath = path }
}

func WithVersionCible(v int) MigrateOption {
	return func(o *migrateOptions) { o.versionCible = v }
}

func WithModeTest(test bool) MigrateOption {
	return func(o *migrateOptions) { o.modeTest = test }
}
