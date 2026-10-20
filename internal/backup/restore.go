// Package backup implémente la restauration d'une sauvegarde EduSync AD.
package backup

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	_ "modernc.org/sqlite"

	"github.com/estemobs/EduSync-AD/internal/config"
	"github.com/estemobs/EduSync-AD/internal/models"
)

// OptionsRestore configure la restauration.
type OptionsRestore struct {
	CheminArchive       string
	MotDePasse          string
	CheminDestination   string // "" = répertoires natifs
	KeyPath             string
	AppliquerMigrations bool
	Progress            func(int, int) // callback progression
}

// ResultatRestore contient le résultat de la restauration.
type ResultatRestore struct {
	Reussite         bool
	ElementsRestores []string
	Erreurs          []string
	Avertissements   []string
	Destination      string
	Integrite        map[string]any
	NbFichiers       int
	VersionDonnees   *int
	Migrations       *MigrationResult
}

// RestorerDansNouvelleMachine restaure une sauvegarde.
func RestorerDansNouvelleMachine(ctx context.Context, opts OptionsRestore) (*ResultatRestore, error) {
	resultat := &ResultatRestore{
		Destination: func() string {
			if opts.CheminDestination != "" {
				return opts.CheminDestination
			}
			return "natif"
		}(),
		Integrite: map[string]any{
			"valide":       false,
			"verification": "aucune",
			"details":      []string{},
		},
	}

	// 1. Vérifier l'archive
	archive := opts.CheminArchive
	if _, err := os.Stat(archive); err != nil {
		resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Archive introuvable: %s", archive))
		return resultat, nil
	}

	// 2. Lecture / déchiffrement
	octetsZIP, err := OuvrirArchive(archive, opts.MotDePasse, opts.KeyPath)
	if err != nil {
		resultat.Erreurs = append(resultat.Erreurs, err.Error())
		resultat.Integrite = map[string]any{
			"valide":       false,
			"verification": "aucune",
			"details":      []string{err.Error()},
		}
		return resultat, nil
	}

	// 3. Intégrité complète
	details := VerifierManifeste(octetsZIP)
	if len(details) > 0 {
		resultat.Erreurs = append(resultat.Erreurs, "Archive refusée — intégrité non vérifiée: "+strings.Join(details, " ; "))
		resultat.Integrite = map[string]any{
			"valide":       false,
			"verification": "complète",
			"details":      details,
		}
		return resultat, nil
	}
	resultat.Integrite = map[string]any{
		"valide":       true,
		"verification": "complète",
		"details":      []string{},
	}

	manifeste, err := LireManifeste(octetsZIP)
	if err != nil {
		resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Manifeste illisible: %v", err))
		return resultat, nil
	}

	// 4. Racines de destination
	var racineConfig, racineDonnees string
	var avertissementsRacines []string
	if opts.CheminDestination != "" {
		base := opts.CheminDestination
		racineConfig = filepath.Join(base, "config")
		racineDonnees = filepath.Join(base, "data")
	} else {
		var err error
		racineConfig, err = config.ConfigDir()
		if err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Répertoire config natif: %v", err))
			return resultat, nil
		}
		racineDonnees, err = config.DataDir()
		if err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Répertoire données natif: %v", err))
			return resultat, nil
		}
		avertissementsRacines = []string{
			"Destination non précisée: restauration dans les répertoires natifs de l'application (configuration et données actuelles écrasées).",
		}
	}
	resultat.Avertissements = append(resultat.Avertissements, avertissementsRacines...)

	// 5. Compatibilité versions
	if !verifierVersions(manifeste, racineDonnees, &resultat.Erreurs) {
		return resultat, nil
	}

	// 6. Extraction
	entries := extraireEntrees(manifeste)
	racines := map[string]string{"config": racineConfig, "data": racineDonnees}

	zr, err := zip.NewReader(bytes.NewReader(octetsZIP), int64(len(octetsZIP)))
	if err != nil {
		resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("ZIP illisible: %v", err))
		return resultat, nil
	}

	files := make(map[string]*zip.File)
	for _, f := range zr.File {
		files[f.Name] = f
	}

	if opts.Progress != nil {
		opts.Progress(0, len(entries))
	}

	for i, entree := range entries {
		select {
		case <-ctx.Done():
			return resultat, ctx.Err()
		default:
		}

		base := racines[entree.Racine]
		if base == "" {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Racine inconnue: %s", entree.Racine))
			continue
		}

		cible := filepath.Join(base, entree.Relatif)
		// Double garde anti zip-slip
		if !strings.HasPrefix(filepath.Clean(cible), filepath.Clean(base)+string(os.PathSeparator)) && cible != base {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Chemin hors destination refusé: %s", entree.Relatif))
			continue
		}

		zf, ok := files[entree.Chemin]
		if !ok {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Fichier absent du ZIP: %s", entree.Chemin))
			continue
		}

		rc, err := zf.Open()
		if err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Ouverture %s: %v", entree.Chemin, err))
			continue
		}
		data, err := io.ReadAll(rc)
		rc.Close()
		if err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Lecture %s: %v", entree.Chemin, err))
			continue
		}

		if err := os.MkdirAll(filepath.Dir(cible), 0o700); err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Création dossier %s: %v", filepath.Dir(cible), err))
			continue
		}

		// Permissions 0600 pour secret.key
		perm := os.FileMode(0o600)
		if err := os.WriteFile(cible, data, perm); err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Écriture %s: %v", entree.Relatif, err))
			continue
		}

		resultat.ElementsRestores = append(resultat.ElementsRestores, entree.Racine+"/"+entree.Relatif)
		resultat.NbFichiers++

		if opts.Progress != nil {
			opts.Progress(i+1, len(entries))
		}
	}

	// 7. Avertissements post-restauration
	alerter(racineConfig, entries, &resultat.Avertissements)

	// 8. Migration des données héritées
	if opts.AppliquerMigrations && len(resultat.Erreurs) == 0 {
		migrations, err := MigrerDonneesVnVn1(racineConfig, racineDonnees)
		if err != nil {
			resultat.Erreurs = append(resultat.Erreurs, fmt.Sprintf("Migration impossible: %v", err))
		} else {
			resultat.Migrations = migrations
			if !migrations.Reussite {
				resultat.Erreurs = append(resultat.Erreurs, "Migration échouée: "+strings.Join(migrations.Erreurs, " ; "))
			}
		}
	}

	resultat.VersionDonnees = manifeste.VersionDonnees
	resultat.Reussite = len(resultat.Erreurs) == 0
	return resultat, nil
}

// extraireEntrees aplatit les sections du manifeste.
func extraireEntrees(mf *Manifeste) []EntreeManifeste {
	var entries []EntreeManifeste
	for _, liste := range mf.Sections {
		for _, e := range liste {
			entries = append(entries, e)
		}
	}
	return entries
}

// verifierVersions refuse une archive/destination trop récente.
func verifierVersions(mf *Manifeste, racineDonnees string, erreurs *[]string) bool {
	// Version de l'archive
	if mf.VersionDonnees != nil && *mf.VersionDonnees > models.DataVersionCurrent {
		*erreurs = append(*erreurs, fmt.Sprintf(
			"Archive créée avec version de données %d (max supporté: %d) — application à mettre à jour.",
			*mf.VersionDonnees, models.DataVersionCurrent))
		return false
	}

	// Version de la destination
	registrePath := filepath.Join(racineDonnees, RegistreMigrations)
	if _, err := os.Stat(registrePath); err == nil {
		data, _ := os.ReadFile(registrePath)
		var reg map[string]any
		if json.Unmarshal(data, &reg) == nil {
			if v, ok := reg["version"].(float64); ok {
				if int(v) > models.DataVersionCurrent {
					*erreurs = append(*erreurs, fmt.Sprintf(
						"Destination version %d (max supporté: %d) — restauration refusée pour ne pas écraser des données plus récentes.",
						int(v), models.DataVersionCurrent))
					return false
				}
			}
		}
	}
	return true
}

// alerter ajoute des avertissements post-restauration.
func alerter(racineConfig string, entries []EntreeManifeste, avertissements *[]string) {
	relatifs := make(map[string]bool)
	for _, e := range entries {
		relatifs[e.Relatif] = true
	}

	// Coffre sans clé
	coffreRestaure := false
	for rel := range relatifs {
		if strings.HasSuffix(rel, NomCoffre) {
			coffreRestaure = true
			break
		}
	}
	if coffreRestaure && !relatifs[NomCle] {
		*avertissements = append(*avertissements,
			"Coffre MDP restauré sans sa clé de chiffrement: les mots de passe ne seront pas lisibles depuis cette machine.")
	}

	// Certificat CA référencé
	configPath := filepath.Join(racineConfig, NomAppConfig)
	if data, err := os.ReadFile(configPath); err == nil {
		var cfg map[string]any
		if json.Unmarshal(data, &cfg) == nil {
			if ldaps, ok := cfg["ldaps"].(map[string]any); ok {
				if ca, ok := ldaps["chemin_certificat_ca"].(string); ok && ca != "" {
					if _, err := os.Stat(ca); err != nil {
						*avertissements = append(*avertissements,
							fmt.Sprintf("Certificat CA référencé introuvable (%s): repositionner le fichier restauré et mettre à jour ldaps_chemin_certificat_ca.", ca))
					}
				}
			}
		}
	}
}
