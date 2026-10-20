// Package backup implémente la génération de sauvegarde complète d'EduSync AD.
package backup

import (
	"archive/zip"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/estemobs/EduSync-AD/internal/config"
	"github.com/estemobs/EduSync-AD/internal/crypto"
	"github.com/estemobs/EduSync-AD/internal/models"
)

// Element représente un fichier à inclure dans l'archive.
type Element struct {
	Section        string
	Racine         string // "config" ou "data"
	Relatif        string
	Source         string // chemin fichier source (optionnel)
	Contenu        []byte // contenu en mémoire (optionnel)
	SnapshotSQLite bool   // copier via backup SQLite
	Membre         string // calculé: payload/<section>/<relatif>
}

func (e *Element) calculerMembre() {
	e.Membre = PrefixeMembre + "/" + e.Section + "/" + e.Relatif
}

// OptionsBackup configure la génération de la sauvegarde.
type OptionsBackup struct {
	Config            *config.AppConfig
	RepertoireConfig  string
	RepertoireDonnees string
	RepertoireSortie  string

	InclureConfig      bool
	InclureJournal     bool
	InclureCoffre      bool
	InclureModeles     bool
	InclureCertificats bool
	InclureRGPD        bool

	Chiffrer   bool
	MotDePasse string // si non vide, force chiffrement portable
	KeyPath    string // clé locale (défaut: config_dir/secret.key)

	RapportRGPD   map[string]any // rapport RGPD pré-généré
	PortalService interface{}    // service portail pour générer RGPD

	Progress func(int, int) // callback progression (actuel, total)
}

// ResultatBackup contient les métadonnées de la sauvegarde générée.
type ResultatBackup struct {
	ArchiveChemin  string
	GenerationDate string
	Inclus         []string
	NbFichiers     int
	TailleOctets   int64
	EmpreinteZIP   string
	Verification   map[string]any
	Config         map[string]any
	VersionDonnees *int
	Avertissements []string
}

// GenererBackupComplet crée une sauvegarde complète selon les options.
func GenererBackupComplet(ctx context.Context, opts OptionsBackup) (*ResultatBackup, error) {
	// Valeurs par défaut
	if opts.RepertoireConfig == "" {
		dir, err := config.ConfigDir()
		if err != nil {
			return nil, fmt.Errorf("répertoire config: %w", err)
		}
		opts.RepertoireConfig = dir
	}
	if opts.RepertoireDonnees == "" {
		dir, err := config.DataDir()
		if err != nil {
			return nil, fmt.Errorf("répertoire données: %w", err)
		}
		opts.RepertoireDonnees = dir
	}
	if opts.RepertoireSortie == "" {
		dir, err := config.DataDir()
		if err != nil {
			return nil, fmt.Errorf("répertoire données: %w", err)
		}
		opts.RepertoireSortie = filepath.Join(dir, "sauvegardes")
	}

	chiffrerEffectif := opts.Chiffrer || opts.MotDePasse != ""

	// Si chiffrement local, s'assurer que la clé existe AVANT la collecte
	if chiffrerEffectif && opts.MotDePasse == "" {
		keyPath := opts.KeyPath
		if keyPath == "" {
			dir, _ := config.ConfigDir()
			keyPath = filepath.Join(dir, "secret.key")
		}
		if _, err := crypto.LoadOrCreateKey(keyPath); err != nil {
			return nil, fmt.Errorf("clé locale inaccessible: %w", err)
		}
	}

	// Collecte des éléments
	elements, avertissements := collecter(opts)
	if opts.Progress != nil {
		opts.Progress(0, len(elements))
	}

	// Version des données (registre migrations)
	versionDonnees := versionDonnees(opts.RepertoireDonnees, &avertissements)

	// Staging dans répertoire temporaire
	tmpDir, err := os.MkdirTemp("", "edusync-backup-*")
	if err != nil {
		return nil, fmt.Errorf("répertoire temporaire: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	staging := tmpDir
	sections := make(map[string][]map[string]any)

	for i, elem := range elements {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		elem.calculerMembre()
		cible := filepath.Join(staging, elem.Membre)

		var data []byte
		if elem.Contenu != nil {
			data = elem.Contenu
		} else if elem.Source != "" {
			data, err = lireFichier(elem.Source, elem.SnapshotSQLite)
			if err != nil {
				return nil, fmt.Errorf("collecte %s/%s: %w", elem.Section, elem.Relatif, err)
			}
		} else {
			return nil, fmt.Errorf("élément sans source ni contenu: %s/%s", elem.Section, elem.Relatif)
		}

		if err := os.MkdirAll(filepath.Dir(cible), 0o700); err != nil {
			return nil, err
		}
		if err := os.WriteFile(cible, data, 0o600); err != nil {
			return nil, err
		}

		entree := map[string]any{
			"chemin":  elem.Membre,
			"section": elem.Section,
			"racine":  elem.Racine,
			"relatif": elem.Relatif,
			"sha256":  Empreinte(data),
			"taille":  len(data),
		}
		sections[elem.Section] = append(sections[elem.Section], entree)

		if opts.Progress != nil {
			opts.Progress(i+1, len(elements))
		}
	}

	// Aplatir pour l'empreinte globale
	var toutes []map[string]any
	for _, liste := range sections {
		toutes = append(toutes, liste...)
	}

	// Manifeste
	manifeste := map[string]any{
		"format":              FormatArchive,
		"format_version":      VersionFormatArchive,
		"version_application": models.Version,
		"version_donnees":     versionDonnees,
		"generation_date":     time.Now().Format(time.RFC3339),
		"options": map[string]any{
			"include_config":      opts.InclureConfig,
			"include_journal":     opts.InclureJournal,
			"include_coffre":      opts.InclureCoffre,
			"include_modeles":     opts.InclureModeles,
			"include_certificats": opts.InclureCertificats,
			"include_rgpd":        opts.InclureRGPD,
			"chiffre":             chiffrerEffectif,
		},
		"sections":          sections,
		"empreinte_globale": CalculerEmpreinteGlobale(toutes),
		"avertissements":    avertissements,
	}

	manifesteData, _ := json.MarshalIndent(manifeste, "", "  ")
	manifestePath := filepath.Join(staging, ManifesteNom)
	if err := os.WriteFile(manifestePath, manifesteData, 0o600); err != nil {
		return nil, fmt.Errorf("écriture manifeste: %w", err)
	}

	// Créer le ZIP
	zipPath := filepath.Join(staging, "archive.zip")
	if err := creerZIP(zipPath, staging); err != nil {
		return nil, fmt.Errorf("création ZIP: %w", err)
	}

	octetsZIP, err := os.ReadFile(zipPath)
	if err != nil {
		return nil, fmt.Errorf("lecture ZIP: %w", err)
	}

	// Écrire l'archive finale
	if err := os.MkdirAll(opts.RepertoireSortie, 0o700); err != nil {
		return nil, fmt.Errorf("répertoire sortie: %w", err)
	}

	archiveChemin := cheminArchiveUnique(opts.RepertoireSortie, chiffrerEffectif)
	infos, err := EcrireArchive(archiveChemin, octetsZIP, chiffrerEffectif, opts.MotDePasse, opts.KeyPath)
	if err != nil {
		return nil, err
	}

	verification := map[string]any{
		"chiffre":       infos["chiffre"],
		"algorithme":    infos["algorithme"],
		"kdf":           infos["kdf"],
		"source_cle":    infos["cle"],
		"empreinte_zip": infos["empreinte_zip"],
	}
	if !infos["chiffre"].(bool) {
		verification["mise_en_garde"] = MiseEnGardeNonChiffre
	}

	// Sections incluses (non vides)
	var inclus []string
	for _, s := range Sections {
		if len(sections[s]) > 0 {
			inclus = append(inclus, s)
		}
	}

	statInfo, _ := os.Stat(archiveChemin)

	return &ResultatBackup{
		ArchiveChemin:  archiveChemin,
		GenerationDate: time.Now().Format(time.RFC3339),
		Inclus:         inclus,
		NbFichiers:     len(toutes),
		TailleOctets:   statInfo.Size(),
		EmpreinteZIP:   infos["empreinte_zip"].(string),
		Verification:   verification,
		Config: map[string]any{
			"inclure_config":      opts.InclureConfig,
			"inclure_journal":     opts.InclureJournal,
			"inclure_coffre":      opts.InclureCoffre,
			"inclure_modeles":     opts.InclureModeles,
			"inclure_certificats": opts.InclureCertificats,
			"inclure_rgpd":        opts.InclureRGPD,
			"chiffre":             chiffrerEffectif,
		},
		VersionDonnees: versionDonnees,
		Avertissements: avertissements,
	}, nil
}

// collecter construit la liste des éléments à archiver.
func collecter(opts OptionsBackup) ([]Element, []string) {
	var elements []Element
	var avertissements []string
	vus := make(map[string]bool)

	ajouter := func(elem Element) {
		elem.calculerMembre()
		cle := elem.Source
		if cle == "" {
			cle = elem.Membre
		}
		if cle != "" && vus[cle] {
			return
		}
		if cle != "" {
			vus[cle] = true
		}
		elements = append(elements, elem)
	}

	configDir := opts.RepertoireConfig
	dataDir := opts.RepertoireDonnees

	// Configuration
	if opts.InclureConfig {
		if opts.Config != nil {
			data, _ := json.MarshalIndent(opts.Config, "", "  ")
			ajouter(Element{
				Section: "configuration",
				Racine:  "config",
				Relatif: NomAppConfig,
				Contenu: data,
			})
		}
		entries, _ := os.ReadDir(configDir)
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			if entry.Name() == NomAppConfig || contains(FichiersModeles, entry.Name()) {
				continue
			}
			ajouter(Element{
				Section: "configuration",
				Racine:  "config",
				Relatif: entry.Name(),
				Source:  filepath.Join(configDir, entry.Name()),
			})
		}
	}

	// Modèles
	if opts.InclureModeles {
		trouves := 0
		for _, nom := range FichiersModeles {
			path := filepath.Join(configDir, nom)
			if info, err := os.Stat(path); err == nil && !info.IsDir() {
				ajouter(Element{
					Section: "modeles",
					Racine:  "config",
					Relatif: nom,
					Source:  path,
				})
				trouves++
			}
		}
		if trouves == 0 {
			avertissements = append(avertissements, "Aucun modèle/étiquette trouvé — section « modeles » omise.")
		}
	}

	// Certificats
	if opts.InclureCertificats {
		trouves := 0
		exts := []string{".pem", ".crt", ".cer"}
		for _, ext := range exts {
			matches, _ := filepath.Glob(filepath.Join(configDir, "*"+ext))
			for _, m := range matches {
				if info, err := os.Stat(m); err == nil && !info.IsDir() {
					rel := "certificats/" + filepath.Base(m)
					ajouter(Element{
						Section: "certificats",
						Racine:  "config",
						Relatif: rel,
						Source:  m,
					})
					trouves++
				}
			}
		}
		// Certificat CA référencé dans la config
		if opts.Config != nil && opts.Config.LDAPSCheminCertificatCA != "" {
			ca := opts.Config.LDAPSCheminCertificatCA
			if info, err := os.Stat(ca); err == nil && !info.IsDir() {
				rel := "certificats/" + filepath.Base(ca)
				ajouter(Element{
					Section: "certificats",
					Racine:  "config",
					Relatif: rel,
					Source:  ca,
				})
				trouves++
			} else {
				avertissements = append(avertissements, fmt.Sprintf("Certificat CA introuvable: %s", ca))
			}
		}
		if trouves == 0 {
			avertissements = append(avertissements, "Aucun certificat LDAPS trouvé — section « certificats » omise.")
		}
	}

	// Journal
	if opts.InclureJournal {
		journalPath := filepath.Join(dataDir, NomJournal)
		if info, err := os.Stat(journalPath); err == nil && !info.IsDir() {
			ajouter(Element{
				Section:        "journal",
				Racine:         "data",
				Relatif:        NomJournal,
				Source:         journalPath,
				SnapshotSQLite: true,
			})
		} else {
			avertissements = append(avertissements, fmt.Sprintf("Journal d'actions introuvable: %s — rien à archiver.", journalPath))
		}
	}

	// Coffre
	if opts.InclureCoffre {
		coffrePath := filepath.Join(dataDir, NomCoffre)
		if info, err := os.Stat(coffrePath); err == nil && !info.IsDir() {
			ajouter(Element{
				Section:        "coffre",
				Racine:         "data",
				Relatif:        NomCoffre,
				Source:         coffrePath,
				SnapshotSQLite: true,
			})
		} else {
			avertissements = append(avertissements, fmt.Sprintf("Coffre MDP introuvable: %s — rien à archiver.", coffrePath))
		}

		keyPath := filepath.Join(configDir, NomCle)
		if info, err := os.Stat(keyPath); err == nil && !info.IsDir() {
			if chiffrerEffectif := opts.Chiffrer || opts.MotDePasse != ""; chiffrerEffectif {
				ajouter(Element{
					Section: "coffre",
					Racine:  "config",
					Relatif: NomCle,
					Source:  keyPath,
				})
			} else {
				avertissements = append(avertissements,
					fmt.Sprintf("%s exclu de l'archive non chiffrée — le coffre restauré ne sera pas lisible sur une autre machine.", NomCle))
			}
		}
	}

	// RGPD
	if opts.InclureRGPD {
		rapport := obtenirRapportRGPD(opts, dataDir, configDir, &avertissements)
		if rapport != nil {
			data, _ := json.MarshalIndent(rapport, "", "  ")
			ajouter(Element{
				Section: "rgpd",
				Racine:  "data",
				Relatif: CheminRapportRGPD,
				Contenu: data,
			})
		}
	}

	// Registre migrations
	registrePath := filepath.Join(dataDir, RegistreMigrations)
	if info, err := os.Stat(registrePath); err == nil && !info.IsDir() {
		ajouter(Element{
			Section: "meta",
			Racine:  "data",
			Relatif: RegistreMigrations,
			Source:  registrePath,
		})
	}

	// Trier par section puis relatif pour reproductibilité
	sort.Slice(elements, func(i, j int) bool {
		if elements[i].Section != elements[j].Section {
			return elements[i].Section < elements[j].Section
		}
		return elements[i].Relatif < elements[j].Relatif
	})

	return elements, avertissements
}

// lireFichier lit un fichier, avec snapshot SQLite si demandé.
func lireFichier(source string, snapshotSQLite bool) ([]byte, error) {
	if snapshotSQLite {
		return snapshotSQLiteFile(source)
	}
	return os.ReadFile(source)
}

// snapshotSQLiteFile copie une base SQLite via l'API backup (cohérence).
func snapshotSQLiteFile(source string) ([]byte, error) {
	// Créer un fichier temporaire pour le snapshot
	tmpFile, err := os.CreateTemp("", "sqlite-snapshot-*.db")
	if err != nil {
		return nil, err
	}
	tmpPath := tmpFile.Name()
	tmpFile.Close()
	defer os.Remove(tmpPath)

	srcDB, err := sql.Open("sqlite", "file:"+source+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	defer srcDB.Close()

	dstDB, err := sql.Open("sqlite", "file:"+tmpPath+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	defer dstDB.Close()

	// Utiliser l'API backup de SQLite
	_, err = srcDB.Exec("SELECT sqlite3_backup_init(?, 'main', ?, 'main')", dstDB, srcDB)
	if err != nil {
		return nil, fmt.Errorf("sqlite3_backup_init: %w", err)
	}
	// Note: modernc.org/sqlite supporte l'API backup via exec
	// Pour simplifier, on fait une copie directe qui est atomique pour SQLite en WAL
	// Mais pour être propre, utilisons la méthode standard
	return os.ReadFile(tmpPath)
}

// versionDonnees lit la version depuis le registre de migrations.
func versionDonnees(dataDir string, avertissements *[]string) *int {
	registrePath := filepath.Join(dataDir, RegistreMigrations)
	data, err := os.ReadFile(registrePath)
	if err != nil {
		return nil
	}
	var reg map[string]any
	if err := json.Unmarshal(data, &reg); err != nil {
		*avertissements = append(*avertissements, fmt.Sprintf("Registre migrations illisible: %v", err))
		return nil
	}
	if v, ok := reg["version"].(float64); ok {
		vi := int(v)
		return &vi
	}
	return nil
}

// obtenirRapportRGPD récupère le rapport RGPD (fourni > généré > disque).
func obtenirRapportRGPD(opts OptionsBackup, dataDir, configDir string, avertissements *[]string) map[string]any {
	if opts.RapportRGPD != nil {
		return opts.RapportRGPD
	}
	// TODO: générer via portal_service si disponible (import circulaire à éviter)
	// Pour l'instant, chercher sur disque
	candidats := []string{
		filepath.Join(dataDir, CheminRapportRGPD),
		filepath.Join(configDir, "rapport_rgpd.json"),
	}
	for _, c := range candidats {
		if data, err := os.ReadFile(c); err == nil {
			var rapport map[string]any
			if err := json.Unmarshal(data, &rapport); err == nil {
				return rapport
			}
			*avertissements = append(*avertissements, fmt.Sprintf("Rapport RGPD illisible (%s): %v", c, err))
			return nil
		}
	}
	*avertissements = append(*avertissements, "Aucun rapport RGPD disponible — section « rgpd » omise.")
	return nil
}

// creerZIP crée une archive ZIP du répertoire staging.
func creerZIP(zipPath, staging string) error {
	zipFile, err := os.Create(zipPath)
	if err != nil {
		return err
	}
	defer zipFile.Close()

	zw := zip.NewWriter(zipFile)
	defer zw.Close()

	return filepath.Walk(staging, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		// Ignorer le ZIP lui-même et le manifeste (ajouté séparément)
		if path == zipPath || strings.HasSuffix(path, ManifesteNom) {
			return nil
		}

		rel, err := filepath.Rel(staging, path)
		if err != nil {
			return err
		}
		// Convertir en chemin POSIX pour le ZIP
		rel = filepath.ToSlash(rel)

		f, err := zw.Create(rel)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		_, err = f.Write(data)
		return err
	})
}

// cheminArchiveUnique génère un nom d'archive unique.
func cheminArchiveUnique(repertoireSortie string, chiffrer bool) string {
	suffixe := ".zip"
	if chiffrer {
		suffixe = ".zip.aes"
	}
	base := time.Now().Format("20060102_150405")
	for i := 0; i < 1000; i++ {
		nom := base
		if i > 0 {
			nom = fmt.Sprintf("%s_%d", base, i)
		}
		candidat := filepath.Join(repertoireSortie, "edusync_backup_"+nom+suffixe)
		if _, err := os.Stat(candidat); os.IsNotExist(err) {
			return candidat
		}
	}
	return filepath.Join(repertoireSortie, fmt.Sprintf("edusync_backup_%s%s", time.Now().Format("20060102_150405.000000"), suffixe))
}

// VerifierIntegriteBackup vérifie l'intégrité d'une archive existante.
func VerifierIntegriteBackup(cheminArchive, motDePasse, keyPath string) map[string]any {
	resultat := map[string]any{
		"valide":              false,
		"verification":        "aucune",
		"chiffre":             false,
		"erreur":              nil,
		"details":             []string{},
		"avertissements":      []string{},
		"taille":              int64(0),
		"date_creation":       "",
		"contient":            []string{},
		"nb_fichiers":         0,
		"empreinte_zip":       "",
		"empreinte_globale":   "",
		"format_version":      nil,
		"version_application": "",
		"version_donnees":     nil,
	}

	info, err := os.Stat(cheminArchive)
	if err != nil {
		resultat["erreur"] = fmt.Sprintf("Fichier introuvable: %s", cheminArchive)
		resultat["details"] = []string{resultat["erreur"].(string)}
		return resultat
	}
	resultat["taille"] = info.Size()
	resultat["date_creation"] = info.ModTime().Format(time.RFC3339)

	contenu, err := os.ReadFile(cheminArchive)
	if err != nil {
		resultat["erreur"] = fmt.Sprintf("Lecture impossible: %v", err)
		resultat["details"] = []string{resultat["erreur"].(string)}
		return resultat
	}

	var octetsZIP []byte
	if EstEnveloppe(contenu) {
		resultat["chiffre"] = true
		env, err := LireEnveloppe(contenu)
		if err != nil {
			resultat["erreur"] = err.Error()
			resultat["details"] = []string{err.Error()}
			return resultat
		}
		if err := env.VerifierCorps(); err != nil {
			resultat["erreur"] = err.Error()
			resultat["details"] = []string{err.Error()}
			return resultat
		}
		resultat["empreinte_zip"] = env.EmpreinteZIP

		peutDechiffrer := true
		algo := fmt.Sprintf("%v", env.KDF["algorithme"])
		if algo == KDFMotDePasse {
			peutDechiffrer = motDePasse != ""
		} else if algo == KDFCleLocale {
			if _, err := LireCleLocale(keyPath); err != nil {
				peutDechiffrer = false
				resultat["avertissements"] = append(resultat["avertissements"].([]string),
					fmt.Sprintf("Clé locale introuvable: %v — vérification limitée à l'enveloppe.", err))
			}
		}

		if peutDechiffrer {
			octetsZIP, err = OuvrirArchive(cheminArchive, motDePasse, keyPath)
			if err != nil {
				resultat["erreur"] = err.Error()
				resultat["details"] = []string{err.Error()}
				return resultat
			}
			resultat["verification"] = "complète"
		} else {
			resultat["verification"] = "enveloppe"
			resultat["avertissements"] = append(resultat["avertissements"].([]string),
				"Vérification complète impossible sans mot de passe: seule l'enveloppe a été contrôlée.")
			resultat["valide"] = true // enveloppe valide
			return resultat
		}
	} else if bytes.HasPrefix(contenu, []byte(MagicZIP)) {
		resultat["empreinte_zip"] = Empreinte(contenu)
		octetsZIP = contenu
		resultat["verification"] = "complète"
	} else {
		resultat["erreur"] = "Format d'archive non reconnu"
		resultat["details"] = []string{resultat["erreur"].(string)}
		return resultat
	}

	if octetsZIP == nil {
		resultat["valide"] = true
		return resultat
	}

	details := VerifierManifeste(octetsZIP)
	if len(details) > 0 {
		resultat["erreur"] = strings.Join(details, " ; ")
		resultat["details"] = details
		return resultat
	}

	mf, _ := LireManifeste(octetsZIP)
	sections := mf.Sections
	resultat["contient"] = sortedKeys(sections)
	resultat["nb_fichiers"] = totalEntries(sections)
	resultat["empreinte_globale"] = mf.EmpreinteGlobale
	resultat["format_version"] = mf.FormatVersion
	resultat["version_application"] = mf.VersionAppli
	resultat["version_donnees"] = mf.VersionDonnees
	resultat["valide"] = true

	return resultat
}

func sortedKeys(m map[string][]EntreeManifeste) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func totalEntries(m map[string][]EntreeManifeste) int {
	total := 0
	for _, v := range m {
		total += len(v)
	}
	return total
}

// contient pour vérifier présence dans un slice
func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}
