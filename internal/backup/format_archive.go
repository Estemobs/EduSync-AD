// Package backup implémente la sauvegarde et la restauration d'EduSync AD.
//
// Format d'archive (identique à Python) :
//   - ZIP clair : commence par PK\x03\x04, lisible sans clé, sans secret.key
//   - Enveloppe chiffrée : première ligne "EDUSYNC-ARCHIVE", deuxième ligne en-tête JSON,
//     reste : jeton AES-256-GCM (base64 du nonce(12) || ciphertext || tag)
//
// L'enveloppe réutilise internal/crypto (encrypt/decrypt, AES-256-GCM).
// Clé dérivée : PBKDF2-HMAC-SHA256 (mot de passe) OU secret.key locale.
package backup

import (
	"archive/zip"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/estemobs/EduSync-AD/internal/config"
	"github.com/estemobs/EduSync-AD/internal/crypto"
)

const (
	// MagicEnveloppe identifie une archive chiffrée.
	MagicEnveloppe = "EDUSYNC-ARCHIVE"
	// MagicZIP identifie un ZIP clair.
	MagicZIP = "PK\x03\x04"

	// FormatArchive identifie le format dans le manifeste.
	FormatArchive = "edusync-backup"
	// VersionFormatArchive version du format (inchangée depuis Python).
	VersionFormatArchive = 1

	AlgorithmeChiffrement = "AES-256-GCM"
	KDFMotDePasse         = "pbkdf2-hmac-sha256"
	KDFCleLocale          = "cle-locale"
	KDFIterations         = 200_000
	KDFIterationsMaximum  = 5_000_000
	SelTailleOctets       = 16
	CleTailleOctets       = 32

	ManifesteNom  = "manifest.json"
	PrefixeMembre = "payload"
)

// Racines de restauration autorisées.
var Racines = []string{"config", "data"}

// ArchiveError est une erreur de sauvegarde/restauration.
type ArchiveError struct {
	msg string
}

func (e *ArchiveError) Error() string { return e.msg }

func newArchiveError(format string, args ...any) *ArchiveError {
	return &ArchiveError{msg: fmt.Sprintf(format, args...)}
}

// Empreinte calcule le SHA-256 hexadécimal de data.
func Empreinte(data []byte) string {
	return hex.EncodeToString(sha256.New().Sum(data))
}

// EmpreinteFichier calcule le SHA-256 hexadécimal d'un fichier par blocs.
func EmpreinteFichier(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 65536)
	for {
		n, err := f.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// CalculerEmpreinteGlobale calcule l'empreinte globale du manifeste.
func CalculerEmpreinteGlobale(entrees []map[string]any) string {
	h := sha256.New()
	// Trier par chemin pour la reproductibilité
	chemins := make([]string, len(entrees))
	for i, e := range entrees {
		chemins[i] = fmt.Sprintf("%v", e["chemin"])
	}
	// Tri simple (pas de sort pour éviter l'allocation, on suppose déjà trié en entrée)
	for _, e := range entrees {
		chemin := fmt.Sprintf("%v", e["chemin"])
		emp := fmt.Sprintf("%v", e["sha256"])
		h.Write([]byte(chemin))
		h.Write([]byte{0})
		h.Write([]byte(emp))
	}
	return hex.EncodeToString(h.Sum(nil))
}

// CheminCleLocale retourne le chemin de la clé locale.
func CheminCleLocale(keyPath string) string {
	if keyPath != "" {
		return keyPath
	}
	dir, _ := config.ConfigDir()
	return filepath.Join(dir, "secret.key")
}

// LireCleLocale charge la clé locale si elle existe (sans la créer).
func LireCleLocale(keyPath string) ([]byte, error) {
	chemin := CheminCleLocale(keyPath)
	data, err := os.ReadFile(chemin)
	if err != nil {
		return nil, err
	}
	if len(data) != CleTailleOctets {
		return nil, fmt.Errorf("clé locale invalide: %d octets (attendu %d)", len(data), CleTailleOctets)
	}
	return data, nil
}

// DeriverCleMotDePasse dérive une clé AES-256 via PBKDF2-HMAC-SHA256.
func DeriverCleMotDePasse(motDePasse string, sel []byte, iterations int) ([]byte, error) {
	if iterations <= 0 || iterations > KDFIterationsMaximum {
		return nil, newArchiveError("itérations de dérivation invalides: %d", iterations)
	}
	return crypto.PBKDF2([]byte(motDePasse), sel, iterations, CleTailleOctets), nil
}

// validerEmpreinte vérifie qu'une chaîne est un SHA-256 hex valide (64 chars).
func validerEmpreinte(valeur, contexte string) (string, error) {
	if len(valeur) != 64 {
		return "", newArchiveError("empreinte SHA-256 invalide dans %s: longueur %d", contexte, len(valeur))
	}
	if _, err := hex.DecodeString(valeur); err != nil {
		return "", newArchiveError("empreinte SHA-256 invalide dans %s: %v", contexte, err)
	}
	return strings.ToLower(valeur), nil
}

// ecrireRestreint écrit un fichier avec permissions 0600.
func ecrireRestreint(chemin string, contenu []byte) error {
	if err := os.MkdirAll(filepath.Dir(chemin), 0o700); err != nil {
		return err
	}
	return os.WriteFile(chemin, contenu, 0o600)
}

// EcrireArchive écrit l'archive (ZIP clair ou enveloppe chiffrée).
func EcrireArchive(chemin string, octetsZIP []byte, chiffrer bool, motDePasse, keyPath string) (map[string]any, error) {
	if motDePasse != "" {
		chiffrer = true
	}
	empreinteZIP := Empreinte(octetsZIP)

	if !chiffrer {
		if err := ecrireRestreint(chemin, octetsZIP); err != nil {
			return nil, newArchiveError("écriture ZIP impossible: %v", err)
		}
		return map[string]any{
			"chiffre":       false,
			"algorithme":    "aucun",
			"kdf":           nil,
			"empreinte_zip": empreinteZIP,
			"cle":           nil,
		}, nil
	}

	var cle []byte
	var kdf map[string]any
	var sourceCle string

	if motDePasse != "" {
		sel := make([]byte, SelTailleOctets)
		if _, err := rand.Read(sel); err != nil {
			return nil, newArchiveError("génération sel: %v", err)
		}
		cleDerivee, err := DeriverCleMotDePasse(motDePasse, sel, KDFIterations)
		if err != nil {
			return nil, err
		}
		cle = cleDerivee
		kdf = map[string]any{
			"algorithme": KDFMotDePasse,
			"iterations": KDFIterations,
			"sel":        base64.StdEncoding.EncodeToString(sel),
		}
		sourceCle = "mot_de_passe"
	} else {
		cleLocale, err := crypto.LoadOrCreateKey(keyPath)
		if err != nil {
			return nil, newArchiveError("clé locale inaccessible: %v", err)
		}
		cle = cleLocale
		kdf = map[string]any{"algorithme": KDFCleLocale}
		sourceCle = "locale"
	}

	// Chiffrer le ZIP (base64 -> encrypt -> base64 du jeton)
	zipB64 := base64.StdEncoding.EncodeToString(octetsZIP)
	jeton, err := crypto.Encrypt(cle, zipB64)
	if err != nil {
		return nil, newArchiveError("chiffrement: %v", err)
	}
	corps := []byte(jeton)

	enTete := map[string]any{
		"format":            FormatArchive,
		"format_version":    VersionFormatArchive,
		"chiffre":           true,
		"algorithme":        AlgorithmeChiffrement,
		"kdf":               kdf,
		"empreinte_zip":     empreinteZIP,
		"empreinte_contenu": Empreinte(corps),
	}
	enTeteJSON, _ := json.Marshal(enTete)

	contenu := []byte(MagicEnveloppe + "\n" + string(enTeteJSON) + "\n" + jeton)
	if err := ecrireRestreint(chemin, contenu); err != nil {
		return nil, newArchiveError("écriture enveloppe: %v", err)
	}

	return map[string]any{
		"chiffre":       true,
		"algorithme":    AlgorithmeChiffrement,
		"kdf":           kdf,
		"empreinte_zip": empreinteZIP,
		"cle":           sourceCle,
	}, nil
}

// Enveloppe représente l'en-tête d'une archive chiffrée.
type Enveloppe struct {
	FormatVersion    int
	Chiffre          bool
	KDF              map[string]any
	EmpreinteZIP     string
	EmpreinteContenu string
	Corps            []byte
}

// EstEnveloppe retourne vrai si le contenu commence par l'enveloppe.
func EstEnveloppe(contenu []byte) bool {
	return bytes.HasPrefix(contenu, []byte(MagicEnveloppe+"\n"))
}

// LireEnveloppe parse et valide une enveloppe (sans déchiffrer).
func LireEnveloppe(source []byte) (*Enveloppe, error) {
	if !EstEnveloppe(source) {
		if bytes.HasPrefix(source, []byte(MagicZIP)) {
			return nil, newArchiveError("archive ZIP clair, pas une enveloppe")
		}
		return nil, newArchiveError("format non reconnu")
	}

	parties := bytes.SplitN(source, []byte("\n"), 3)
	if len(parties) != 3 || len(parties[1]) == 0 || len(parties[2]) == 0 {
		return nil, newArchiveError("enveloppe tronquée")
	}

	var brut map[string]any
	if err := json.Unmarshal(parties[1], &brut); err != nil {
		return nil, newArchiveError("en-tête illisible: %v", err)
	}
	if brut["format"] != FormatArchive {
		return nil, newArchiveError("format inconnu: %v", brut["format"])
	}
	version := brut["format_version"]
	if v, ok := version.(float64); !ok || int(v) != VersionFormatArchive {
		return nil, newArchiveError("version format non gérée: %v", version)
	}
	if brut["chiffre"] != true {
		return nil, newArchiveError("champ 'chiffre' doit être true")
	}
	kdf, ok := brut["kdf"].(map[string]any)
	if !ok {
		return nil, newArchiveError("kdf absent")
	}

	empZIP, _ := validerEmpreinte(fmt.Sprintf("%v", brut["empreinte_zip"]), "en-tête")
	empContenu, _ := validerEmpreinte(fmt.Sprintf("%v", brut["empreinte_contenu"]), "en-tête")

	return &Enveloppe{
		FormatVersion:    VersionFormatArchive,
		Chiffre:          true,
		KDF:              kdf,
		EmpreinteZIP:     empZIP,
		EmpreinteContenu: empContenu,
		Corps:            parties[2],
	}, nil
}

// VerifierCorps vérifie l'intégrité du contenu chiffré (sans clé).
func (e *Enveloppe) VerifierCorps() error {
	if Empreinte(e.Corps) != e.EmpreinteContenu {
		return newArchiveError("empreinte contenu modifiée — archive altérée")
	}
	return nil
}

// cleDechiffrement obtient la clé pour déchiffrer l'enveloppe.
func cleDechiffrement(env *Enveloppe, motDePasse, keyPath string) ([]byte, error) {
	algo := fmt.Sprintf("%v", env.KDF["algorithme"])
	if algo == KDFMotDePasse {
		if motDePasse == "" {
			return nil, newArchiveError("mot de passe requis pour cette archive")
		}
		selB64, _ := env.KDF["sel"].(string)
		sel, err := base64.StdEncoding.DecodeString(selB64)
		if err != nil || len(sel) != SelTailleOctets {
			return nil, newArchiveError("sel invalide")
		}
		iter := KDFIterations
		if v, ok := env.KDF["iterations"].(float64); ok {
			iter = int(v)
		}
		return DeriverCleMotDePasse(motDePasse, sel, iter)
	}
	if algo == KDFCleLocale {
		cle, err := LireCleLocale(keyPath)
		if err != nil {
			return nil, newArchiveError("clé locale introuvable: %v", err)
		}
		return cle, nil
	}
	return nil, newArchiveError("algorithme KDF inconnu: %s", algo)
}

// OuvrirArchive déchiffre et retourne les octets du ZIP.
func OuvrirArchive(chemin, motDePasse, keyPath string) ([]byte, error) {
	contenu, err := os.ReadFile(chemin)
	if err != nil {
		return nil, newArchiveError("lecture archive: %v", err)
	}

	if EstEnveloppe(contenu) {
		env, err := LireEnveloppe(contenu)
		if err != nil {
			return nil, err
		}
		if err := env.VerifierCorps(); err != nil {
			return nil, err
		}
		cle, err := cleDechiffrement(env, motDePasse, keyPath)
		if err != nil {
			return nil, err
		}
		decore, err := crypto.Decrypt(cle, string(env.Corps))
		if err != nil {
			if fmt.Sprintf("%v", env.KDF["algorithme"]) == KDFCleLocale {
				return nil, newArchiveError("déchiffrement impossible: clé locale incorrecte ou archive altérée")
			}
			return nil, newArchiveError("déchiffrement impossible: mot de passe incorrect ou archive altérée")
		}
		octetsZIP, err := base64.StdEncoding.DecodeString(decore)
		if err != nil {
			return nil, newArchiveError("base64 invalide après déchiffrement")
		}
		if Empreinte(octetsZIP) != env.EmpreinteZIP {
			return nil, newArchiveError("empreinte ZIP modifiée — archive altérée")
		}
		return octetsZIP, nil
	}

	if bytes.HasPrefix(contenu, []byte(MagicZIP)) {
		return contenu, nil
	}

	return nil, newArchiveError("format non reconnu")
}

// --- Manifeste ---------------------------------------------------------------

// Manifeste représente le contenu de manifest.json.
type Manifeste struct {
	Format           string                       `json:"format"`
	FormatVersion    int                          `json:"format_version"`
	VersionAppli     string                       `json:"version_application"`
	VersionDonnees   *int                         `json:"version_donnees,omitempty"`
	GenerationDate   string                       `json:"generation_date"`
	Options          map[string]any               `json:"options"`
	Sections         map[string][]EntreeManifeste `json:"sections"`
	EmpreinteGlobale string                       `json:"empreinte_globale"`
	Avertissements   []string                     `json:"avertissements"`
}

// EntreeManifeste est une entrée du manifeste.
type EntreeManifeste struct {
	Chemin  string `json:"chemin"`
	Section string `json:"section"`
	Racine  string `json:"racine"`
	Relatif string `json:"relatif"`
	SHA256  string `json:"sha256"`
	Taille  int64  `json:"taille"`
}

// LireManifeste charge et valide le manifeste depuis un ZIP.
func LireManifeste(octetsZIP []byte) (*Manifeste, error) {
	zr, err := zip.NewReader(bytes.NewReader(octetsZIP), int64(len(octetsZIP)))
	if err != nil {
		return nil, newArchiveError("ZIP illisible: %v", err)
	}

	var mf *Manifeste
	for _, f := range zr.File {
		if f.Name == ManifesteNom {
			rc, err := f.Open()
			if err != nil {
				return nil, newArchiveError("manifeste illisible: %v", err)
			}
			data, _ := io.ReadAll(rc)
			rc.Close()
			if err := json.Unmarshal(data, &mf); err != nil {
				return nil, newArchiveError("manifeste JSON invalide: %v", err)
			}
			break
		}
	}
	if mf == nil {
		return nil, newArchiveError("manifeste absent")
	}
	if mf.Format != FormatArchive || mf.FormatVersion != VersionFormatArchive {
		return nil, newArchiveError("format/version manifeste invalide")
	}
	return mf, nil
}

// VerifierManifeste vérifie l'intégrité complète d'un ZIP.
func VerifierManifeste(octetsZIP []byte) []string {
	var erreurs []string

	zr, err := zip.NewReader(bytes.NewReader(octetsZIP), int64(len(octetsZIP)))
	if err != nil {
		return []string{fmt.Sprintf("ZIP illisible: %v", err)}
	}

	mf, err := LireManifeste(octetsZIP)
	if err != nil {
		return []string{err.Error()}
	}

	// Map pour accès rapide aux fichiers du ZIP
	files := make(map[string]*zip.File)
	for _, f := range zr.File {
		files[f.Name] = f
	}

	var entreesValides []EntreeManifeste
	for section, liste := range mf.Sections {
		for _, entree := range liste {
			// Valider chemin
			if !strings.HasPrefix(entree.Chemin, PrefixeMembre+"/"+section+"/") {
				erreurs = append(erreurs, fmt.Sprintf("entrée hors section: %s", entree.Chemin))
				continue
			}
			// Valider racine
			racineOK := false
			for _, r := range Racines {
				if entree.Racine == r {
					racineOK = true
					break
				}
			}
			if !racineOK {
				erreurs = append(erreurs, fmt.Sprintf("racine inconnue: %s", entree.Racine))
				continue
			}
			// Valider empreinte
			if _, err := validerEmpreinte(entree.SHA256, "manifeste"); err != nil {
				erreurs = append(erreurs, err.Error())
				continue
			}
			if entree.Taille < 0 {
				erreurs = append(erreurs, fmt.Sprintf("taille invalide: %s", entree.Chemin))
				continue
			}

			// Lire et vérifier le fichier
			zf, ok := files[entree.Chemin]
			if !ok {
				erreurs = append(erreurs, fmt.Sprintf("fichier absent: %s", entree.Chemin))
				continue
			}
			rc, err := zf.Open()
			if err != nil {
				erreurs = append(erreurs, fmt.Sprintf("lecture %s: %v", entree.Chemin, err))
				continue
			}
			data, _ := io.ReadAll(rc)
			rc.Close()

			if int64(len(data)) != entree.Taille {
				erreurs = append(erreurs, fmt.Sprintf("taille incohérente: %s", entree.Chemin))
			}
			if Empreinte(data) != entree.SHA256 {
				erreurs = append(erreurs, fmt.Sprintf("empreinte invalide: %s", entree.Chemin))
			}
			entreesValides = append(entreesValides, entree)
		}
	}

	// Empreinte globale
	if mf.EmpreinteGlobale == "" {
		erreurs = append(erreurs, "empreinte globale absente")
	} else if CalculerEmpreinteGlobale(toMapSlice(entreesValides)) != strings.ToLower(mf.EmpreinteGlobale) {
		erreurs = append(erreurs, "empreinte globale invalide — contenu modifié")
	}

	return erreurs
}

func toMapSlice(entrees []EntreeManifeste) []map[string]any {
	res := make([]map[string]any, len(entrees))
	for i, e := range entrees {
		res[i] = map[string]any{
			"chemin": e.Chemin,
			"sha256": e.SHA256,
		}
	}
	return res
}

// cheminSur valide un chemin relatif (pas d'absolu, pas de ..).
func cheminSur(chemin string) (string, error) {
	if chemin == "" || strings.Contains(chemin, "\\") {
		return "", newArchiveError("chemin invalide: %s", chemin)
	}
	parts := strings.Split(chemin, "/")
	for _, p := range parts {
		if p == ".." || p == "" {
			return "", newArchiveError("chemin hors archive: %s", chemin)
		}
	}
	return chemin, nil
}
