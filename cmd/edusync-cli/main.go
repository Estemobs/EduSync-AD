// Commande edusync-cli — interface en ligne de commande d'EduSync-AD.
//
// Destinée aux scripts, à l'intégration continue et à l'IA : toutes les
// commandes parlent JSON, sortent un code de retour explicite et ne dépendent
// d'aucune interface graphique. Le cœur partagé est celui de l'application
// desktop (internal/…), conformément à ROADMAP_GO_MIGRATION.md §3.
//
// État : la connexion AD réelle (bind LDAP/GSSAPI) arrive avec le pool de
// connexions de la Phase 0 (jours 5-6) ; la commande « connect » valide
// dès à présent la résolution, la TCP et la poignée de main TLS/LDAPS.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/estemobs/EduSync-AD/internal/audit"
	"github.com/estemobs/EduSync-AD/internal/config"
)

// version est renseignée au build :
//
//	go build -ldflags "-X main.version=1.17.1" ./cmd/edusync-cli
var version = "dev"

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintf(os.Stderr, "erreur: %v\n", err)
		var exitCode *codeSortie
		if errors.As(err, &exitCode) {
			os.Exit(exitCode.code)
		}
		os.Exit(1)
	}
}

// codeSortie porte un code de retour d'erreur métier (2 = usage invalide).
type codeSortie struct {
	code int
	err  error
}

func (c *codeSortie) Error() string { return c.err.Error() }
func (c *codeSortie) Unwrap() error { return c.err }

func usage(sortie io.Writer) {
	fmt.Fprint(sortie, `EduSync AD — CLI

Utilisation :
  edusync-cli <commande> [options]

Commandes :
  version                 Affiche la version
  paths                   Affiche les chemins locaux (config, données, journal)
  config show             Affiche la configuration au format JSON
  audit list              Liste le journal d'actions
  users import-simulate   Simule l'import d'utilisateurs depuis un CSV (JSON)
  password simulate       Génère des mots de passe selon une politique (JSON)
  migration plan          Construit un plan de migration depuis un CSV (JSON)
  export fields           Liste les champs d'export disponibles (JSON)
  departs simulate        Simule un départ (sans écriture AD)
  departs pending list    Liste les suppressions planifiées
  departs pending purge   Purge les suppressions expirées
  profils simulate        Simule l'application d'un profil utilisateur (JSON)
  profils execute         Applique un profil utilisateur (JSON)
  logon-hours simulate    Simule les heures de connexion (JSON)
  logon-hours execute     Applique les heures de connexion (JSON)
  logon-scripts simulate  Simule l'application d'un script (JSON)
  logon-scripts execute   Applique un script (JSON)
  quotas simulate         Simule un quota de disque (JSON)
  quotas execute          Applique un quota de disque (JSON)
  homedirs simulate       Simule un dossier personnel (JSON)
  homedirs execute        Applique un dossier personnel (JSON)
  photos simulate         Simule l'application d'une photo (JSON)
  photos execute          Applique une photo (JSON)
  class-spaces plan             Simule un espace partagé de classe
  class-spaces creation-script  Génère le script PowerShell de création
  class-spaces sync-script      Génère le script PowerShell de synchro
  class-spaces notify           Enregistre un espace en attente (hook)
  class-spaces config           Affiche la configuration des espaces
  ad-cache stats           Affiche les statistiques du cache AD
  ad-cache ous             Liste les OUs du cache
  ad-cache groups          Liste les groupes du cache
  ad-cache users           Liste les utilisateurs d'une OU (cache)
  ad-cache search          Recherche des utilisateurs dans le cache
  ad-cache sync            Synchronise le cache (AD requis)
  advanced-io validate-filter  Valide un filtre LDAP
  advanced-io source       Construit/valide une source LDAP d'import
  advanced-io import       Importe depuis AD (AD requis)
  advanced-io export       Exporte en CSV/LDIF/vCard
  advanced-io merge-html   Publipostage HTML (un fichier par objet)
  advanced-io gep          Import GEP (mapping, parse, conversion)
  advanced-io config       Configure l'outil tiers GEP
  advanced-io run-tool     Lance l'outil tiers GEP
  rds config               Affiche/enregistre la configuration RDS
  rds collection-script    Script de collections de sessions RDS
  rds remoteapp-script     Script de publication des RemoteApp
  rds fslogix-script       Script de configuration FSLogix
  rds plans                Affiche les plans RDS enregistrés
  exchange config          Affiche/enregistre la configuration Exchange
  exchange plans           Construit les plans de boîtes depuis des comptes
  exchange onprem-script   Script Exchange sur site (Remote PowerShell)
  exchange online-script   Script Exchange Online (app-only)
  exchange policy-script   Script de politique d'adresses (on-prem)
  exchange report          Rapport CSV des adresses
  m365 config              Affiche/enregistre la configuration Microsoft 365
  m365 user-plans          Prépare les comptes cloud depuis des comptes AD
  m365 group-plans         Prépare les groupes Entra depuis des groupes AD
  m365 photo-mapping       Associe les photos d'identité aux comptes cloud
  m365 hybrid              Check-list d'identité hybride (PHS/ADFS/PTA)
  vault set                Enregistre un mot de passe positionné (coffre local)
  vault get                Réaffiche le mot de passe enregistré d'un compte
  vault list               Liste les comptes présents dans le coffre
  vault count              Nombre d'entrées du coffre
  vault clear              Purge le coffre (— force : --yes)
  multisite list           Liste les domaines/sites enregistrés
  multisite add            Ajoute un profil de domaine (chiffré)
  multisite remove         Supprime un profil de domaine
  multisite import-legacy  Importe la connexion mémorisée (M9)
  multisite ensure         Charge/migre les sites (jamais bloquant)
  rbac roles               Affiche les rôles et leurs permissions
  rbac list                Liste les délégués enregistrés
  rbac add                 Ajoute un délégué (rôle + portées OU/groupe)
  rbac remove              Supprime un délégué
  rbac policy              Résout la politique d'un opérateur
  label templates          Liste les modèles d'étiquettes
  label render             Génère une planche d'étiquettes PDF
  label trombinoscope      Génère un trombinoscope PDF (A4/A3)
  label send               Envoie une étiquette par mail (SMTP)
  group-templates list     Liste les modèles de groupes
  group-templates show     Affiche un modèle de groupe (JSON)
  group-templates validate Valide un ou tous les modèles (JSON)
  group-templates duplicate Duplique un modèle
  group-templates remove   Supprime un modèle
  group-templates export   Exporte un modèle (JSON ou XML)
  group-templates import   Importe un modèle (JSON ou XML)
  group-templates builtins Restaure/écrit les modèles intégrés
  group-templates plan     Calcule OU + groupes + artefacts (sans écriture AD)
  portal config            Affiche/enregistre les réglages du portail (M27)
  portal requests          Liste les demandes de création de compte
  portal status            État du portail (JSON)
  portal refuse            Refuse une demande de création de compte
  portal approve           Valide une demande (crée le compte dans l'AD)
  api config               Affiche/enregistre les réglages de l'API REST (M28)
  api keys list            Liste les clés d'API enregistrées
  api keys create          Crée une clé d'API (affichée une seule fois)
  api keys revoke          Révoque une clé d'API
  api keys remove          Supprime une clé d'API
  api keys permissions     Liste les permissions disponibles
  api routes               Liste les routes de l'API (JSON)
  api spec                 Écrit le document OpenAPI (api-spec.json)
  api webhooks list        Liste les webhooks enregistrés
  api webhooks add         Ajoute un webhook (secret chiffré)
  api webhooks remove      Supprime un webhook
  api webhooks deliveries  Liste le journal des livraisons
  api webhooks test        Envoie un événement de test à un webhook
  api webhooks events      Liste les événements disponibles
  api serve                Démarre le serveur de l'API (Ctrl-C pour arrêter)
  assistant anomalies      Détecte les anomalies de l'annuaire (M29, AD requis)
  assistant cleanup        Suggère le nettoyage de fin d'année (M29, AD requis)
  assistant smart-id       Génère un identifiant unique (M29)
  assistant security       Analyse de sécurité (recommandations + score, M29)
  assistant journal        Analyse le journal d'actions (M29)
  compliance mfa           Vérifie la double authentification admin (M30)
  compliance secret        Génère un secret TOTP et des codes de secours (M30)
  compliance session       Contrôle la politique de session unique (M30)
  compliance pso           Analyse les politiques de mot de passe (M30)
  compliance sign          Signe un export JSON (M30)
  compliance verify        Vérifie la signature d'un export (M30)
  compliance rgpd          Génère le rapport RGPD (M30)
  connect                 Teste l'accessibilité d'un contrôleur (TCP + TLS)
  migrate from-python       Migre config/coffre/journal Python → Go (--force, --dry-run, --json)

Exemples :
  edusync-cli paths
  edusync-cli config show
  edusync-cli audit list --limit 20 --json
  edusync-cli connect --server dc1.edusync.local --port 636
  edusync-cli connect --server dc1.local --ca /etc/ssl/adcs-root.pem

Toutes les commandes --json produisent une sortie stable pour scripts/IA.
`)
}

func run(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		usage(stdout)
		return &codeSortie{code: 2, err: errors.New("commande manquante")}
	}

	switch args[0] {
	case "help", "-h", "--help":
		usage(stdout)
		return nil
	case "version":
		fmt.Fprintf(stdout, "edusync-cli %s\n", version)
		return nil
	case "paths":
		return cmdPaths(args[1:], stdout, stderr)
	case "config":
		return cmdConfig(args[1:], stdout, stderr)
	case "audit":
		return cmdAudit(args[1:], stdout, stderr)
	case "users":
		return cmdUsers(args[1:], stdout, stderr)
	case "password":
		return cmdPassword(args[1:], stdout, stderr)
	case "migration":
		return cmdMigration(args[1:], stdout, stderr)
	case "export":
		return cmdExport(args[1:], stdout, stderr)
	case "departs":
		return cmdDepart(args[1:], stdout, stderr)
	case "profils":
		return cmdProfils(args[1:], stdout, stderr)
	case "logon-hours":
		return cmdLogonHours(args[1:], stdout, stderr)
	case "logon-scripts":
		return cmdLogonScripts(args[1:], stdout, stderr)
	case "quotas":
		return cmdQuotas(args[1:], stdout, stderr)
	case "homedirs":
		return cmdHomeDirs(args[1:], stdout, stderr)
	case "photos":
		return cmdPhotos(args[1:], stdout, stderr)
	case "class-spaces":
		return cmdClassSpaces(args[1:], stdout, stderr)
	case "ad-cache":
		return cmdADCache(args[1:], stdout, stderr)
	case "advanced-io":
		return cmdAdvancedIO(args[1:], stdout, stderr)
	case "rds":
		return cmdRDS(args[1:], stdout, stderr)
	case "exchange":
		return cmdExchange(args[1:], stdout, stderr)
	case "m365":
		return cmdM365(args[1:], stdout, stderr)
	case "vault":
		return cmdVault(args[1:], stdout, stderr)
	case "multisite":
		return cmdMultisite(args[1:], stdout, stderr)
	case "rbac":
		return cmdRBAC(args[1:], stdout, stderr)
	case "label":
		return cmdLabel(args[1:], stdout, stderr)
	case "group-templates":
		return cmdGroupTemplates(args[1:], stdout, stderr)
	case "portal":
		return cmdPortal(args[1:], stdout, stderr)
	case "api":
		return cmdAPI(args[1:], stdout, stderr)
	case "assistant":
		return cmdAssistant(args[1:], stdout, stderr)
	case "compliance":
		return cmdCompliance(args[1:], stdout, stderr)
	case "connect":
		return cmdConnect(args[1:], stdout, stderr)
	case "migrate":
		return cmdMigrate(args[1:], stdout, stderr)
	default:
		return &codeSortie{code: 2, err: fmt.Errorf("commande inconnue %q (voir « edusync-cli help »)", args[0])}
	}
}

// cmdPaths expose les emplacements utilisés par l'application — indispensable
// pour sauvegarder, migrer ou diagnostiquer un poste.
func cmdPaths(args []string, stdout, _ io.Writer) error {
	fs := flag.NewFlagSet("paths", flag.ContinueOnError)
	jsonOut := fs.Bool("json", false, "sortie JSON")
	if err := fs.Parse(args); err != nil {
		return &codeSortie{code: 2, err: err}
	}

	cheminConfig, err := config.DefaultPath()
	if err != nil {
		return err
	}
	cheminDonnees, err := config.DataDir()
	if err != nil {
		return err
	}
	cheminCache, err := config.CacheDir()
	if err != nil {
		return err
	}
	journal, err := audit.DefaultPath()
	if err != nil {
		return err
	}

	valeurs := struct {
		Configuration string `json:"configuration"`
		Donnees       string `json:"donnees"`
		Cache         string `json:"cache"`
		Journal       string `json:"journal"`
		Version       string `json:"version"`
	}{
		Configuration: cheminConfig,
		Donnees:       cheminDonnees,
		Cache:         cheminCache,
		Journal:       journal,
		Version:       version,
	}

	if *jsonOut {
		return ecrireJSON(stdout, valeurs)
	}
	fmt.Fprintf(stdout, "configuration : %s\n", valeurs.Configuration)
	fmt.Fprintf(stdout, "données       : %s\n", valeurs.Donnees)
	fmt.Fprintf(stdout, "cache         : %s\n", valeurs.Cache)
	fmt.Fprintf(stdout, "journal       : %s\n", valeurs.Journal)
	return nil
}

// cmdConfig affiche la configuration effective.
func cmdConfig(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		return &codeSortie{code: 2, err: errors.New("sous-commande manquante : « show »")}
	}
	if args[0] != "show" {
		return &codeSortie{code: 2, err: fmt.Errorf("sous-commande inconnue %q", args[0])}
	}

	fs := flag.NewFlagSet("config show", flag.ContinueOnError)
	chemin := fs.String("f", "", "chemin d'un config.json alternatif")
	if err := fs.Parse(args[1:]); err != nil {
		return &codeSortie{code: 2, err: err}
	}

	cfg, err := config.Load(*chemin)
	if err != nil {
		fmt.Fprintf(stderr, "avertissement: %v (valeurs par défaut utilisées)\n", err)
	}
	return ecrireJSON(stdout, cfg)
}

// cmdAudit liste le journal d'actions avec filtres (mêmes critères que l'UI).
func cmdAudit(args []string, stdout, _ io.Writer) error {
	if len(args) == 0 {
		return &codeSortie{code: 2, err: errors.New("sous-commande manquante : « list »")}
	}
	if args[0] != "list" {
		return &codeSortie{code: 2, err: fmt.Errorf("sous-commande inconnue %q", args[0])}
	}

	fs := flag.NewFlagSet("audit list", flag.ContinueOnError)
	limite := fs.Int("limit", 50, "nombre maximum d'entrées")
	typeAction := fs.String("type", "", "filtre par type d'action")
	resultat := fs.String("resultat", "", "filtre par résultat")
	domaine := fs.String("domaine", "", "filtre par domaine")
	operateur := fs.String("operateur", "", "filtre par opérateur")
	recherche := fs.String("search", "", "recherche dans le compte ou le détail")
	jsonOut := fs.Bool("json", false, "sortie JSON")
	chemin := fs.String("f", "", "chemin d'un journal.db alternatif")
	if err := fs.Parse(args[1:]); err != nil {
		return &codeSortie{code: 2, err: err}
	}

	l, err := audit.Open(*chemin)
	if err != nil {
		return err
	}
	defer l.Close() //nolint:errcheck

	entries, err := l.List(audit.Filter{
		Limit:      *limite,
		ActionType: *typeAction,
		Resultat:   *resultat,
		Domaine:    *domaine,
		Operateur:  *operateur,
		Recherche:  *recherche,
	})
	if err != nil {
		return err
	}

	if *jsonOut {
		if entries == nil {
			entries = []audit.Entry{}
		}
		return ecrireJSON(stdout, entries)
	}
	if len(entries) == 0 {
		fmt.Fprintln(stdout, "(journal vide)")
		return nil
	}
	for _, e := range entries {
		fmt.Fprintf(stdout, "%s  %-18s %-20s %-8s %-15s %s\n",
			e.Timestamp, e.ActionType, e.Compte, e.Resultat, e.Utilisateur, e.Detail)
	}
	return nil
}

// ecrireJSON sérialise avec une indentation stable (compatibilité outils).
func ecrireJSON(w io.Writer, valeur any) error {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(valeur); err != nil {
		return fmt.Errorf("sérialisation JSON: %w", err)
	}
	return nil
}
