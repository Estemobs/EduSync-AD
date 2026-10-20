// Package backup implémente la sauvegarde et la restauration d'EduSync AD.
//
// Constantes pour les sections de l'archive (partagées avec Python).
package backup

const (
	// Sections de l'archive
	SectionConfiguration = "configuration"
	SectionJournal       = "journal"
	SectionCoffre        = "coffre"
	SectionModeles       = "modeles"
	SectionCertificats   = "certificats"
	SectionRGPD          = "rgpd"
	SectionMeta          = "meta"
)

// Sections dans l'ordre d'affichage.
var Sections = []string{
	SectionConfiguration,
	SectionJournal,
	SectionCoffre,
	SectionModeles,
	SectionCertificats,
	SectionRGPD,
	SectionMeta,
}

// Fichiers de modèles (exclus de la section configuration).
var FichiersModeles = []string{"label_templates.json", "group_templates.json"}

var MiseEnGardeNonChiffre = "Archive non chiffrée : configuration, clés API et journal accessibles en clair — ne la copiez ni sur support partagé ni hors poste de confiance. La clé du coffre (secret.key) est volontairement exclue."
