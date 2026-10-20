// Package models définit les types de domaine d'EduSync-AD.
//
// Ces types sont le reflet Go des dataclasses Python de
// src/edusync_ad/core/models.py. Les clés JSON restent en « snake_case »
// français afin que les fichiers échangés (configuration, exports)
// restent lisibles par les deux implémentations — cf. ROADMAP_GO_MIGRATION.md §8.
//
// Règle du projet : zéro perte fonctionnelle. Toute évolution de ce fichier
// doit rester compatible avec les fichiers produits par la version Python.
package models

// AccountType distingue les élèves et le personnel (politiques de mot de
// passe distinctes).
type AccountType string

const (
	AccountEleve     AccountType = "eleve"
	AccountPersonnel AccountType = "personnel"
)

// DoublonRule décrit la règle de résolution des doublons d'identifiants.
type DoublonRule string

const (
	DoublonSuffixeNumerique    DoublonRule = "suffixe_numerique"
	DoublonSuffixeNumeriqueSep DoublonRule = "suffixe_numerique_separateur"
	DoublonPrefixeNumerique    DoublonRule = "prefixe_numerique"
	DoublonLettresPrenom       DoublonRule = "lettres_prenom"
	DoublonLettresNom          DoublonRule = "lettres_nom"
	DoublonAnneeSuffixe        DoublonRule = "annee_suffixe"
)

// ValidDoublonRule accepte une valeur lue depuis un fichier de configuration.
func ValidDoublonRule(v string) bool {
	switch DoublonRule(v) {
	case DoublonSuffixeNumerique, DoublonSuffixeNumeriqueSep, DoublonPrefixeNumerique,
		DoublonLettresPrenom, DoublonLettresNom, DoublonAnneeSuffixe:
		return true
	}
	return false
}

// PrenomComposeRule décrit comment un prénom composé est réduit pour
// fabriquer l'identifiant.
type PrenomComposeRule string

const (
	PrenomPremierPrenom PrenomComposeRule = "premier_prenom"
	PrenomConcatenation PrenomComposeRule = "concatenation"
	PrenomTroncature    PrenomComposeRule = "troncature"
)

// ValidPrenomComposeRule accepte une valeur lue depuis un fichier de
// configuration.
func ValidPrenomComposeRule(v string) bool {
	switch PrenomComposeRule(v) {
	case PrenomPremierPrenom, PrenomConcatenation, PrenomTroncature:
		return true
	}
	return false
}

// PasswordPolicy — politique de mot de passe (élèves et personnels sont
// configurés séparément). Le champ PatternFixe est omis du JSON lorsqu'il
// est vide, ce qui correspond exactement à `pattern_fixe = None` côté Python.
type PasswordPolicy struct {
	Longueur            int    `json:"longueur"`
	Majuscules          bool   `json:"majuscules"`
	Chiffres            bool   `json:"chiffres"`
	CaracteresSpeciaux  bool   `json:"caracteres_speciaux"`
	MotDePasseIdentique bool   `json:"mot_de_passe_identique"`
	PatternFixe         string `json:"pattern_fixe,omitempty"`
}

// DefaultPasswordPolicy reprend les valeurs par défaut du dataclass Python.
func DefaultPasswordPolicy() PasswordPolicy {
	return PasswordPolicy{
		Longueur:            10,
		Majuscules:          true,
		Chiffres:            true,
		CaracteresSpeciaux:  false,
		MotDePasseIdentique: false,
	}
}

// RawUserRow est une ligne brute telle qu'importée depuis le CSV (avant
// génération). « ou » peut être vide : l'OU est alors résolue à partir de
// « classe » ou d'une OU par défaut.
type RawUserRow struct {
	Prenom        string `json:"prenom"`
	Nom           string `json:"nom"`
	OU            string `json:"ou"`
	Classe        string `json:"classe,omitempty"`
	Email         string `json:"email,omitempty"`
	DateNaissance string `json:"date_naissance,omitempty"`
	Numero        string `json:"numero,omitempty"`
}

// GeneratedUser est une ligne de prévisualisation prête à être écrite dans
// l'AD (résultat de Validate → Simulate, avant Execute).
type GeneratedUser struct {
	Source        RawUserRow `json:"source"`
	Identifiant   string     `json:"identifiant"`
	MotDePasse    string     `json:"mot_de_passe"`
	AdresseMail   string     `json:"adresse_mail"`
	OUCible       string     `json:"ou_cible"`
	Groupe        string     `json:"groupe,omitempty"`
	DoublonResolu bool       `json:"doublon_resolu"`
	DoublonAD     bool       `json:"doublon_ad"`
	Erreur        string     `json:"erreur,omitempty"`
}

// NomComplet retourne « prénom nom ».
func (u GeneratedUser) NomComplet() string {
	return u.Source.Prenom + " " + u.Source.Nom
}

// ConnectionInfo décrit la cible AD de la session courante.
type ConnectionInfo struct {
	Domaine     string `json:"domaine"`
	Controleur  string `json:"controleur"`
	Utilisateur string `json:"utilisateur"`
	UseLDAPS    bool   `json:"use_ldaps"`
}

// DepartRow est une ligne du fichier de départs.
type DepartRow struct {
	Identifiant string   `json:"identifiant"`
	UserDN      string   `json:"user_dn,omitempty"`
	NomComplet  string   `json:"nom_complet,omitempty"`
	GroupesDN   []string `json:"groupes_dns,omitempty"`
	Erreur      string   `json:"erreur,omitempty"`
}

// MigrationRow est une ligne du fichier de migration de classe.
type MigrationRow struct {
	Identifiant   string `json:"identifiant"`
	OUSource      string `json:"ou_source"`
	OUDestination string `json:"ou_destination"`
	UserDN        string `json:"user_dn,omitempty"`
	NomComplet    string `json:"nom_complet,omitempty"`
	Erreur        string `json:"erreur,omitempty"`
}

// ActionLogEntry est une entrée du journal d'actions (append-only).
// Les champs ou_source / ou_destination sont toujours présents dans le JSON
// (valeur vide = aucune OU concernée) pour rester acceptés par le
// dataclass Python, qui les déclare obligatoires.
type ActionLogEntry struct {
	Timestamp     string `json:"timestamp"`
	ActionType    string `json:"action_type"`
	Compte        string `json:"compte"`
	OUSource      string `json:"ou_source"`
	OUDestination string `json:"ou_destination"`
	Resultat      string `json:"resultat"`
	SessionID     string `json:"session_id"`
	Simulation    bool   `json:"simulation"`
	Detail        string `json:"detail"`
	Utilisateur   string `json:"utilisateur"`
	Domaine       string `json:"domaine"`
}

// Version de l'application (renseignée au build via -ldflags).
var Version = "dev"

// DataVersionCurrent est la version de schéma des données supportée.
// Doit correspondre à la dernière migration dans internal/backup/migration.go.
const DataVersionCurrent = 4
