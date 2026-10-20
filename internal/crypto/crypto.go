// Package crypto chiffre les données locales d'EduSync-AD (config
// mémorisée, coffre de mots de passe).
//
// Le format est **strictement identique** à celui de la version Python
// (src/edusync_ad/core/crypto.py) :
//
//	clef brute AES-256 (32 octets) stockée dans secret.key (0600)
//	empreinte = base64( nonce(12) || ciphertext(AES-256-GCM) )
//
// Cette parité permet la migration sans réinitialisation : un
// secret.key produit par Python est lu par Go et inversement
// (cf. ROADMAP_GO_MIGRATION.md §8).
package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const (
	// KeySize est la taille de la clé AES (AES-256).
	KeySize = 32
	// NonceSize est la taille du nonce AES-GCM (96 bits, valeur de
	// référence recommandée).
	NonceSize = 12
)

var (
	// ErrInvalidKey signale une clé de taille incorrecte.
	ErrInvalidKey = errors.New("crypto: clé de chiffrement invalide (32 octets attendus)")
	// ErrInvalidToken signale une empreinte illisible (base64, tronquée…).
	ErrInvalidToken = errors.New("crypto: empreinte de chiffrement illisible")
	// ErrDecryptFailed signale un déchiffrement impossible (clé fausse ou
	// donnée altérée). Ne jamais différencier les deux cas côté applicatif :
	// cela permettrait une attaque par oracle.
	ErrDecryptFailed = errors.New("crypto: déchiffrement impossible (clé ou donnée incorrecte)")
)

// NewKey génère une nouvelle clé AES-256 aléatoire.
func NewKey() ([]byte, error) {
	key := make([]byte, KeySize)
	if _, err := io.ReadFull(rand.Reader, key); err != nil {
		return nil, fmt.Errorf("crypto: génération de clé: %w", err)
	}
	return key, nil
}

// LoadOrCreateKey lit la clé située à « path ». Si le fichier n'existe pas,
// il est créé (permissions 0600, propriétaire uniquement) — exactement
// comme get_or_create_key() côté Python.
//
// Une clé existante n'est jamais réécrit : la réécrire reviendrait à
// rendre irrécupérables toutes les données déjà chiffrées.
func LoadOrCreateKey(path string) ([]byte, error) {
	if path == "" {
		return nil, fmt.Errorf("crypto: chemin de clé vide")
	}

	data, err := os.ReadFile(path)
	switch {
	case err == nil:
		if len(data) != KeySize {
			return nil, fmt.Errorf("%w: %d octets lus dans %s", ErrInvalidKey, len(data), path)
		}
		return data, nil
	case !errors.Is(err, os.ErrNotExist):
		return nil, fmt.Errorf("crypto: lecture de %s: %w", path, err)
	}

	// Création du fichier de clé.
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("crypto: création du dossier %s: %w", dir, err)
		}
	}

	key, err := NewKey()
	if err != nil {
		return nil, err
	}

	// O_EXCL : si un autre processus crée la clé entre-temps, on relit la sienne.
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		if errors.Is(err, os.ErrExist) {
			return LoadOrCreateKey(path)
		}
		return nil, fmt.Errorf("crypto: création de %s: %w", path, err)
	}
	if _, err := f.Write(key); err != nil {
		f.Close() //nolint:errcheck // l'erreur de fermeture n'apporte rien ici
		_ = os.Remove(path)
		return nil, fmt.Errorf("crypto: écriture de %s: %w", path, err)
	}
	if err := f.Close(); err != nil {
		return nil, fmt.Errorf("crypto: fermeture de %s: %w", path, err)
	}
	return key, nil
}

// Encrypt chiffre « plaintext » avec AES-256-GCM et renvoie
// base64(nonce || ciphertext), le format lisible par la version Python.
func Encrypt(key []byte, plaintext string) (string, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return "", err
	}

	nonce := make([]byte, NonceSize)
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", fmt.Errorf("crypto: génération du nonce: %w", err)
	}

	ciphertext := gcm.Seal(nonce, nonce, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(ciphertext), nil
}

// Decrypt inverse Encrypt. En cas d'échec (clé fausse, empreinte altérée),
// ErrDecryptFailed est renvoyé — jamais d'information sur la cause précise.
func Decrypt(key []byte, token string) (string, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return "", err
	}

	raw, err := base64.StdEncoding.DecodeString(token)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrInvalidToken, err)
	}
	if len(raw) <= NonceSize {
		return "", ErrInvalidToken
	}

	nonce, ciphertext := raw[:NonceSize], raw[NonceSize:]
	plaintext, err := gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return "", ErrDecryptFailed
	}
	return string(plaintext), nil
}

func newGCM(key []byte) (cipher.AEAD, error) {
	if len(key) != KeySize {
		return nil, fmt.Errorf("%w: %d octets fournis", ErrInvalidKey, len(key))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("crypto: AES-256 indisponible: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("crypto: mode GCM indisponible: %w", err)
	}
	return gcm, nil
}

// PBKDF2 dérive une clé via PBKDF2-HMAC-SHA256 (RFC 2898).
// Identique à hashlib.pbkdf2_hmac("sha256", ...) côté Python.
func PBKDF2(motDePasse, sel []byte, iterations, longueurCle int) []byte {
	prf := hmac.New(sha256.New, motDePasse)
	tailleEmpreinte := prf.Size()
	nombreBlocs := (longueurCle + tailleEmpreinte - 1) / tailleEmpreinte

	var compteur [4]byte
	cle := make([]byte, 0, nombreBlocs*tailleEmpreinte)
	u := make([]byte, tailleEmpreinte)

	for bloc := 1; bloc <= nombreBlocs; bloc++ {
		prf.Reset()
		prf.Write(sel)
		compteur[0] = byte(bloc >> 24)
		compteur[1] = byte(bloc >> 16)
		compteur[2] = byte(bloc >> 8)
		compteur[3] = byte(bloc)
		prf.Write(compteur[:4])
		cle = prf.Sum(cle)
		t := cle[len(cle)-tailleEmpreinte:]
		copy(u, t)

		for n := 2; n <= iterations; n++ {
			prf.Reset()
			prf.Write(u)
			u = u[:0]
			u = prf.Sum(u)
			for i := range u {
				t[i] ^= u[i]
			}
		}
	}
	return cle[:longueurCle]
}
