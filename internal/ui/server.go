// Package ui sert l'interface graphique d'EduSync-AD : pages HTML rendues
// côté serveur (html/template) + HTMX pour les mises à jour partielles.
//
// Principe : le serveur fait le travail, le navigateur ne contient que le
// strict minimum (thème, langue). Aucun framework JavaScript, aucune API
// REST parallèle à maintenir.
//
// Toutes les routes sont accessibles en navigation normale (formulaire
// classique) : l'application reste utilisable sans JavaScript, ce qui
// facilite les tests automatisés (Go) et les tests pilotés par l'IA.
package ui

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html"
	"html/template"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/estemobs/EduSync-AD/internal/ad"
	"github.com/estemobs/EduSync-AD/internal/api"
	"github.com/estemobs/EduSync-AD/internal/audit"
	"github.com/estemobs/EduSync-AD/internal/classspaces"
	"github.com/estemobs/EduSync-AD/internal/compliance"
	"github.com/estemobs/EduSync-AD/internal/config"
	"github.com/estemobs/EduSync-AD/internal/exchange"
	"github.com/estemobs/EduSync-AD/internal/graph"
	"github.com/estemobs/EduSync-AD/internal/grouptemplates"
	"github.com/estemobs/EduSync-AD/internal/label"
	"github.com/estemobs/EduSync-AD/internal/m365"
	"github.com/estemobs/EduSync-AD/internal/mailer"
	"github.com/estemobs/EduSync-AD/internal/models"
	"github.com/estemobs/EduSync-AD/internal/multisite"
	"github.com/estemobs/EduSync-AD/internal/portal"
	"github.com/estemobs/EduSync-AD/internal/progress"
	"github.com/estemobs/EduSync-AD/internal/rbac"
	"github.com/estemobs/EduSync-AD/internal/rds"
	"github.com/estemobs/EduSync-AD/internal/updater"
	"github.com/estemobs/EduSync-AD/internal/vault"
	"github.com/estemobs/EduSync-AD/web"
)

const (
	// cookieSession transporte l'identifiant de session (HttpOnly).
	cookieSession = "edusync_sid"
	// cookieLangue / cookieTheme gardent les préférences d'affichage
	// même hors session (page de connexion).
	cookieLangue = "edusync_langue"
	cookieTheme  = "edusync_theme"

	// dureeSession est la durée de validité d'une session administrateur.
	dureeSession = 12 * time.Hour
)

// Options permet d'injecter des chemins alternatifs (tests) et la version.
type Options struct {
	// ConfigPath vide = chemin par défaut (~/.config/EduSyncAD/config.json).
	ConfigPath string
	// AuditPath vide = chemin par défaut (~/.local/share/EduSyncAD/journal.db).
	AuditPath string
	// Version est affichée dans l'en-tête et le tableau de bord.
	Version string
	// UpdaterAPIURL est l'URL de l'API GitHub releases (vide = défaut GitHub).
	UpdaterAPIURL string
}

// noeudArbre enveloppe un nœud de l'arbre AD avec la langue de la page. Les
// gabarits imbriqués ({{template}}) repartent avec un contexte propre ($ est
// rebindé sur l'argument du gabarit appelé) : la langue doit donc voyager
// avec le nœud pour que {{tr}} fonctionne dans explorer_tree_node.
// Les champs de ExplorerNode sont promus : la syntaxe du gabarit reste
// identique ({{.DN}}, {{.Children}}…).
type noeudArbre struct {
	*ad.ExplorerNode
	Langue string
}

// noeudArbreConstruct fabrique l'enveloppe pour le gabarit explorer_tree_node.
func noeudArbreConstruct(langue string, n *ad.ExplorerNode) noeudArbre {
	return noeudArbre{ExplorerNode: n, Langue: langue}
}

// PageData est le modèle transmis aux templates.
type PageData struct {
	Titre        string
	Langue       string
	Theme        string
	Version      string
	Connecte     bool
	LDAPFallback bool // true si repli LDAP 389 effectué

	// Session courante
	Utilisateur string
	Domaine     string
	Controleur  string

	// Erreurs de formulaire (affichées sans rechargement via HTMX)
	Erreurs []string

	// Tableau de bord
	CheminConfig  string
	CheminDonnees string
	NbActions     int
	Actions       []audit.Entry
	StatutAD      string

	// Arrivées — simulation
	Simulation *SimulationResult
	ForcerMdp  string
	SkipAd     string

	// Arrivées — exécution
	Resultat *ExecuteResult

	// Password — simulation
	SimulationPwd *PasswordSimResult
	ForceChange   bool

	// Password — exécution
	ResultatPwd *PasswordExecResult

	// Migration — plan
	PlanMigration *MigrationPlanResult

	// Migration — simulation
	SimulationMigration *MigrationSimResult

	// Migration — exécution
	MigrationExecute *MigrationExecResult

	// Explorer
	SearchResult  *ad.SearchResult
	ExplorerTree  *ad.ExplorerNode
	ExplorerNodes []*ad.ExplorerNode
	ExplorerUser  *ad.User
	ExplorerGroup *ad.Group
	ExplorerOU    *ad.OU

	// Export (M6)
	ExportOU  string   // OU pré-remplie du formulaire
	ExportOUs []*ad.OU // OU de premier niveau pour le datalist

	// Progression (A12)
	Operations []*progress.Operation // opérations de masse en cours

	// Espaces de classe (M18)
	ConfigClassSpaces classspaces.Config

	// Cache AD (T2)
	StatsCache         ad.CacheStats
	CheminCacheAD      string
	CacheADPerime      bool
	ResultatCacheOUs   []ad.CacheOU
	ResultatCacheUsers []ad.CacheUser

	// Import/Export avancés (M22)
	ConfigAdvIO ad.ConfigAdvIO

	// RDS / Bureau à distance (M21)
	ConfigRDS rds.Config

	// Microsoft Exchange (M20)
	ConfigExchange exchange.Config

	// Microsoft 365 / Entra ID (M19)
	ConfigM365 ConfigM365UI

	// Coffre-fort local des mots de passe
	NombreCoffre int
	CoffreSAMs   []string

	// Multisite / multi-domaine (M25)
	Sites           []multisite.ProfilDomaine
	DomainesJournal []string

	// RBAC — délégation d'administration (M26)
	Grants    []rbac.Grant
	RolesRBAC []RoleRBACUI

	// Étiquettes avancées (M24)
	ModelesLabel []label.ModeleLabel
	MailLabel    mailer.MailConfig

	// Modèles de groupes (M23)
	ModelesGT []grouptemplates.ModeleGroupe

	// Portail auto-service (M27)
	PortailConfig       portal.Config
	PortailDemandes     []portal.Demande
	PortailEnEcoute     bool
	PortailURL          string
	PortailCheminConfig string
	PortailCheminBase   string

	// API REST et webhooks (M28)
	APIConfig           api.Config
	APICles             []CleAPIVue
	APIWebhooks         []WebhookVue
	APILivraisons       []LivraisonVue
	APINombreLivraisons int
	APIEnEcoute         bool
	APIURL              string
	APICheminConfig     string
	APICheminCles       string
	APICheminWebhooks   string
	APICheminLivraisons string
	APIPermissions      []api.PermissionLookup
	APIRoutes           []api.Route
	APIEvenements       []string

	// Assistant IA (M29)
	AssistantFormats       []string
	AssistantADDisponible  bool
	AssistantAvertissement string

	// Conformité & sécurité (M30)
	ComplianceConfig       compliance.Config
	ComplianceCheminConfig string
}

// importRow représente une ligne du CSV d'import (niveau paquet pour réutilisation).
type importRow struct {
	Prenom, Nom, OU, Classe, Email string
}

// SimulationResult contient le résultat complet de la simulation
type SimulationResult struct {
	Lignes  []SimulatedUserLine
	Valides int
	Erreurs int
	Total   int
}

// SimulatedUserLine représente une ligne de simulation pour le template
type SimulatedUserLine struct {
	Prenom        string
	Nom           string
	Identifiant   string
	Mail          string
	MotDePasse    string
	OUCible       string
	Groupe        string
	DoublonResolu bool
	DoublonAD     bool
	Erreur        string
}

// ExecuteResult contient le résultat de l'exécution
type ExecuteResult struct {
	Crees  int
	Echecs int
	Lignes []MigrationExecLine
}

// ExecuteLine représente une ligne du résultat d'exécution
type ExecuteLine struct {
	Prenom, Nom, Identifiant string
	NouveauMDP               string
	Statut                   string // "succes" ou "echec"
	Detail                   string
}

// PasswordSimLine représente une ligne de simulation MDP
type PasswordSimLine struct {
	Prenom      string
	Nom         string
	Identifiant string
	Mail        string
	NouveauMDP  string
	OUCible     string
	Erreur      string
}

// PasswordSimResult contient le résultat de la simulation MDP
type PasswordSimResult struct {
	Lignes  []PasswordSimLine
	Valides int
	Erreurs int
	Total   int
}

// PasswordExecResult contient le résultat de l'exécution MDP
type PasswordExecResult struct {
	Crees  int
	Echecs int
	Lignes []MigrationExecLine
}

// MigrationPlanLine représente une ligne du plan de migration
type MigrationPlanLine struct {
	Identifiant string
	NomComplet  string
	OUSource    string
	OUCible     string
	Action      string
	Erreur      string
}

// MigrationPlanResult contient le plan de migration
type MigrationPlanResult struct {
	Lignes   []MigrationPlanLine
	Total    int
	WillMove int
	WillSkip int
}

// MigrationSimLine représente une ligne de simulation migration
type MigrationSimLine struct {
	Identifiant string
	NomComplet  string
	OUSource    string
	OUCible     string
	Action      string
	Erreur      string
}

// MigrationSimResult contient le résultat de la simulation migration
type MigrationSimResult struct {
	Lignes  []MigrationSimLine
	Total   int
	Valides int
	Erreurs int
}

// MigrationExecResult contient le résultat d'exécution migration

// MigrationExecLine représente une ligne de résultat exécution migration
type MigrationExecLine struct {
	NouveauMDP  string
	Prenom      string
	Nom         string
	Identifiant string
	NomComplet  string
	OUSource    string
	OUCible     string
	Statut      string
	Detail      string
}

// MigrationExecResult contient le résultat d'exécution migration
type MigrationExecResult struct {
	Crees  int
	Echecs int
	Lignes []MigrationExecLine
}

type session struct {
	Utilisateur               string
	Domaine                   string
	Controleur                string
	MotDePasse                string // en mémoire seulement, jamais persisté
	LDAPFallback              bool   // true si repli LDAP 389 effectué
	DerniereSimulation        []models.GeneratedUser
	DerniereSimPassword       []ad.PasswordResetResult
	DerniereSimPasswordParams ad.PasswordResetParams
	DernierPlanMigration      *ad.MigrationPlan
	CreeLe                    time.Time
}

// Server implémente http.Handler.
type Server struct {
	pages   map[string]*template.Template
	racines map[string]string // fichier → nom du premier {{define}} du fichier
	mux     *http.ServeMux
	journal *audit.Log
	opts    Options

	mu       sync.Mutex
	sessions map[string]*session
	adPools  map[string]*ad.Pool // clé = sessionID
	progress *progress.Tracker   // suivi des opérations de masse

	// Serveur HTTP du portail auto-service (M27), partagé par l'application.
	portailMu sync.Mutex
	portail   *portailEnCours

	// Serveur de l'API REST et dispatcher des webhooks (M28).
	apiMu sync.Mutex
	api   *apiEnCours
}

// New prépare le serveur d'interface (templates, assets, journal).
func New(opts Options) (*Server, error) {
	journal, err := audit.Open(opts.AuditPath)
	if err != nil {
		return nil, fmt.Errorf("ui: ouverture du journal: %w", err)
	}

	base, err := template.New("layout.html").
		Funcs(template.FuncMap{"tr": Tr, "horodatage": horodatage, "noeud": noeudArbreConstruct}).
		ParseFS(web.FS, "templates/layout.html")
	if err != nil {
		_ = journal.Close()
		return nil, fmt.Errorf("ui: lecture du gabarit layout: %w", err)
	}

	// Découverte de tous les gabarits : les fichiers définissant « content »
	// sont des pages complètes (rendues dans le layout), les autres sont des
	// fragments partagés (explorer_tree, explorer_results…) référencés par
	// les pages via {{template}} et rendus seuls dans les cibles HTMX.
	chemins, err := fs.Glob(web.FS, "templates/*.html")
	if err != nil {
		_ = journal.Close()
		return nil, fmt.Errorf("ui: énumération des gabarits: %w", err)
	}
	var fichiersPages, fichiersFragments []string
	racines := make(map[string]string, len(chemins))
	for _, chemin := range chemins {
		nom := filepath.Base(chemin)
		if nom == "layout.html" {
			continue
		}
		brut, err := fs.ReadFile(web.FS, chemin)
		if err != nil {
			_ = journal.Close()
			return nil, fmt.Errorf("ui: lecture de %s: %w", nom, err)
		}
		racines[nom] = premierDefine(brut)
		if bytes.Contains(brut, []byte(`{{define "content"}}`)) {
			fichiersPages = append(fichiersPages, chemin)
		} else {
			fichiersFragments = append(fichiersFragments, chemin)
		}
	}
	if len(fichiersPages) == 0 {
		_ = journal.Close()
		return nil, fmt.Errorf("ui: aucun gabarit de page trouvé")
	}

	// Chaque jeu = layout + fragments partagés + le fichier courant. Les
	// fragments ne définissent jamais « content » : aucune collision possible
	// avec la page du jeu.
	pages := make(map[string]*template.Template, len(fichiersPages)+len(fichiersFragments))
	jeu := func(nom, fichier string) error {
		page, err := base.Clone()
		if err != nil {
			return fmt.Errorf("duplication du gabarit: %w", err)
		}
		if len(fichiersFragments) > 0 {
			if _, err := page.ParseFS(web.FS, fichiersFragments...); err != nil {
				return fmt.Errorf("lecture des fragments pour %s: %w", nom, err)
			}
		}
		if fichier != "" {
			if _, err := page.ParseFS(web.FS, fichier); err != nil {
				return fmt.Errorf("lecture du gabarit %s: %w", nom, err)
			}
		}
		pages[nom] = page
		return nil
	}
	// …puis un jeu par page (contenant déjà les fragments, + sa page).
	for _, chemin := range fichiersPages {
		nom := filepath.Base(chemin)
		if err := jeu(nom, chemin); err != nil {
			_ = journal.Close()
			return nil, fmt.Errorf("ui: %w", err)
		}
	}
	for _, chemin := range fichiersFragments {
		nom := filepath.Base(chemin)
		jeuFrag, err := base.Clone()
		if err != nil {
			_ = journal.Close()
			return nil, fmt.Errorf("ui: duplication du gabarit: %w", err)
		}
		if _, err := jeuFrag.ParseFS(web.FS, fichiersFragments...); err != nil {
			_ = journal.Close()
			return nil, fmt.Errorf("ui: lecture des fragments pour %s: %w", nom, err)
		}
		pages[nom] = jeuFrag
	}

	s := &Server{
		pages:    pages,
		racines:  racines,
		journal:  journal,
		opts:     opts,
		sessions: map[string]*session{},
		adPools:  map[string]*ad.Pool{},
		progress: progress.NewTracker(),
	}
	s.mux = s.routes()
	return s, nil
}

// Close libère la base du journal.
func (s *Server) Close() error {
	s.fermerPortail()
	s.fermerAPI()
	if s.journal == nil {
		return nil
	}
	return s.journal.Close()
}

// routes déclare les points d'entrée. Chaque route existe en GET et/ou POST
// pour rester utilisable avec des formulaires HTML purs.
func (s *Server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", s.accueil)
	mux.HandleFunc("GET /login", s.pageLogin)
	mux.HandleFunc("POST /login", s.connexion)
	mux.HandleFunc("GET /app", s.tableauBord)
	mux.HandleFunc("POST /logout", s.deconnexion)
	mux.HandleFunc("POST /theme", s.changerTheme)
	mux.HandleFunc("POST /langue", s.changerLangue)

	// AD Modules (protégés par session)
	mux.HandleFunc("GET /app/users", s.requireAuth(s.pageUsers))
	mux.HandleFunc("POST /app/users/import", s.requireAuth(s.usersImport))
	mux.HandleFunc("POST /app/users/simulate", s.requireAuth(s.usersSimulate))
	mux.HandleFunc("POST /app/users/execute", s.requireAuth(s.usersExecute))

	mux.HandleFunc("GET /app/password", s.requireAuth(s.pagePassword))
	mux.HandleFunc("POST /app/password/simulate", s.requireAuth(s.passwordSimulate))
	mux.HandleFunc("POST /app/password/execute", s.requireAuth(s.passwordExecute))

	mux.HandleFunc("GET /app/migration", s.requireAuth(s.pageMigration))
	mux.HandleFunc("POST /app/migration/plan", s.requireAuth(s.migrationPlan))
	mux.HandleFunc("POST /app/migration/simulate", s.requireAuth(s.migrationSimulate))
	mux.HandleFunc("POST /app/migration/execute", s.requireAuth(s.migrationExecute))

	// Départs (M3)
	mux.HandleFunc("GET /app/departs", s.requireAuth(s.pageDeparts))
	mux.HandleFunc("POST /app/departs/simulate", s.requireAuth(s.departsSimulate))
	mux.HandleFunc("POST /app/departs/execute", s.requireAuth(s.departsExecute))

	// Paramètres globaux (M8)
	mux.HandleFunc("GET /app/parametres", s.requireAuth(s.pageParametres))
	mux.HandleFunc("POST /app/parametres/comptes", s.requireAuth(s.parametresComptes))
	mux.HandleFunc("POST /app/parametres/mots_de_passe", s.requireAuth(s.parametresMotsDePasse))
	mux.HandleFunc("POST /app/parametres/apparence", s.requireAuth(s.parametresApparence))

	mux.HandleFunc("GET /app/explorer", s.requireAuth(s.pageExplorer))
	mux.HandleFunc("POST /app/explorer/search", s.requireAuth(s.explorerSearch))
	mux.HandleFunc("POST /app/explorer/tree", s.requireAuth(s.explorerTree))

	mux.HandleFunc("GET /app/export", s.requireAuth(s.pageExport))
	mux.HandleFunc("POST /app/export/csv", s.requireAuth(s.exportCSV))
	mux.HandleFunc("POST /app/export/pdf", s.requireAuth(s.exportPDF))
	mux.HandleFunc("POST /app/export/trombinoscope", s.requireAuth(s.exportTrombinoscope))

	// Audit / Journal
	mux.HandleFunc("GET /app/audit", s.requireAuth(s.pageAudit))
	mux.HandleFunc("POST /app/audit/filter", s.requireAuth(s.auditFilter))

	// Mise à jour intégrée (M10)
	mux.HandleFunc("GET /app/updater/check", s.requireAuth(s.pageUpdaterCheck))
	mux.HandleFunc("POST /app/updater/install", s.requireAuth(s.pageUpdaterInstall))

	// Progression / annulation des opérations de masse (A12)
	mux.HandleFunc("GET /app/progress", s.requireAuthSession(s.pageProgress))
	mux.HandleFunc("GET /app/progress/list", s.requireAuthSession(s.progressList))
	mux.HandleFunc("POST /app/progress/cancel", s.requireAuthSession(s.progressCancel))

	// Profils utilisateurs (M13)
	mux.HandleFunc("GET /app/profils", s.requireAuthSession(s.pageProfils))
	mux.HandleFunc("POST /app/profils/simulate", s.requireAuthSession(s.profilsSimulate))
	mux.HandleFunc("POST /app/profils/execute", s.requireAuthSession(s.profilsExecute))

	// Heures de connexion (M16)
	mux.HandleFunc("GET /app/logon-hours", s.requireAuthSession(s.pageLogonHours))
	mux.HandleFunc("POST /app/logon-hours/simulate", s.requireAuthSession(s.logonHoursSimulate))
	mux.HandleFunc("POST /app/logon-hours/execute", s.requireAuthSession(s.logonHoursExecute))

	// Scripts logon/logoff (M15)
	mux.HandleFunc("GET /app/logon-scripts", s.requireAuthSession(s.pageLogonScripts))
	mux.HandleFunc("POST /app/logon-scripts/simulate", s.requireAuthSession(s.logonScriptsSimulate))
	mux.HandleFunc("POST /app/logon-scripts/execute", s.requireAuthSession(s.logonScriptsExecute))

	// Quotas de disque (M14)
	mux.HandleFunc("GET /app/quotas", s.requireAuthSession(s.pageQuotas))
	mux.HandleFunc("POST /app/quotas/simulate", s.requireAuthSession(s.quotasSimulate))
	mux.HandleFunc("POST /app/quotas/execute", s.requireAuthSession(s.quotasExecute))

	// Dossiers personnels (M17)
	mux.HandleFunc("GET /app/homedirs", s.requireAuthSession(s.pageHomeDirs))
	mux.HandleFunc("POST /app/homedirs/simulate", s.requireAuthSession(s.homeDirsSimulate))
	mux.HandleFunc("POST /app/homedirs/execute", s.requireAuthSession(s.homeDirsExecute))

	// Photos d'identite (M12)
	mux.HandleFunc("GET /app/photos", s.requireAuthSession(s.pagePhotos))
	mux.HandleFunc("POST /app/photos/simulate", s.requireAuthSession(s.photosSimulate))
	mux.HandleFunc("POST /app/photos/execute", s.requireAuthSession(s.photosExecute))

	// Espaces partages par classe (M18)
	mux.HandleFunc("GET /app/class-spaces", s.requireAuthSession(s.pageClassSpaces))
	mux.HandleFunc("POST /app/class-spaces/config", s.requireAuthSession(s.classSpacesConfig))
	mux.HandleFunc("POST /app/class-spaces/plan", s.requireAuthSession(s.classSpacesPlan))
	mux.HandleFunc("POST /app/class-spaces/creation-script", s.requireAuthSession(s.classSpacesCreationScript))
	mux.HandleFunc("POST /app/class-spaces/sync-script", s.requireAuthSession(s.classSpacesSyncScript))
	mux.HandleFunc("POST /app/class-spaces/mark-synced", s.requireAuthSession(s.classSpacesMarkSynced))

	// Cache AD SQLite (T2)
	mux.HandleFunc("GET /app/ad-cache", s.requireAuthSession(s.pageADCache))
	mux.HandleFunc("POST /app/ad-cache/sync", s.requireAuthSession(s.adCacheSync))
	mux.HandleFunc("POST /app/ad-cache/search", s.requireAuthSession(s.adCacheSearch))
	mux.HandleFunc("POST /app/ad-cache/ous", s.requireAuthSession(s.adCacheOUs))

	// Import/Export avancés (M22)
	mux.HandleFunc("GET /app/advanced-io", s.requireAuthSession(s.pageAdvancedIO))
	mux.HandleFunc("POST /app/advanced-io/validate-filter", s.requireAuthSession(s.advioValidateFilter))
	mux.HandleFunc("POST /app/advanced-io/source", s.requireAuthSession(s.advioSource))
	mux.HandleFunc("POST /app/advanced-io/import", s.requireAuthSession(s.advioImport))
	mux.HandleFunc("POST /app/advanced-io/gep-mapping", s.requireAuthSession(s.advioGEPMapping))
	mux.HandleFunc("POST /app/advanced-io/config", s.requireAuthSession(s.advioConfig))

	// RDS / Bureau à distance (M21)
	mux.HandleFunc("GET /app/rds", s.requireAuthSession(s.pageRDS))
	mux.HandleFunc("POST /app/rds/config", s.requireAuthSession(s.rdsConfigForm))
	mux.HandleFunc("POST /app/rds/collection-script", s.requireAuthSession(s.rdsCollectionScript))
	mux.HandleFunc("POST /app/rds/remoteapp-script", s.requireAuthSession(s.rdsRemoteAppScript))
	mux.HandleFunc("POST /app/rds/fslogix-script", s.requireAuthSession(s.rdsFSLogixScript))

	// Microsoft Exchange (M20)
	mux.HandleFunc("GET /app/exchange", s.requireAuthSession(s.pageExchange))
	mux.HandleFunc("POST /app/exchange/config", s.requireAuthSession(s.exchangeConfigForm))
	mux.HandleFunc("POST /app/exchange/plans", s.requireAuthSession(s.exchangePlans))
	mux.HandleFunc("POST /app/exchange/onprem-script", s.requireAuthSession(s.exchangeOnpremScript))
	mux.HandleFunc("POST /app/exchange/online-script", s.requireAuthSession(s.exchangeOnlineScript))
	mux.HandleFunc("POST /app/exchange/policy-script", s.requireAuthSession(s.exchangePolicyScript))

	// Microsoft 365 / Entra ID (M19)
	mux.HandleFunc("GET /app/m365", s.requireAuthSession(s.pageM365))
	mux.HandleFunc("POST /app/m365/config", s.requireAuthSession(s.m365ConfigForm))
	mux.HandleFunc("POST /app/m365/user-plans", s.requireAuthSession(s.m365UserPlans))
	mux.HandleFunc("POST /app/m365/group-plans", s.requireAuthSession(s.m365GroupPlans))

	// Coffre-fort local des mots de passe
	mux.HandleFunc("GET /app/vault", s.requireAuthSession(s.pageVault))
	mux.HandleFunc("POST /app/vault/rechercher", s.requireAuthSession(s.vaultRechercher))
	mux.HandleFunc("POST /app/vault/supprimer", s.requireAuthSession(s.vaultSupprimer))
	mux.HandleFunc("POST /app/vault/purger", s.requireAuthSession(s.vaultPurger))

	// Multisite / multi-domaine (M25)
	mux.HandleFunc("GET /app/multisite", s.requireAuthSession(s.pageMultisite))
	mux.HandleFunc("POST /app/multisite/ajouter", s.requireAuthSession(s.multisiteAjouter))
	mux.HandleFunc("POST /app/multisite/supprimer", s.requireAuthSession(s.multisiteSupprimer))

	// RBAC — délégation d'administration (M26)
	mux.HandleFunc("GET /app/rbac", s.requireAuthSession(s.pageRBAC))
	mux.HandleFunc("POST /app/rbac/ajouter", s.requireAuthSession(s.rbacAjouter))
	mux.HandleFunc("POST /app/rbac/supprimer", s.requireAuthSession(s.rbacSupprimer))

	// Étiquettes avancées (M24)
	mux.HandleFunc("GET /app/label", s.requireAuthSession(s.pageLabel))
	mux.HandleFunc("POST /app/label/apercu", s.requireAuthSession(s.labelApercu))
	mux.HandleFunc("POST /app/label/trombinoscope", s.requireAuthSession(s.labelTrombinoscope))
	mux.HandleFunc("POST /app/label/mail", s.requireAuthSession(s.labelMail))
	mux.HandleFunc("POST /app/label/supprimer", s.requireAuthSession(s.labelSupprimer))

	// Modèles de groupes (M23)
	mux.HandleFunc("GET /app/group-templates", s.requireAuthSession(s.pageGroupTemplates))
	mux.HandleFunc("POST /app/group-templates/apercu", s.requireAuthSession(s.groupTemplatesApercu))
	mux.HandleFunc("POST /app/group-templates/instancier", s.requireAuthSession(s.groupTemplatesInstancier))
	mux.HandleFunc("POST /app/group-templates/dupliquer", s.requireAuthSession(s.groupTemplatesDupliquer))
	mux.HandleFunc("POST /app/group-templates/supprimer", s.requireAuthSession(s.groupTemplatesSupprimer))
	mux.HandleFunc("GET /app/group-templates/export", s.requireAuthSession(s.groupTemplatesExporter))

	// Portail auto-service (M27)
	mux.HandleFunc("GET /app/portal", s.requireAuthSession(s.pagePortail))
	mux.HandleFunc("POST /app/portal/config", s.requireAuthSession(s.portailConfigForm))
	mux.HandleFunc("POST /app/portal/demarrer", s.requireAuthSession(s.portailDemarrer))
	mux.HandleFunc("POST /app/portal/arreter", s.requireAuthSession(s.portailArreter))
	mux.HandleFunc("POST /app/portal/approuver", s.requireAuthSession(s.portailApprouver))
	mux.HandleFunc("POST /app/portal/refuser", s.requireAuthSession(s.portailRefuser))
	mux.HandleFunc("GET /app/portal/etat", s.requireAuthSession(s.portailEtat))

	mux.HandleFunc("GET /app/api", s.requireAuthSession(s.pageAPI))
	mux.HandleFunc("POST /app/api/config", s.requireAuthSession(s.apiConfigForm))
	mux.HandleFunc("POST /app/api/demarrer", s.requireAuthSession(s.apiDemarrer))
	mux.HandleFunc("POST /app/api/arreter", s.requireAuthSession(s.apiArreter))
	mux.HandleFunc("GET /app/api/etat", s.requireAuthSession(s.apiEtat))
	mux.HandleFunc("POST /app/api/cles/creer", s.requireAuthSession(s.apiCreerCle))
	mux.HandleFunc("POST /app/api/cles/revoquer", s.requireAuthSession(s.apiRevoquerCle))
	mux.HandleFunc("POST /app/api/cles/supprimer", s.requireAuthSession(s.apiSupprimerCle))
	mux.HandleFunc("POST /app/api/webhooks/ajouter", s.requireAuthSession(s.apiAjouterWebhook))
	mux.HandleFunc("POST /app/api/webhooks/supprimer", s.requireAuthSession(s.apiSupprimerWebhook))
	mux.HandleFunc("POST /app/api/webhooks/tester", s.requireAuthSession(s.apiTesterWebhook))

	// Assistant IA (M29)
	mux.HandleFunc("GET /app/assistant", s.requireAuthSession(s.pageAssistant))
	mux.HandleFunc("POST /app/assistant/identifiant", s.requireAuthSession(s.assistantIdentifiant))
	mux.HandleFunc("POST /app/assistant/anomalies", s.requireAuthSession(s.assistantAnomalies))
	mux.HandleFunc("POST /app/assistant/cleanup", s.requireAuthSession(s.assistantCleanup))
	mux.HandleFunc("POST /app/assistant/securite", s.requireAuthSession(s.assistantSecurite))
	mux.HandleFunc("POST /app/assistant/journal", s.requireAuthSession(s.assistantJournal))

	// Conformité & sécurité (M30)
	mux.HandleFunc("GET /app/compliance", s.requireAuthSession(s.pageCompliance))
	mux.HandleFunc("POST /app/compliance/config", s.requireAuthSession(s.complianceConfigForm))
	mux.HandleFunc("POST /app/compliance/mfa/status", s.requireAuthSession(s.complianceMFAStatus))
	mux.HandleFunc("POST /app/compliance/mfa/init", s.requireAuthSession(s.complianceMFAInit))
	mux.HandleFunc("POST /app/compliance/rgpd", s.requireAuthSession(s.complianceRGPD))
	mux.HandleFunc("POST /app/compliance/pso", s.requireAuthSession(s.compliancePSO))

	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.FS(mustSub("static")))))
	return mux
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

// accueil renvoie vers le tableau de bord si la session est ouverte,
// vers la connexion sinon.
func (s *Server) accueil(w http.ResponseWriter, r *http.Request) {
	if s.session(r) != nil {
		rediriger(w, r, "/app")
		return
	}
	s.rendre(w, http.StatusOK, "login.html", s.donnees(r, Tr(s.langue(r), "login.titre")))
}

func (s *Server) pageLogin(w http.ResponseWriter, r *http.Request) {
	if s.session(r) != nil {
		rediriger(w, r, "/app")
		return
	}
	s.rendre(w, http.StatusOK, "login.html", s.donnees(r, Tr(s.langue(r), "login.titre")))
}

// connexion ouvre une session administrateur et teste la liaison AD.
func (s *Server) connexion(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	domaine := strings.TrimSpace(r.PostFormValue("domaine"))
	controleur := strings.TrimSpace(r.PostFormValue("controleur"))
	utilisateur := strings.TrimSpace(r.PostFormValue("utilisateur"))
	motDePasse := r.PostFormValue("motdepasse") // jamais logué, jamais persisté sur disque
	memoriser := r.PostFormValue("remember") == "on"
	skipVerify := r.PostFormValue("skip_verify") == "on"

	var erreurs []string
	if domaine == "" {
		erreurs = append(erreurs, Tr(langue, "erreur.domaine"))
	}
	if controleur == "" {
		erreurs = append(erreurs, Tr(langue, "erreur.controleur"))
	}
	if utilisateur == "" {
		erreurs = append(erreurs, Tr(langue, "erreur.utilisateur"))
	}
	if len(erreurs) > 0 {
		s.erreurs(w, erreurs)
		return
	}

	// Tentative de création du pool AD (sans validation immédiate).
	// La validation réelle aura lieu lors de l'accès aux routes protégées (requireAuth).
	// skipVerify = true -> TLSSkipVerify = true
	ldapFallback := false
	pool, err := s.creerPoolADSansValidationAvecSkipVerify(domaine, controleur, utilisateur, motDePasse, true, skipVerify)
	if err != nil {
		// Repli LDAP 389 + StartTLS
		ldapFallback = true
		pool, err = s.creerPoolADSansValidationAvecSkipVerify(domaine, controleur, utilisateur, motDePasse, false, skipVerify)
		if err != nil {
			// Impossible de créer le pool même en no-validate : erreur de config
			msg := formaterErreurConnexion(err, langue)
			s.erreurs(w, []string{msg})
			return
		}
	}

	sid := nouveauJeton()
	s.mu.Lock()
	s.sessions[sid] = &session{
		Utilisateur:  utilisateur,
		Domaine:      domaine,
		Controleur:   controleur,
		MotDePasse:   motDePasse,
		LDAPFallback: ldapFallback,
		CreeLe:       time.Now(),
	}
	// Stocker le pool pour cette session (validation différée)
	s.adPools[sid] = pool
	s.mu.Unlock()

	http.SetCookie(w, &http.Cookie{
		Name:     cookieSession,
		Value:    sid,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   int(dureeSession.Seconds()),
	})

	// Si "Mémoriser les identifiants" coché, enregistrer le site en multisite
	if memoriser && motDePasse != "" {
		s.enregistrerSiteMultisite(domaine, controleur, utilisateur, motDePasse, skipVerify)
	}

	// Traçabilité : toute connexion est journalisée (append-only).
	if s.journal != nil {
		detail := fmt.Sprintf("Domaine %s, contrôleur %s", domaine, controleur)
		if ldapFallback {
			detail += " (repli LDAP 389)"
		}
		if skipVerify {
			detail += " (certificat non vérifié)"
		}
		entree := nouvelleEntree("connexion", utilisateur, "succes", detail)
		entree.Utilisateur = utilisateur
		entree.Domaine = domaine
		_ = s.journal.Record(entree)
	}

	rediriger(w, r, "/app")
}

// creerPoolAD tente de créer un pool de connexions AD (avec validation immédiate).
// Si useLDAPS est true, essaie sur le port 636 (LDAPS), sinon sur 389 (LDAP + StartTLS).
func (s *Server) creerPoolAD(domaine, controleur, utilisateur, motDePasse string, useLDAPS bool) (*ad.Pool, error) {
	port := 636
	if !useLDAPS {
		port = 389
	}
	return ad.NewPool(ad.Config{
		Domain:        domaine,
		Controller:    controleur,
		Port:          port,
		UseLDAPS:      useLDAPS,
		AuthMethod:    ad.AuthSimple,
		BindDN:        utilisateur,
		BindPassword:  motDePasse,
		TLSSkipVerify: true, // Phase 0 : certificats auto-signés (labo)
		Timeout:       10 * time.Second,
		MaxConns:      10,
	})
}

// creerPoolADSansValidation crée un pool sans pré-validation de la connexion.
// Utile pour la phase de login : on ne bloque pas l'utilisateur si l'AD est inaccessible.
func (s *Server) creerPoolADSansValidation(domaine, controleur, utilisateur, motDePasse string, useLDAPS bool) (*ad.Pool, error) {
	return s.creerPoolADSansValidationAvecSkipVerify(domaine, controleur, utilisateur, motDePasse, useLDAPS, true)
}

// creerPoolADSansValidationAvecSkipVerify crée un pool sans pré-validation, avec option TLSSkipVerify.
func (s *Server) creerPoolADSansValidationAvecSkipVerify(domaine, controleur, utilisateur, motDePasse string, useLDAPS bool, skipVerify bool) (*ad.Pool, error) {
	port := 636
	if !useLDAPS {
		port = 389
	}
	return ad.NewPoolNoValidate(ad.Config{
		Domain:        domaine,
		Controller:    controleur,
		Port:          port,
		UseLDAPS:      useLDAPS,
		AuthMethod:    ad.AuthSimple,
		BindDN:        utilisateur,
		BindPassword:  motDePasse,
		TLSSkipVerify: skipVerify, // skip_verify = true -> pas de vérification du certificat
		Timeout:       10 * time.Second,
		MaxConns:      10,
	})
}

// enregistrerSiteMultisite enregistre le site dans la configuration multisite si l'utilisateur a coché "Mémoriser".
func (s *Server) enregistrerSiteMultisite(domaine, controleur, utilisateur, motDePasse string, skipVerify bool) {
	// Charger la config multisite existante
	sites, err := multisite.ChargerSites("", "")
	if err != nil {
		sites = []multisite.ProfilDomaine{}
	}

	// Normaliser pour la comparaison (domaine en minuscules comme dans le stockage)
	domaineNorm := strings.ToLower(strings.TrimSpace(domaine))
	controleurNorm := strings.TrimSpace(controleur)

	// Vérifier si le site existe déjà (même domaine + contrôleur)
	for i, site := range sites {
		if site.Domain == domaineNorm && site.Controller == controleurNorm {
			// Mettre à jour
			sites[i].Username = utilisateur
			sites[i].Password = motDePasse
			sites[i].RememberPassword = true
			sites[i].VerifyCertificate = !skipVerify
			_ = multisite.SauvegarderSites(sites, "", "")
			return
		}
	}

	// Générer un ID unique
	id := multisite.NewSiteID(sites)

	// Ajouter nouveau site
	label := fmt.Sprintf("%s (%s)", domaineNorm, controleurNorm)
	profil := multisite.ProfilDomaine{
		ID:                id,
		Label:             label,
		Domain:            domaineNorm,
		Controller:        controleurNorm,
		Username:          utilisateur,
		Password:          motDePasse,
		RememberPassword:  true,
		VerifyCertificate: !skipVerify,
	}
	sites = append(sites, profil)
	_ = multisite.SauvegarderSites(sites, "", "")
}

// formaterErreurConnexion transforme une erreur AD en message utilisateur localisé.
func formaterErreurConnexion(err error, langue string) string {
	msg := err.Error()
	msgLower := strings.ToLower(msg)

	switch {
	case strings.Contains(msgLower, "certificate") || strings.Contains(msgLower, "certificat") || strings.Contains(msgLower, "tls") || strings.Contains(msgLower, "x509"):
		return Tr(langue, "erreur.connexion_ad_certificat", msg)
	case strings.Contains(msgLower, "invalid credentials") || strings.Contains(msgLower, "invalid dn") || strings.Contains(msgLower, "wrong password") || strings.Contains(msgLower, "account locked") || strings.Contains(msgLower, "logon failure") || strings.Contains(msgLower, "identifiant") || strings.Contains(msgLower, "verrouill"):
		return Tr(langue, "erreur.connexion_ad_bind")
	case strings.Contains(msgLower, "connection refused") || strings.Contains(msgLower, "timeout") || strings.Contains(msgLower, "no route") || strings.Contains(msgLower, "unreachable") || strings.Contains(msgLower, "refus") || strings.Contains(msgLower, "inaccessible") || strings.Contains(msgLower, "network"):
		return Tr(langue, "erreur.connexion_ad_reseau")
	default:
		return Tr(langue, "erreur.connexion_ad") + ": " + msg
	}
}

// tableauBord affiche la page d'accueil de l'application.
func (s *Server) tableauBord(w http.ResponseWriter, r *http.Request) {
	sess := s.session(r)
	if sess == nil {
		rediriger(w, r, "/login")
		return
	}

	langue := s.langue(r)
	d := s.donnees(r, Tr(langue, "dash.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	d.LDAPFallback = sess.LDAPFallback

	// Statut AD avec avertissement si repli LDAP
	if sess.LDAPFallback {
		d.StatutAD = Tr(langue, "avertissement.ldap_fallback")
	} else {
		d.StatutAD = Tr(langue, "statut.ad")
	}

	if s.journal != nil {
		entries, err := s.journal.List(audit.Filter{Limit: 8})
		if err == nil {
			d.Actions = entries
		}
		if n, err := s.journal.Count(); err == nil {
			d.NbActions = n
		}
	}

	s.rendre(w, http.StatusOK, "dashboard.html", d)
}

// deconnexion ferme la session.
func (s *Server) deconnexion(w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(cookieSession); err == nil && c.Value != "" {
		s.mu.Lock()
		sess := s.sessions[c.Value]
		// Nettoyer le pool AD associé
		poolKey := sess.Utilisateur + "@" + sess.Domaine
		if sess.LDAPFallback {
			poolKey += ":ldap"
		}
		if pool, ok := s.adPools[poolKey]; ok {
			_ = pool.Close()
			delete(s.adPools, poolKey)
		}
		delete(s.sessions, c.Value)
		s.mu.Unlock()

		if s.journal != nil && sess != nil {
			entree := nouvelleEntree("deconnexion", sess.Utilisateur, "succes", "")
			entree.Utilisateur = sess.Utilisateur
			entree.Domaine = sess.Domaine
			_ = s.journal.Record(entree)
		}
	}
	http.SetCookie(w, &http.Cookie{Name: cookieSession, Value: "", Path: "/", MaxAge: -1})
	rediriger(w, r, "/login")
}

// changerTheme persiste le thème choisi (l'UI est basculée côté client par Alpine).
func (s *Server) changerTheme(w http.ResponseWriter, r *http.Request) {
	_ = r.ParseForm()
	valeur := strings.TrimSpace(r.PostFormValue("theme"))
	if valeur != "clair" && valeur != "sombre" {
		http.Error(w, "theme invalide", http.StatusBadRequest)
		return
	}
	if err := s.majConfig(func(c *config.AppConfig) { c.Theme = valeur }); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	http.SetCookie(w, &http.Cookie{Name: cookieTheme, Value: valeur, Path: "/", MaxAge: 31536000})
	w.WriteHeader(http.StatusNoContent)
}

// changerLangue change la langue de l'interface et recharge la page courante.
func (s *Server) changerLangue(w http.ResponseWriter, r *http.Request) {
	_ = r.ParseForm()
	valeur := strings.TrimSpace(r.PostFormValue("langue"))
	if valeur != "fr" && valeur != "en" {
		http.Error(w, "langue invalide", http.StatusBadRequest)
		return
	}
	if err := s.majConfig(func(c *config.AppConfig) { c.Langue = valeur }); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	http.SetCookie(w, &http.Cookie{Name: cookieLangue, Value: valeur, Path: "/", MaxAge: 31536000})

	vers := cheminSeul(r.Header.Get("Hx-Current-URL"))
	if vers == "" {
		vers = cheminSeul(r.Referer())
	}
	if vers == "" {
		vers = "/login"
	}
	rediriger(w, r, vers)
}

// cheminSeul réduit une URL (absolue ou relative) à son chemin local :
// seule la page courante est une cible de redirection valide, ce qui écarte
// toute redirection ouverte (open redirect) venue des en-têtes client.
func cheminSeul(u string) string {
	if u == "" {
		return ""
	}
	parsed, err := url.Parse(u)
	if err != nil || parsed.Path == "" {
		return ""
	}
	if parsed.RawQuery != "" {
		return parsed.Path + "?" + parsed.RawQuery
	}
	return parsed.Path
}

// majConfig applique une modification sur la configuration persistée.
func (s *Server) majConfig(maj func(*config.AppConfig)) error {
	cfg, err := config.Load(s.opts.ConfigPath)
	if err != nil {
		return err
	}
	maj(&cfg)
	return config.Save(cfg, s.opts.ConfigPath)
}

// donnees construit le modèle de base d'une page.
func (s *Server) donnees(r *http.Request, titre string) PageData {
	langue := s.langue(r)
	theme := s.themeCourant(r)

	d := PageData{
		Titre:   titre,
		Langue:  langue,
		Theme:   theme,
		Version: s.opts.Version,
	}

	if chemin, err := s.cheminConfig(); err == nil {
		d.CheminConfig = chemin
	}
	// Le journal injecté (tests, CLI) prime sur le chemin par défaut : ce qui
	// s'affiche est ce qui est réellement lu.
	if s.opts.AuditPath != "" {
		d.CheminDonnees = s.opts.AuditPath
	} else if dir, err := config.DataDir(); err == nil {
		d.CheminDonnees = filepath.Join(dir, "journal.db")
	}
	return d
}

func (s *Server) cheminConfig() (string, error) {
	if s.opts.ConfigPath != "" {
		return s.opts.ConfigPath, nil
	}
	return config.DefaultPath()
}

// getPool retourne le pool AD pour la session courante, ou le crée si nécessaire.
func (s *Server) getPool(sess *session) (*ad.Pool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Clé unique incluant le fallback LDAP (port différent)
	poolKey := sess.Utilisateur + "@" + sess.Domaine
	if sess.LDAPFallback {
		poolKey += ":ldap"
	}

	if pool, ok := s.adPools[poolKey]; ok {
		return pool, nil
	}

	port := 636
	useLDAPS := true
	if sess.LDAPFallback {
		port = 389
		useLDAPS = false
	}

	pool, err := ad.NewPool(ad.Config{
		Domain:        sess.Domaine,
		Controller:    sess.Controleur,
		Port:          port,
		UseLDAPS:      useLDAPS,
		AuthMethod:    ad.AuthSimple,
		BindDN:        sess.Utilisateur,
		BindPassword:  sess.MotDePasse,
		BaseDN:        "",
		TLSSkipVerify: true, // Phase 0 : certificats auto-signés
		Timeout:       10 * time.Second,
		MaxConns:      10,
	})
	if err != nil {
		return nil, err
	}
	s.adPools[poolKey] = pool
	return pool, nil
}

// session retourne la session liée à la requête, ou nil.
func (s *Server) session(r *http.Request) *session {
	c, err := r.Cookie(cookieSession)
	if err != nil || c.Value == "" {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[c.Value]
	if !ok {
		return nil
	}
	if time.Since(sess.CreeLe) > dureeSession {
		delete(s.sessions, c.Value)
		return nil
	}
	return sess
}

// langue lit le cookie de langue puis la configuration.
func (s *Server) langue(r *http.Request) string {
	if c, err := r.Cookie(cookieLangue); err == nil {
		if c.Value == "fr" || c.Value == "en" {
			return c.Value
		}
	}
	if cfg, err := config.Load(s.opts.ConfigPath); err == nil && (cfg.Langue == "fr" || cfg.Langue == "en") {
		return cfg.Langue
	}
	return "fr"
}

// themeCourant lit le cookie de thème puis la configuration.
func (s *Server) themeCourant(r *http.Request) string {
	if c, err := r.Cookie(cookieTheme); err == nil {
		if c.Value == "clair" || c.Value == "sombre" {
			return c.Value
		}
	}
	if cfg, err := config.Load(s.opts.ConfigPath); err == nil && (cfg.Theme == "clair" || cfg.Theme == "sombre") {
		return cfg.Theme
	}
	return "clair"
}

// rendre exécute un gabarit de page (avec layout complet).
func (s *Server) rendre(w http.ResponseWriter, status int, page string, d PageData) {
	t, ok := s.pages[page]
	if !ok {
		http.Error(w, "page inconnue: "+page, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	if err := t.ExecuteTemplate(w, "layout", d); err != nil {
		// Le flux est déjà parti : on trace plutôt que de planter.
		fmt.Fprintf(os.Stderr, "ui: rendu de %s: %v\n", page, err)
	}
}

// rendreFragment exécute uniquement le gabarit racine d'un fichier (premier
// {{define}} : « content » pour une page, le nom du fragment sinon), sans
// layout. C'est le rendu destiné aux cibles HTMX (innerHTML) et au fetch()
// de l'explorateur : la réponse doit être un fragment autonome, jamais un
// document complet imbriqué.
func (s *Server) rendreFragment(w http.ResponseWriter, status int, fichier string, d PageData) {
	t, ok := s.pages[fichier]
	if !ok {
		http.Error(w, "gabarit inconnu: "+fichier, http.StatusInternalServerError)
		return
	}
	nom := s.racines[fichier]
	if nom == "" {
		http.Error(w, "gabarit sans {{define}}: "+fichier, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	if err := t.ExecuteTemplate(w, nom, d); err != nil {
		fmt.Fprintf(os.Stderr, "ui: rendu fragment %s (%s): %v\n", fichier, nom, err)
	}
}

// regexpDefine extrait les {{define "nom"}} d'un gabarit.
var regexpDefine = regexp.MustCompile(`{{\s*define\s+"([^"]+)"\s*}}`)

// premierDefine retourne le nom du premier {{define}} d'un fichier gabarit
// (racine de ce fichier). Retourne "" si le fichier n'en définit aucun.
func premierDefine(brut []byte) string {
	// Ignorer les {{define}} imbriqués dans un commentaire Go — non applicable
	// ici : les gabarits du dépôt ne commentent pas leurs définitions.
	if m := regexpDefine.FindSubmatch(brut); m != nil {
		return string(m[1])
	}
	return ""
}

// erreurs renvoie un fragment HTML de validation (usage HTMX, cible #erreurs).
// Le statut 422 est interprété par HTMX grâce au correctif
// « htmx:beforeSwap » du gabarit layout.
func (s *Server) erreurs(w http.ResponseWriter, messages []string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusUnprocessableEntity)
	var b strings.Builder
	for _, m := range messages {
		fmt.Fprintf(&b, `<p class="mt-1 text-sm font-medium text-red-600 dark:text-red-400">%s</p>`, m)
	}
	_, _ = w.Write([]byte(b.String()))
}

// erreur renvoie un message unique (variant simple de erreurs).
func (s *Server) erreur(w http.ResponseWriter, message string) {
	s.erreurs(w, []string{message})
}

// rediriger répond en HX-Redirect quand la requête vient d'HTMX, sinon par
// une redirection 303 classique (navigation de formulaire).
func rediriger(w http.ResponseWriter, r *http.Request, vers string) {
	if r.Header.Get("HX-Request") == "true" {
		w.Header().Set("HX-Redirect", vers)
		w.WriteHeader(http.StatusOK)
		return
	}
	http.Redirect(w, r, vers, http.StatusSeeOther)
}

// nouvelleEntree fabrique une entrée de journal horodatée en UTC.
func nouvelleEntree(action, compte, resultat, detail string) models.ActionLogEntry {
	return models.ActionLogEntry{
		Timestamp:  time.Now().UTC().Format(time.RFC3339),
		ActionType: action,
		Compte:     compte,
		Resultat:   resultat,
		Detail:     detail,
	}
}

// nouveauJeton produit un identifiant de session aléatoire (128 bits).
func nouveauJeton() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

// horodatage formate un horodatage ISO 8601 pour l'affichage
// (locales non prises en charge : format numérique explicite).
func horodatage(valeur string) string {
	t, err := time.Parse(time.RFC3339, valeur)
	if err != nil {
		return valeur
	}
	return t.Local().Format("02/01/2006 15:04:05")
}

func mustSub(name string) fs.FS {
	sub, err := fs.Sub(web.FS, name)
	if err != nil {
		panic("ui: assets manquants: " + err.Error())
	}
	return sub
}

// requireAuth vérifie la session et injecte le pool AD dans le contexte.
func (s *Server) requireAuth(handler func(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sess := s.session(r)
		if sess == nil {
			if r.Header.Get("HX-Request") == "true" {
				w.Header().Set("HX-Redirect", "/login")
				w.WriteHeader(http.StatusOK)
			} else {
				http.Redirect(w, r, "/login", http.StatusSeeOther)
			}
			return
		}

		pool, err := s.getPool(sess)
		if err != nil {
			s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
			return
		}

		handler(w, r, sess, pool)
	}
}

// requireAuthSession vérifie seulement la session, sans exiger de pool AD.
// Utile pour les routes qui n'ont pas besoin d'AD (progression, etc.).
func (s *Server) requireAuthSession(handler func(w http.ResponseWriter, r *http.Request, sess *session)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sess := s.session(r)
		if sess == nil {
			if r.Header.Get("HX-Request") == "true" {
				w.Header().Set("HX-Redirect", "/login")
				w.WriteHeader(http.StatusOK)
			} else {
				http.Redirect(w, r, "/login", http.StatusSeeOther)
			}
			return
		}
		handler(w, r, sess)
	}
}

// ─── Page Utilisateurs ─────────────────────────────────────────────────

func (s *Server) pageUsers(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "users.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	// OU par défaut pour l'import
	if cfg, err := config.Load(s.opts.ConfigPath); err == nil && cfg.OUParenteClasses != "" {
		d.CheminConfig = cfg.OUParenteClasses
	}

	s.rendre(w, http.StatusOK, "users.html", d)
}

// usersImport charge un CSV et construit le plan d'import (simulation).
func (s *Server) usersImport(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseMultipartForm(10 << 20); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.fichier_trop_gros"))
		return
	}

	file, _, err := r.FormFile("csv_file")
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_manquant"))
		return
	}
	defer file.Close()

	// Parser CSV (auto-détection séparateur)
	reader := csv.NewReader(file)
	reader.Comma = s.detectCSVSeparator(file)
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true

	records, err := reader.ReadAll()
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_invalide"))
		return
	}

	if len(records) < 2 {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_vide"))
		return
	}

	// Header → indices
	header := records[0]
	indices := make(map[string]int)
	for i, h := range header {
		indices[strings.TrimSpace(strings.ToLower(h))] = i
	}

	get := func(row []string, key string) string {
		if idx, ok := indices[key]; ok && idx < len(row) {
			return strings.TrimSpace(row[idx])
		}
		return ""
	}

	// Construire les lignes brutes
	var rows []importRow
	for _, rec := range records[1:] {
		rows = append(rows, importRow{
			Prenom: get(rec, "prenom"),
			Nom:    get(rec, "nom"),
			OU:     get(rec, "ou"),
			Classe: get(rec, "classe"),
			Email:  get(rec, "email"),
		})
	}

	// Charger la config pour génération identifiants/MDP/mail
	cfg, _ := config.Load(s.opts.ConfigPath)

	// Simulation complète : génération + détection doublons + vérification AD
	simulated := s.simulerCreationUsers(sess, pool, rows, cfg, s.langue(r))

	// Stocker la simulation en session pour l'exécution ultérieure
	sid := s.getSessionID(r)
	if sid != "" {
		s.mu.Lock()
		if sess, ok := s.sessions[sid]; ok {
			sess.DerniereSimulation = simulated
			s.sessions[sid] = sess
		}
		s.mu.Unlock()
	}

	// Rendre le template de simulation
	s.rendreSimulationUsers(w, s.langue(r), simulated)
}

// detectCSVSeparator détecte automatiquement le séparateur (; ou ,)
func (s *Server) detectCSVSeparator(file interface {
	Seek(int64, int) (int64, error)
	Read([]byte) (int, error)
}) rune {
	file.Seek(0, 0)
	buf := make([]byte, 1024)
	n, _ := file.Read(buf)
	file.Seek(0, 0)
	line := string(buf[:n])
	if strings.Count(line, ";") > strings.Count(line, ",") {
		return ';'
	}
	return ','
}

// simulerCreationUsers effectue la simulation complète de création d'utilisateurs
func (s *Server) simulerCreationUsers(sess *session, pool *ad.Pool, rows []importRow, cfg config.AppConfig, langue string) []models.GeneratedUser {
	var simulated []models.GeneratedUser
	var usedIdents []string
	var usedEmails []string

	// Déterminer le type de compte (par défaut élève)
	accountType := models.AccountEleve

	// Charger les identifiants existants dans l'AD si pas skip_ad
	skipAD := false // TODO: lire depuis config ou paramètre
	var existingIdents map[string]bool
	if !skipAD {
		existingIdents = s.getExistingSAMAccountNames(sess, pool)
	}

	for _, row := range rows {
		gu := models.GeneratedUser{
			Source: models.RawUserRow{
				Prenom: row.Prenom, Nom: row.Nom, OU: row.OU, Classe: row.Classe, Email: row.Email,
			},
		}

		// Validation de base
		if row.Prenom == "" || row.Nom == "" {
			gu.Erreur = Tr(langue, "erreur.csv_invalide") + ": prénom/nom manquant"
			simulated = append(simulated, gu)
			continue
		}

		// Génération identifiant + résolution des doublons du lot (même
		// moteur que la version Python : formats, règles de doublons, limite
		// de 20 caractères du sAMAccountName).
		existant := make(map[string]bool, len(usedIdents))
		for _, ident := range usedIdents {
			existant[ident] = true
		}
		identifiant, doublon, err := cfg.MoteurIdentifiants(accountType).
			GenererUnique(row.Prenom, row.Nom, existant)
		if err != nil {
			gu.Erreur = err.Error()
			simulated = append(simulated, gu)
			continue
		}
		gu.Identifiant, gu.DoublonResolu = identifiant, doublon
		usedIdents = append(usedIdents, gu.Identifiant)

		// Vérification AD existant
		if existingIdents != nil && existingIdents[strings.ToLower(gu.Identifiant)] {
			gu.DoublonAD = true
			gu.Erreur = Tr(langue, "users.doublon_ad")
		}

		// Génération mot de passe
		mdp, _ := cfg.GeneratePassword(cfg.PolitiqueMDPEleve)
		gu.MotDePasse = mdp

		// Génération mail
		domaineMail := cfg.DomaineMail
		if domaineMail == "" {
			domaineMail = strings.ToLower(sess.Domaine)
		}
		gu.AdresseMail = cfg.GenerateEmail(row.Prenom, row.Nom, domaineMail)
		if contains(usedEmails, gu.AdresseMail) {
			// Résoudre doublon mail
			base := strings.Split(gu.AdresseMail, "@")[0]
			domain := strings.Split(gu.AdresseMail, "@")[1]
			resolved, _ := cfg.ResolveDuplicate(base, usedEmails, cfg.RegleDoublons)
			gu.AdresseMail = resolved + "@" + domain
		}
		usedEmails = append(usedEmails, gu.AdresseMail)

		// OU cible
		gu.OUCible = row.OU
		if gu.OUCible == "" && row.Classe != "" {
			gu.OUCible = config.BuildUserDN(config.SanitizeDNPart(row.Classe), "OU=Eleves,"+config.DomaineToBaseDN(sess.Domaine))
		} else if gu.OUCible == "" {
			gu.OUCible = "OU=Eleves," + config.DomaineToBaseDN(sess.Domaine)
		}

		// Groupe classe auto
		if cfg.GroupesClasseAuto && row.Classe != "" {
			gu.Groupe = "CN=" + config.SanitizeDNPart(row.Classe) + ",OU=Groupes," + config.DomaineToBaseDN(sess.Domaine)
		}

		simulated = append(simulated, gu)
	}

	return simulated
}

// getExistingSAMAccountNames récupère tous les sAMAccountName existants dans l'AD
func (s *Server) getExistingSAMAccountNames(sess *session, pool *ad.Pool) map[string]bool {
	result := make(map[string]bool)
	// Recherche dans la base du domaine
	baseDN := config.DomaineToBaseDN(sess.Domaine)
	users, err := pool.UserSearch(context.Background(), baseDN, "", nil, 1000)
	if err != nil {
		return result
	}
	for _, u := range users {
		result[strings.ToLower(u.SAMAccountName)] = true
	}
	return result
}

// contains vérifie si une chaîne est dans un slice
func contains(slice []string, item string) bool {
	for _, s := range slice {
		if strings.EqualFold(s, item) {
			return true
		}
	}
	return false
}

// getSessionID extrait l'ID de session depuis la requête
func (s *Server) getSessionID(r *http.Request) string {
	c, err := r.Cookie(cookieSession)
	if err != nil || c.Value == "" {
		return ""
	}
	return c.Value
}

// ─── Users : simulation & exécution ────────────────────────────────────

func (s *Server) usersSimulate(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	// Récupérer la simulation stockée en session
	sid := s.getSessionID(r)
	if sid == "" {
		s.erreur(w, Tr(s.langue(r), "users.aucune_simulation"))
		return
	}

	s.mu.Lock()
	sessionData, ok := s.sessions[sid]
	s.mu.Unlock()

	if !ok || sessionData.DerniereSimulation == nil {
		s.erreur(w, Tr(s.langue(r), "users.aucune_simulation"))
		return
	}

	// Convertir pour le template
	lines := make([]SimulatedUserLine, len(sessionData.DerniereSimulation))
	valides, erreurs := 0, 0
	for i, u := range sessionData.DerniereSimulation {
		lines[i] = SimulatedUserLine{
			Prenom:        u.Source.Prenom,
			Nom:           u.Source.Nom,
			Identifiant:   u.Identifiant,
			Mail:          u.AdresseMail,
			MotDePasse:    u.MotDePasse,
			OUCible:       u.OUCible,
			Groupe:        u.Groupe,
			DoublonResolu: u.DoublonResolu,
			DoublonAD:     u.DoublonAD,
			Erreur:        u.Erreur,
		}
		if u.Erreur != "" {
			erreurs++
		} else {
			valides++
		}
	}

	d := s.donnees(r, Tr(s.langue(r), "users.simulate_titre"))
	d.Simulation = &SimulationResult{
		Lignes:  lines,
		Valides: valides,
		Erreurs: erreurs,
		Total:   len(lines),
	}
	d.LDAPFallback = sess.LDAPFallback

	// Parser les options du formulaire
	if err := r.ParseForm(); err == nil {
		d.ForcerMdp = r.FormValue("forcer_mdp")
		d.SkipAd = r.FormValue("skip_ad")
	}

	s.rendre(w, http.StatusOK, "users_simulate.html", d)
}

func (s *Server) usersExecute(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	sid := s.getSessionID(r)
	if sid == "" {
		s.erreur(w, Tr(s.langue(r), "users.aucune_simulation"))
		return
	}

	// Récupérer la simulation
	s.mu.Lock()
	sessionData, ok := s.sessions[sid]
	s.mu.Unlock()

	if !ok || sessionData.DerniereSimulation == nil {
		s.erreur(w, Tr(s.langue(r), "users.aucune_simulation"))
		return
	}

	// Parser le formulaire
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	forcerMdp := r.FormValue("forcer_mdp") == "1"
	_ = r.FormValue("skip_ad") // lu pour compatibilité future

	// Créer une opération de suivi avec contexte annulable (A12).
	opID, opCtx := s.nouvelOperation("users")
	total := len(sessionData.DerniereSimulation)

	var resultLines []MigrationExecLine
	crees, echecs := 0, 0

	for i, gu := range sessionData.DerniereSimulation {
		// Vérifier l'annulation avant chaque ligne (A12).
		if progress.Annulable(opCtx) {
			s.terminerOperation(opID, false, Tr(s.langue(r), "progress.annulee"), "")
			s.rendre(w, http.StatusOK, "users_execute.html", s.donnees(r, Tr(s.langue(r), "users.execute_titre")))
			return
		}

		s.majProgression(opID, i, fmt.Sprintf("%s %s", gu.Identifiant, gu.NomComplet()))

		if gu.Erreur != "" {
			// Ligne en erreur, ne pas tenter de créer
			resultLines = append(resultLines, MigrationExecLine{
				Prenom: gu.Source.Prenom, Nom: gu.Source.Nom, Identifiant: gu.Identifiant,
				Statut: "echec", Detail: gu.Erreur,
			})
			echecs++
			continue
		}

		// Construire les paramètres de création
		params := ad.UserCreateParams{
			SAMAccountName:    gu.Identifiant,
			UserPrincipalName: gu.Identifiant + "@" + sess.Domaine,
			DisplayName:       gu.Source.Prenom + " " + gu.Source.Nom,
			GivenName:         gu.Source.Prenom,
			SN:                gu.Source.Nom,
			InitialPassword:   gu.MotDePasse,
			OU:                gu.OUCible,
			Description:       "Créé via EduSync-AD",
			Email:             gu.AdresseMail,
			Enabled:           true,
			Groups:            []string{},
		}
		if gu.Groupe != "" {
			params.Groups = []string{gu.Groupe}
		}

		// Simulation AD (vérification finale)
		simResult, err := pool.UserCreateSimulate(opCtx, params)
		if err != nil || !simResult.WouldSucceed {
			resultLines = append(resultLines, MigrationExecLine{
				Prenom: gu.Source.Prenom, Nom: gu.Source.Nom, Identifiant: gu.Identifiant,
				Statut: "echec", Detail: simResult.Error,
			})
			echecs++
			continue
		}

		// Exécution réelle
		createdUser, err := pool.UserCreate(opCtx, params)
		if err != nil {
			resultLines = append(resultLines, MigrationExecLine{
				Prenom: gu.Source.Prenom, Nom: gu.Source.Nom, Identifiant: gu.Identifiant,
				Statut: "echec", Detail: err.Error(),
			})
			echecs++
			continue
		}

		// Forcer changement MDP si demandé
		if forcerMdp {
			_ = pool.UserResetPassword(opCtx, createdUser.DN, gu.MotDePasse)
		}

		// Journaliser
		if s.journal != nil {
			entree := nouvelleEntree("creation_utilisateur", gu.Identifiant, "succes",
				fmt.Sprintf("Prénom: %s, Nom: %s, OU: %s", gu.Source.Prenom, gu.Source.Nom, gu.OUCible))
			entree.Utilisateur = sess.Utilisateur
			entree.Domaine = sess.Domaine
			_ = s.journal.Record(entree)
		}

		resultLines = append(resultLines, MigrationExecLine{
			Prenom: gu.Source.Prenom, Nom: gu.Source.Nom, Identifiant: gu.Identifiant,
			Statut: "succes", Detail: "Créé dans " + gu.OUCible,
		})
		crees++
	}

	// Marquer l'opération comme terminée.
	s.majProgression(opID, total, Tr(s.langue(r), "progress.termine"))
	s.terminerOperation(opID, true, Tr(s.langue(r), "progress.termine"), "")

	// Nettoyer la simulation
	s.mu.Lock()
	if sessionData, ok := s.sessions[sid]; ok {
		sessionData.DerniereSimulation = nil
		s.sessions[sid] = sessionData
	}
	s.mu.Unlock()

	d := s.donnees(r, Tr(s.langue(r), "users.execute_titre"))
	d.Resultat = &ExecuteResult{
		Crees:  crees,
		Echecs: echecs,
		Lignes: resultLines,
	}
	d.LDAPFallback = sess.LDAPFallback

	s.rendre(w, http.StatusOK, "users_execute.html", d)
}

// ─── Page Mot de passe ─────────────────────────────────────────────────

func (s *Server) pagePassword(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "pwd.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "password.html", d)
}

func (s *Server) passwordSimulate(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	mode := r.FormValue("mode")
	ou := r.FormValue("ou")
	groupDN := r.FormValue("group_dn")
	policyName := r.FormValue("policy")
	forceChange := r.FormValue("force_change") == "1"

	cfg, _ := config.Load(s.opts.ConfigPath)

	// Déterminer la politique
	policy := cfg.PolitiqueMDPEleve
	if policyName == "personnel" {
		policy = cfg.PolitiqueMDPPersonnel
	}

	// Paramètres de réinitialisation
	params := ad.PasswordResetParams{
		OU:          ou,
		GroupDN:     groupDN,
		Policy:      s.configPolicyToADPolicy(policy),
		ForceChange: forceChange,
		DryRun:      true, // Simulation
	}

	var results []ad.PasswordResetResult
	var err error

	switch mode {
	case "ou":
		if ou == "" {
			s.erreur(w, Tr(s.langue(r), "pwd.erreur_ou_vide"))
			return
		}
		params.OU = ou
		results, err = pool.PasswordResetBulk(r.Context(), params)
	case "group":
		if groupDN == "" {
			s.erreur(w, Tr(s.langue(r), "pwd.erreur_groupe_vide"))
			return
		}
		params.GroupDN = groupDN
		results, err = pool.PasswordResetBulk(r.Context(), params)
	case "csv":
		if err := r.ParseMultipartForm(10 << 20); err != nil {
			s.erreur(w, Tr(s.langue(r), "erreur.fichier_trop_gros"))
			return
		}
		file, _, err := r.FormFile("csv_file")
		if err != nil {
			s.erreur(w, Tr(s.langue(r), "erreur.csv_manquant"))
			return
		}
		defer file.Close()

		reader := csv.NewReader(file)
		reader.Comma = s.detectCSVSeparator(file)
		reader.FieldsPerRecord = -1
		reader.LazyQuotes = true

		records, err := reader.ReadAll()
		if err != nil {
			s.erreur(w, Tr(s.langue(r), "erreur.csv_invalide"))
			return
		}

		if len(records) < 2 {
			s.erreur(w, Tr(s.langue(r), "erreur.csv_vide"))
			return
		}

		header := records[0]
		indices := make(map[string]int)
		for i, h := range header {
			indices[strings.TrimSpace(strings.ToLower(h))] = i
		}

		get := func(row []string, key string) string {
			if idx, ok := indices[key]; ok && idx < len(row) {
				return strings.TrimSpace(row[idx])
			}
			return ""
		}

		var targetDNs []string
		for _, rec := range records[1:] {
			dn := get(rec, "identifiant")
			if dn == "" {
				dn = get(rec, "samaccountname")
			}
			if dn != "" {
				targetDNs = append(targetDNs, dn)
			}
		}

		if len(targetDNs) == 0 {
			s.erreur(w, Tr(s.langue(r), "erreur.csv_vide"))
			return
		}

		// Pour chaque DN, faire une simulation individuelle
		results = make([]ad.PasswordResetResult, 0, len(targetDNs))
		for _, dn := range targetDNs {
			singleParams := params
			singleParams.TargetDN = dn
			singleParams.OU = ""
			singleParams.GroupDN = ""
			r, err := pool.PasswordResetBulk(r.Context(), singleParams)
			if err != nil {
				results = append(results, ad.PasswordResetResult{
					UserDN: dn, Error: fmt.Sprintf("simulation: %v", err),
				})
			} else if len(r) > 0 {
				results = append(results, r[0])
			}
		}
	default:
		s.erreur(w, Tr(s.langue(r), "pwd.erreur_mode_invalide"))
		return
	}

	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Convertir pour le template
	lines := make([]PasswordSimLine, len(results))
	valides, erreurs := 0, 0
	for i, res := range results {
		generated := res.Generated
		if res.NewPassword == "" && generated {
			// Générer un MDP pour l'affichage (simulation)
			pwd, _ := params.Policy.Generate()
			res.NewPassword = pwd
		}
		lines[i] = PasswordSimLine{
			Prenom:      res.DisplayName, // On n'a pas le prénom/nom séparés ici
			Nom:         "",
			Identifiant: res.SAMAccountName,
			Mail:        "",
			NouveauMDP:  res.NewPassword,
			OUCible:     res.UserDN,
			Erreur:      res.Error,
		}
		if res.Error != "" {
			erreurs++
		} else {
			valides++
		}
	}

	// Stocker la simulation en session
	sid := s.getSessionID(r)
	if sid != "" {
		s.mu.Lock()
		if sess, ok := s.sessions[sid]; ok {
			sess.DerniereSimPassword = results
			sess.DerniereSimPasswordParams = params
			s.sessions[sid] = sess
		}
		s.mu.Unlock()
	}

	d := s.donnees(r, Tr(s.langue(r), "pwd.simulate_titre"))
	d.SimulationPwd = &PasswordSimResult{
		Lignes:  lines,
		Valides: valides,
		Erreurs: erreurs,
		Total:   len(lines),
	}
	d.LDAPFallback = sess.LDAPFallback
	d.ForceChange = forceChange

	s.rendre(w, http.StatusOK, "password_simulate.html", d)
}

func (s *Server) passwordExecute(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	forceChange := r.FormValue("force_change") == "1"

	sid := s.getSessionID(r)
	if sid == "" {
		s.erreur(w, Tr(s.langue(r), "pwd.aucune_simulation"))
		return
	}

	s.mu.Lock()
	sessionData, ok := s.sessions[sid]
	s.mu.Unlock()

	if !ok || sessionData.DerniereSimPassword == nil {
		s.erreur(w, Tr(s.langue(r), "pwd.aucune_simulation"))
		return
	}

	// Rejouer avec DryRun = false
	params := sessionData.DerniereSimPasswordParams
	params.DryRun = false
	params.ForceChange = forceChange

	results, err := pool.PasswordResetBulk(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
		return
	}

	var resultLines []MigrationExecLine
	crees, echecs := 0, 0

	for _, res := range results {
		if res.Error != "" {
			resultLines = append(resultLines, MigrationExecLine{
				Identifiant: res.SAMAccountName,
				NouveauMDP:  res.NewPassword,
				Statut:      "echec",
				Detail:      res.Error,
			})
			echecs++
		} else {
			// Journaliser
			if s.journal != nil {
				entree := nouvelleEntree("reinitialisation_mdp", res.SAMAccountName, "succes",
					fmt.Sprintf("OU: %s, généré: %v", res.UserDN, res.Generated))
				entree.Utilisateur = sess.Utilisateur
				entree.Domaine = sess.Domaine
				_ = s.journal.Record(entree)
			}
			resultLines = append(resultLines, MigrationExecLine{
				Identifiant: res.SAMAccountName,
				NouveauMDP:  res.NewPassword,
				Statut:      "succes",
				Detail:      "Réinitialisé avec succès",
			})
			crees++
		}
	}

	// Nettoyer la simulation
	s.mu.Lock()
	if sessionData, ok := s.sessions[sid]; ok {
		sessionData.DerniereSimPassword = nil
		sessionData.DerniereSimPasswordParams = ad.PasswordResetParams{}
		s.sessions[sid] = sessionData
	}
	s.mu.Unlock()

	d := s.donnees(r, Tr(s.langue(r), "pwd.execute_titre"))
	d.ResultatPwd = &PasswordExecResult{
		Crees:  crees,
		Echecs: echecs,
		Lignes: resultLines,
	}
	d.LDAPFallback = sess.LDAPFallback

	s.rendre(w, http.StatusOK, "password_execute.html", d)
}

// configPolicyToADPolicy convertit models.PasswordPolicy en ad.PasswordPolicy
func (s *Server) configPolicyToADPolicy(p models.PasswordPolicy) ad.PasswordPolicy {
	return ad.PasswordPolicy{
		Length:           p.Longueur,
		RequireUppercase: p.Majuscules,
		RequireDigits:    p.Chiffres,
		RequireSpecial:   p.CaracteresSpeciaux,
		ForbidUserInfo:   p.MotDePasseIdentique,
		MaxAgeDays:       0,
		HistorySize:      0,
		MinAgeDays:       0,
	}
}

// ─── Page Migration ────────────────────────────────────────────────────

func (s *Server) pageMigration(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "migration.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "migration.html", d)
}

func (s *Server) migrationPlan(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseMultipartForm(10 << 20); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.fichier_trop_gros"))
		return
	}

	file, _, err := r.FormFile("csv_file")
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_manquant"))
		return
	}
	defer file.Close()

	reader := csv.NewReader(file)
	reader.Comma = s.detectCSVSeparator(file)
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true

	records, err := reader.ReadAll()
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_invalide"))
		return
	}

	if len(records) < 2 {
		s.erreur(w, Tr(s.langue(r), "erreur.csv_vide"))
		return
	}

	// Header → indices
	header := records[0]
	indices := make(map[string]int)
	for i, h := range header {
		indices[strings.TrimSpace(strings.ToLower(h))] = i
	}

	get := func(row []string, key string) string {
		if idx, ok := indices[key]; ok && idx < len(row) {
			return strings.TrimSpace(row[idx])
		}
		return ""
	}

	type importRow struct {
		Identifiant, OUSource, OUCible, Prenom, Nom string
	}
	var rows []importRow
	for _, rec := range records[1:] {
		rows = append(rows, importRow{
			Identifiant: get(rec, "identifiant"),
			OUSource:    get(rec, "ou_source"),
			OUCible:     get(rec, "ou_cible"),
			Prenom:      get(rec, "prenom"),
			Nom:         get(rec, "nom"),
		})
	}

	// Construire le plan de migration
	var adRows []ad.MigrationRow
	for _, row := range rows {
		adRows = append(adRows, ad.MigrationRow{
			SAMAccountName: row.Identifiant,
			SourceOU:       row.OUSource,
			TargetOU:       row.OUCible,
		})
	}

	plan, err := pool.MigrationPlanFromCSV(r.Context(), adRows)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Convertir pour le template
	lines := make([]MigrationPlanLine, len(plan.Rows))
	for i, row := range plan.Rows {
		lines[i] = MigrationPlanLine{
			Identifiant: row.SAMAccountName,
			NomComplet:  row.DisplayName,
			OUSource:    row.SourceOU,
			OUCible:     row.TargetOU,
			Action:      s.actionLabel(row, s.langue(r)),
			Erreur:      row.Error,
		}
	}

	// Stocker le plan en session
	sid := s.getSessionID(r)
	if sid != "" {
		s.mu.Lock()
		if sess, ok := s.sessions[sid]; ok {
			sess.DernierPlanMigration = plan
			s.sessions[sid] = sess
		}
		s.mu.Unlock()
	}

	d := s.donnees(r, Tr(s.langue(r), "migration.plan_titre"))
	d.PlanMigration = &MigrationPlanResult{
		Lignes:   lines,
		Total:    plan.Total,
		WillMove: plan.WillMove,
		WillSkip: plan.WillSkip,
	}
	d.LDAPFallback = sess.LDAPFallback

	s.rendre(w, http.StatusOK, "migration_plan.html", d)
}

func (s *Server) migrationSimulate(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	sid := s.getSessionID(r)
	if sid == "" {
		s.erreur(w, Tr(s.langue(r), "migration.aucune_plan"))
		return
	}

	s.mu.Lock()
	sessionData, ok := s.sessions[sid]
	s.mu.Unlock()

	if !ok || sessionData.DernierPlanMigration == nil {
		s.erreur(w, Tr(s.langue(r), "migration.aucune_plan"))
		return
	}

	plan := sessionData.DernierPlanMigration
	_, err := pool.MigrationSimulate(r.Context(), plan)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Convertir pour le template
	lines := make([]MigrationSimLine, len(plan.Rows))
	valides, erreurs := 0, 0
	for i, row := range plan.Rows {
		lines[i] = MigrationSimLine{
			Identifiant: row.SAMAccountName,
			NomComplet:  row.DisplayName,
			OUSource:    row.SourceOU,
			OUCible:     row.TargetOU,
			Action:      s.actionLabel(row, s.langue(r)),
			Erreur:      row.Error,
		}
		if row.Error != "" {
			erreurs++
		} else if row.SourceOU != row.TargetOU {
			valides++
		} else {
			valides++ // skip aussi compté comme valide
		}
	}

	d := s.donnees(r, Tr(s.langue(r), "migration.simulation_titre"))
	d.SimulationMigration = &MigrationSimResult{
		Lignes:  lines,
		Total:   len(lines),
		Valides: valides,
		Erreurs: erreurs,
	}
	d.LDAPFallback = sess.LDAPFallback

	s.rendre(w, http.StatusOK, "migration_simulate.html", d)
}

func (s *Server) migrationExecute(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	sid := s.getSessionID(r)
	if sid == "" {
		s.erreur(w, Tr(s.langue(r), "migration.aucune_plan"))
		return
	}

	s.mu.Lock()
	sessionData, ok := s.sessions[sid]
	s.mu.Unlock()

	if !ok || sessionData.DernierPlanMigration == nil {
		s.erreur(w, Tr(s.langue(r), "migration.aucune_plan"))
		return
	}

	plan := sessionData.DernierPlanMigration
	err := pool.MigrationExecute(r.Context(), plan)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Groupes de classe auto (post-migration)
	cfg, _ := config.Load(s.opts.ConfigPath)
	if cfg.GroupesClasseAuto {
		// Pour chaque OU cible qui a reçu des élèves, sync le groupe
		targetOUs := make(map[string]bool)
		for _, row := range plan.Rows {
			if row.Error == "" && row.SourceOU != row.TargetOU {
				targetOUs[row.TargetOU] = true
			}
		}
		for ou := range targetOUs {
			_ = pool.MigrationGroupsByOU(r.Context(), ou, "Classe")
		}
	}

	var resultLines []MigrationExecLine
	crees, echecs := 0, 0

	for _, row := range plan.Rows {
		if row.Error != "" {
			resultLines = append(resultLines, MigrationExecLine{
				Identifiant: row.SAMAccountName,
				NomComplet:  row.DisplayName,
				OUSource:    row.SourceOU,
				OUCible:     row.TargetOU,
				Statut:      "echec",
				Detail:      row.Error,
			})
			echecs++
		} else if row.SourceOU == row.TargetOU {
			resultLines = append(resultLines, MigrationExecLine{
				Identifiant: row.SAMAccountName,
				NomComplet:  row.DisplayName,
				OUSource:    row.SourceOU,
				OUCible:     row.TargetOU,
				Statut:      "skip",
				Detail:      "Déjà dans l'OU cible",
			})
		} else {
			// Journaliser
			if s.journal != nil {
				entree := nouvelleEntree("migration", row.SAMAccountName, "succes",
					fmt.Sprintf("Déplacé de %s vers %s", row.SourceOU, row.TargetOU))
				entree.Utilisateur = sess.Utilisateur
				entree.Domaine = sess.Domaine
				_ = s.journal.Record(entree)
			}
			resultLines = append(resultLines, MigrationExecLine{
				Identifiant: row.SAMAccountName,
				NomComplet:  row.DisplayName,
				OUSource:    row.SourceOU,
				OUCible:     row.TargetOU,
				Statut:      "succes",
				Detail:      "Déplacé avec succès",
			})
			crees++
		}
	}

	// Nettoyer le plan
	s.mu.Lock()
	if sessionData, ok := s.sessions[sid]; ok {
		sessionData.DernierPlanMigration = nil
		s.sessions[sid] = sessionData
	}
	s.mu.Unlock()

	d := s.donnees(r, Tr(s.langue(r), "migration.execute_titre"))
	d.MigrationExecute = &MigrationExecResult{
		Crees:  crees,
		Echecs: echecs,
		Lignes: resultLines,
	}
	d.LDAPFallback = sess.LDAPFallback

	s.rendre(w, http.StatusOK, "migration_execute.html", d)
}

// ─── Page Départs ──────────────────────────────────────────────────────

func (s *Server) pageDeparts(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "departs.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "departs.html", d)
}

func (s *Server) departsSimulate(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	mode := ad.DepartMode(r.FormValue("mode"))
	delaiJours := 0
	if v := r.FormValue("delai_jours"); v != "" {
		fmt.Sscanf(v, "%d", &delaiJours)
	}

	params := ad.DepartParams{
		UserDN:     userDN,
		Mode:       mode,
		DelaiJours: delaiJours,
		Motif:      r.FormValue("motif"),
	}

	result, err := pool.DepartSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.rendreDepartResultat(w, langue, result)
}

func (s *Server) departsExecute(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	mode := ad.DepartMode(r.FormValue("mode"))
	delaiJours := 0
	if v := r.FormValue("delai_jours"); v != "" {
		fmt.Sscanf(v, "%d", &delaiJours)
	}

	params := ad.DepartParams{
		UserDN:     userDN,
		Mode:       mode,
		DelaiJours: delaiJours,
		Motif:      r.FormValue("motif"),
	}

	result, err := pool.DepartExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Journaliser le départ
	if s.journal != nil {
		entree := nouvelleEntree("depart", result.SAMAccountName, result.Statut,
			fmt.Sprintf("Mode: %s, délai: %d jours, motif: %s", params.Mode, params.DelaiJours, params.Motif))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	// Si mode différé, enregistrer la suppression planifiée dans le journal
	if params.Mode == ad.DepartModeDiffere && result.Statut == "succes" && s.journal != nil {
		sid := s.getSessionID(r)
		_ = s.journal.RecordPendingDeletion(result.UserDN, result.SAMAccountName, result.DisplayName, sid, params.DelaiJours)
	}

	s.rendreDepartResultat(w, langue, result)
}

// rendreDepartResultat affiche le résultat d'un départ (simulation ou exécution).
func (s *Server) rendreDepartResultat(w http.ResponseWriter, langue string, result *ad.DepartResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">Désactivation / Réactivation</h2>`)
	b.WriteString(`<div class="mt-2 space-y-2">`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>Identifiant:</strong> %s</p>`, result.SAMAccountName))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>Nom:</strong> %s</p>`, result.DisplayName))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>Mode:</strong> %s</p>`, result.Mode))
	if result.DelaiJours > 0 {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>Délai avant purge:</strong> %d jours</p>`, result.DelaiJours))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>Statut:</strong> %s</p>`, result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>Détail:</strong> %s</p>`, result.Detail))
	b.WriteString(`</div></div>`)
	w.Write([]byte(b.String()))
}

// ─── Page Paramètres globaux ───────────────────────────────────────────

func (s *Server) pageParametres(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "parametres.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	s.rendre(w, http.StatusOK, "parametres.html", d)
}

// parametresComptes sauvegarde l'onglet Comptes (génération identifiants,
// doublons, groupes de classe, OU).
func (s *Server) parametresComptes(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	maj := func(cfg *config.AppConfig) {
		cfg.IdentifiantFormatEleve = strings.TrimSpace(r.FormValue("format_eleve"))
		cfg.IdentifiantFormatPersonnel = strings.TrimSpace(r.FormValue("format_personnel"))
		cfg.RegleDoublons = models.DoublonRule(strings.TrimSpace(r.FormValue("regle_doublons")))
		cfg.ReglePrenomCompose = models.PrenomComposeRule(strings.TrimSpace(r.FormValue("regle_prenom")))
		cfg.GroupesClasseAuto = r.FormValue("groupes_auto") == "1"
		cfg.OUParenteClasses = strings.TrimSpace(r.FormValue("ou_parente"))
		cfg.OUArchive = strings.TrimSpace(r.FormValue("ou_archive"))
		if v := r.FormValue("delai_suppression"); v != "" {
			fmt.Sscanf(v, "%d", &cfg.DelaiSuppressionJours)
		}
		cfg.DomaineMail = strings.TrimSpace(r.FormValue("domaine_mail"))
		cfg.FormatMail = strings.TrimSpace(r.FormValue("format_mail"))
	}
	if err := s.majConfig(maj); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.config")+": "+err.Error())
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusNoContent)
}

// parametresMotsDePasse sauvegarde l'onglet Mots de passe (politiques).
func (s *Server) parametresMotsDePasse(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	maj := func(cfg *config.AppConfig) {
		// Politique élèves
		if v := r.FormValue("pwd_eleve_longueur"); v != "" {
			fmt.Sscanf(v, "%d", &cfg.PolitiqueMDPEleve.Longueur)
		}
		cfg.PolitiqueMDPEleve.Majuscules = r.FormValue("pwd_eleve_majuscules") == "1"
		cfg.PolitiqueMDPEleve.Chiffres = r.FormValue("pwd_eleve_chiffres") == "1"
		cfg.PolitiqueMDPEleve.CaracteresSpeciaux = r.FormValue("pwd_eleve_speciaux") == "1"
		cfg.PolitiqueMDPEleve.MotDePasseIdentique = r.FormValue("pwd_eleve_interdit_info") == "1"

		// Politique personnels
		if v := r.FormValue("pwd_personnel_longueur"); v != "" {
			fmt.Sscanf(v, "%d", &cfg.PolitiqueMDPPersonnel.Longueur)
		}
		cfg.PolitiqueMDPPersonnel.Majuscules = r.FormValue("pwd_personnel_majuscules") == "1"
		cfg.PolitiqueMDPPersonnel.Chiffres = r.FormValue("pwd_personnel_chiffres") == "1"
		cfg.PolitiqueMDPPersonnel.CaracteresSpeciaux = r.FormValue("pwd_personnel_speciaux") == "1"
		cfg.PolitiqueMDPPersonnel.MotDePasseIdentique = r.FormValue("pwd_personnel_interdit_info") == "1"
	}
	if err := s.majConfig(maj); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.config")+": "+err.Error())
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusNoContent)
}

// parametresApparence sauvegarde l'onglet Apparence (thème, langue).
func (s *Server) parametresApparence(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	maj := func(cfg *config.AppConfig) {
		theme := strings.TrimSpace(r.FormValue("theme"))
		if theme == "clair" || theme == "sombre" {
			cfg.Theme = theme
		}
		langue := strings.TrimSpace(r.FormValue("langue"))
		if langue == "fr" || langue == "en" {
			cfg.Langue = langue
		}
	}
	if err := s.majConfig(maj); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.config")+": "+err.Error())
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusNoContent)
}

// actionLabel retourne le libellé d'action pour une ligne de migration
func (s *Server) actionLabel(row ad.MigrationRow, langue string) string {
	if row.Error != "" {
		return Tr(langue, "migration.action.erreur")
	}
	if row.SourceOU == row.TargetOU {
		return Tr(langue, "migration.action.skip")
	}
	return Tr(langue, "migration.action.move")
}

// ─── Page Explorateur ──────────────────────────────────────────────────

func (s *Server) pageExplorer(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "explorer.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	// Arbre rendu côté serveur (affichage immédiat, utilisable sans JS) ;
	// en cas d'échec, le client retente un chargement à vide.
	tree, err := pool.BuildTree(r.Context(), pool.BaseDN(), 3, true)
	if err != nil {
		d.Erreurs = append(d.Erreurs, Tr(s.langue(r), "erreur.connexion_ad")+": "+err.Error())
	} else {
		d.ExplorerTree = tree
	}
	s.rendre(w, http.StatusOK, "explorer.html", d)
}

func (s *Server) explorerSearch(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)

	filter := ad.SearchFilter{
		Query:           r.PostFormValue("q"),
		ObjectTypes:     r.Form["object_types"],
		BaseDN:          r.FormValue("base_dn"),
		Cookie:          r.PostFormValue("cookie"),
		PageSize:        100,
		IncludeDisabled: r.FormValue("include_disabled") == "1",
	}

	result, err := pool.Search(r.Context(), filter)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	d := s.donnees(r, Tr(langue, "explorer.results_title"))
	d.SearchResult = result
	d.LDAPFallback = sess.LDAPFallback
	s.rendreFragment(w, http.StatusOK, "explorer_results.html", d)
}

func (s *Server) explorerTree(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	langue := s.langue(r)
	parentDN := r.FormValue("parent_dn")
	includeGroups := r.FormValue("include_groups") == "1"

	if parentDN == "" {
		tree, err := pool.BuildTree(r.Context(), pool.BaseDN(), 3, includeGroups)
		if err != nil {
			s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
			return
		}
		d := s.donnees(r, Tr(langue, "explorer.tree_title"))
		d.ExplorerTree = tree
		d.LDAPFallback = sess.LDAPFallback
		s.rendreFragment(w, http.StatusOK, "explorer_tree.html", d)
		return
	}

	nodes, err := pool.GetSubTree(r.Context(), parentDN, true)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	d := s.donnees(r, "")
	d.ExplorerNodes = nodes
	d.LDAPFallback = sess.LDAPFallback
	s.rendreFragment(w, http.StatusOK, "explorer_tree.html", d)
}

func (s *Server) explorerDetails(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	dn := r.FormValue("dn")
	if dn == "" {
		s.erreur(w, Tr(s.langue(r), "explorer.erreur_dn_manquant"))
		return
	}

	langue := s.langue(r)
	user, err := pool.UserGet(r.Context(), dn)
	if err == nil {
		d := s.donnees(r, Tr(langue, "explorer.details_title"))
		d.ExplorerUser = user
		d.LDAPFallback = sess.LDAPFallback
		s.rendreFragment(w, http.StatusOK, "explorer_details.html", d)
		return
	}

	group, err := pool.GroupGet(r.Context(), dn)
	if err == nil {
		d := s.donnees(r, Tr(langue, "explorer.details_title"))
		d.ExplorerGroup = group
		d.LDAPFallback = sess.LDAPFallback
		s.rendreFragment(w, http.StatusOK, "explorer_details.html", d)
		return
	}

	// Ni utilisateur ni groupe : peut-être une OU (cas des résultats de
	// recherche qui incluent les OU).
	ou, err := pool.OUGet(r.Context(), dn)
	if err == nil {
		d := s.donnees(r, Tr(langue, "explorer.details_title"))
		d.ExplorerOU = ou
		d.LDAPFallback = sess.LDAPFallback
		s.rendreFragment(w, http.StatusOK, "explorer_details.html", d)
		return
	}

	s.erreur(w, Tr(langue, "explorer.erreur_dn_manquant")+": "+dn)
}

// ─── Page Export ───────────────────────────────────────────────────────

func (s *Server) pageExport(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "export.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	// Pré-remplissage OU + liste de premier niveau pour le datalist
	// (meilleure chance que la saisie libre, sans dépendre d'un arbre complet).
	d.ExportOU = "OU=Eleves," + pool.BaseDN()
	if ous, err := pool.OUSearch(r.Context(), pool.BaseDN(), ""); err == nil {
		d.ExportOUs = ous
	}
	s.rendre(w, http.StatusOK, "export.html", d)
}

// champsExport convertit les valeurs de formulaire en champs d'export valides.
// Le mot de passe et le QR code sont écartés ici : le premier n'est jamais
// lisible depuis l'AD (attribut en écriture seule), le second n'a de sens que
// dans un PDF (exportPDF l'ajoute explicitement).
func champsExport(valeurs, defauts []string) []ad.ExportField {
	permis := map[ad.ExportField]bool{}
	for _, f := range ad.AllExportFields {
		permis[f] = true
	}
	var out []ad.ExportField
	for _, v := range valeurs {
		f := ad.ExportField(v)
		if permis[f] && f != ad.FieldPassword && f != ad.FieldQRCode {
			out = append(out, f)
		}
	}
	if len(out) == 0 {
		for _, d := range defauts {
			out = append(out, ad.ExportField(d))
		}
	}
	return out
}

// chercheUsersExport charge les utilisateurs d'une OU (sous-OU incluses).
func chercheUsersExport(r *http.Request, pool *ad.Pool, ou string) ([]*ad.User, error) {
	return pool.UserSearch(r.Context(), ou,
		"(&(objectClass=user)(objectCategory=person))",
		[]string{"dn", "sAMAccountName", "displayName", "givenName", "sn", "mail",
			"ou", "userPrincipalName", "description", "title", "department",
			"employeeID", "telephoneNumber", "mobile"},
		0)
}

// journaliseExport écrit une entrée de traçabilité pour un export généré.
func (s *Server) journaliseExport(sess *session, action, ou string, nb, nbChamps int) {
	if s.journal == nil {
		return
	}
	entree := nouvelleEntree(action, sess.Utilisateur, "succes",
		fmt.Sprintf("OU: %s, %d utilisateurs, %d champs", ou, nb, nbChamps))
	entree.Utilisateur = sess.Utilisateur
	entree.Domaine = sess.Domaine
	_ = s.journal.Record(entree)
}

func (s *Server) exportCSV(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)

	ou := strings.TrimSpace(r.FormValue("ou"))
	if ou == "" {
		s.erreur(w, Tr(langue, "export.ou_requise"))
		return
	}
	fields := champsExport(r.Form["champs"], []string{"sAMAccountName", "displayName", "mail"})

	sep := ';'
	switch r.FormValue("separateur") {
	case ",":
		sep = ','
	case "tab":
		sep = '\t'
	}

	users, err := chercheUsersExport(r, pool, ou)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	params := ad.ExportParams{
		Format:        ad.FormatCSV,
		Fields:        fields,
		IncludeHeader: r.FormValue("en_tete") != "",
		Separator:     sep,
		Users:         users,
		Title:         "Export EduSync AD",
	}

	data, err := pool.ExportCSV(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.export")+": "+err.Error())
		return
	}
	s.journaliseExport(sess, "export_csv", ou, len(users), len(fields))

	nom := "export_" + time.Now().Format("20060102-1504") + ".csv"
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="`+nom+`"`)
	// BOM UTF-8 : Excel (Windows) ouvre alors les accents correctement.
	_, _ = w.Write([]byte("\xEF\xBB\xBF"))
	_, _ = w.Write(data)
}

func (s *Server) exportPDF(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)

	ou := strings.TrimSpace(r.FormValue("ou"))
	if ou == "" {
		s.erreur(w, Tr(langue, "export.ou_requise"))
		return
	}
	template := ad.LabelTemplate(r.FormValue("template"))
	if template == "" {
		template = ad.LabelL7160
	}
	fields := champsExport(r.Form["champs"], []string{"displayName", "sAMAccountName", "mail", "ou"})

	users, err := chercheUsersExport(r, pool, ou)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	params := ad.ExportParams{
		Format:   ad.FormatPDF,
		Template: template,
		Fields:   fields,
		Users:    users,
		Title:    Tr(langue, "export.pdf_titre"),
	}
	// QR code d'identifiant (optionnelle) : contenu = UPN, sinon SAM.
	if r.FormValue("qrcode") != "" {
		fields = append(fields, ad.FieldQRCode)
		params.Fields = fields
		params.QRCodeData = func(u *ad.User) string {
			if u.UserPrincipalName != "" {
				return u.UserPrincipalName
			}
			return u.SAMAccountName
		}
	}

	data, err := pool.ExportLabelsPDF(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.export")+": "+err.Error())
		return
	}
	s.journaliseExport(sess, "export_etiquettes", ou, len(users), len(fields))

	nom := "etiquettes_" + time.Now().Format("20060102-1504") + ".pdf"
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", `attachment; filename="`+nom+`"`)
	_, _ = w.Write(data)
}

// exportTrombinoscope génère un PDF trombinoscope (grille de photos+noms).
func (s *Server) exportTrombinoscope(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)

	ou := strings.TrimSpace(r.FormValue("ou"))
	if ou == "" {
		s.erreur(w, Tr(langue, "export.ou_requise"))
		return
	}
	titre := strings.TrimSpace(r.FormValue("titre"))
	if titre == "" {
		titre = "Trombinoscope"
	}

	users, err := chercheUsersExport(r, pool, ou)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	params := ad.ExportParams{
		Format:         ad.FormatPDF,
		Fields:         []ad.ExportField{ad.FieldDisplayName, ad.FieldSAMAccountName},
		Users:          users,
		Title:          titre,
		PhotoAttribute: "thumbnailPhoto",
	}

	data, err := pool.ExportTrombinoscope(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.export")+": "+err.Error())
		return
	}
	s.journaliseExport(sess, "export_trombinoscope", ou, len(users), len(params.Fields))

	nom := "trombinoscope_" + time.Now().Format("20060102-1504") + ".pdf"
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", `attachment; filename="`+nom+`"`)
	_, _ = w.Write(data)
}

// ─── Page Audit ────────────────────────────────────────────────────────

func (s *Server) pageAudit(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	d := s.donnees(r, Tr(s.langue(r), "audit.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	if s.journal != nil {
		entries, _ := s.journal.List(audit.Filter{Limit: 50})
		d.Actions = entries
	}
	s.rendre(w, http.StatusOK, "audit.html", d)
}

func (s *Server) auditFilter(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	f := audit.Filter{
		From:       strings.TrimSpace(r.FormValue("date_debut")),
		To:         strings.TrimSpace(r.FormValue("date_fin")),
		ActionType: strings.TrimSpace(r.FormValue("type_action")),
		Resultat:   strings.TrimSpace(r.FormValue("resultat")),
		Operateur:  strings.TrimSpace(r.FormValue("operateur")),
		Recherche:  strings.TrimSpace(r.FormValue("recherche")),
		Limit:      200,
	}

	entries, err := s.journal.List(f)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.journal")+": "+err.Error())
		return
	}

	d := s.donnees(r, Tr(s.langue(r), "audit.titre"))
	d.Actions = entries
	d.LDAPFallback = sess.LDAPFallback
	s.rendreFragment(w, http.StatusOK, "audit_results.html", d)
}

// updaterAPIURLDefault est l'URL par défaut de l'API GitHub releases.
const updaterAPIURLDefault = "https://api.github.com/repos/estemobs/EduSync-AD/releases/latest"

// updaterInfoJSON est la structure de réponse JSON pour /app/updater/check.
type updaterInfoJSON struct {
	Version      string `json:"version"`
	Current      string `json:"current"`
	DownloadURL  string `json:"download_url"`
	ChecksumURL  string `json:"checksum_url"`
	ReleaseNotes string `json:"release_notes,omitempty"`
	AJour        bool   `json:"a_jour"`
}

// pageUpdaterCheck vérifie s'il existe une mise à jour et retourne un JSON.
func (s *Server) pageUpdaterCheck(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	apiURL := s.opts.UpdaterAPIURL
	if apiURL == "" {
		apiURL = updaterAPIURLDefault
	}

	info, err := updater.CheckForUpdate(r.Context(), apiURL, s.opts.Version)
	if err != nil || info == nil {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(updaterInfoJSON{AJour: true})
		return
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(updaterInfoJSON{
		Version:      info.Version,
		Current:      info.Current,
		DownloadURL:  info.DownloadURL,
		ChecksumURL:  info.ChecksumURL,
		ReleaseNotes: info.ReleaseNotes,
		AJour:        false,
	})
}

// pageUpdaterInstall télécharge et prépare l'installation de la mise à jour.
// finalize n'est pas exécuté ici : l'appelant (UI) doit confirmer explicitement
// avant d'appeler /app/updater/finalize — voir la note de sécurité dans
// internal/updater (le lancement est différé après confirmation utilisateur).
func (s *Server) pageUpdaterInstall(w http.ResponseWriter, r *http.Request, sess *session, pool *ad.Pool) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	downloadURL := strings.TrimSpace(r.FormValue("download_url"))
	checksumURL := strings.TrimSpace(r.FormValue("checksum_url"))
	if downloadURL == "" {
		s.erreur(w, Tr(s.langue(r), "erreur.url_manquante"))
		return
	}

	ok, finalize, err := updater.DownloadAndInstall(r.Context(), downloadURL, checksumURL)
	if err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.telechargement")+": "+err.Error())
		return
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if !ok {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"erreur": Tr(s.langue(r), "updater.echec_preparation")})
		return
	}

	// Le clic sur "Installer" constitue la confirmation explicite de
	// l'utilisateur : on lance la finalisation (installateur Windows /
	// remplacement + relance Linux).
	if finalize != nil {
		go func() {
			_ = finalize()
		}()
	}

	_ = json.NewEncoder(w).Encode(map[string]string{"ok": Tr(s.langue(r), "updater.installation_lancee")})
}

// ─── Progression / annulation (A12) ───────────────────────────────────

func (s *Server) pageProgress(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "progress.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "progress.html", d)
}

func (s *Server) progressList(w http.ResponseWriter, r *http.Request, sess *session) {
	liste := s.progress.Liste()
	if liste == nil {
		liste = []*progress.Operation{}
	}
	d := s.donnees(r, "")
	d.Operations = liste
	s.rendreFragment(w, http.StatusOK, "progress_list.html", d)
}

func (s *Server) progressCancel(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	id := strings.TrimSpace(r.FormValue("id"))
	if id == "" {
		s.erreur(w, Tr(s.langue(r), "erreur.id_manquant"))
		return
	}

	if s.progress.Annuler(id) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(map[string]string{"ok": "annule"})
		return
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusNotFound)
	_ = json.NewEncoder(w).Encode(map[string]string{"erreur": Tr(s.langue(r), "progress.introuvable")})
}

// ─── Profils utilisateurs (M13) ──────────────────────────────────────

func (s *Server) pageProfils(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "profils.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "profils.html", d)
}

func (s *Server) profilsSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.ProfileType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))

	params := ad.ProfileParams{
		UserDN: userDN,
		Type:   typ,
		Chemin: chemin,
	}

	// Récupérer le pool AD (peut être nil en dehors d'une session AD valide).
	pool, err := s.getPool(sess)
	if err != nil {
		// Pas de connexion AD : retourner un résultat en attente.
		s.profilsResultatFragment(w, langue, &ad.ProfileResult{
			UserDN:        params.UserDN,
			Type:          params.Type,
			CheminResolve: params.Chemin,
			Statut:        "attente_ad",
			Detaille:      "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.ProfileSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.profilsResultatFragment(w, langue, result)
}

func (s *Server) profilsExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.ProfileType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))

	params := ad.ProfileParams{
		UserDN: userDN,
		Type:   typ,
		Chemin: chemin,
	}

	// Récupérer le pool AD (peut être nil en dehors d'une session AD valide).
	pool, err := s.getPool(sess)
	if err != nil {
		s.profilsResultatFragment(w, langue, &ad.ProfileResult{
			UserDN:        params.UserDN,
			Type:          params.Type,
			CheminResolve: params.Chemin,
			Statut:        "attente_ad",
			Detaille:      "connexion AD requise pour appliquer le profil",
		})
		return
	}

	result, err := pool.ProfileExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Journaliser l'application du profil
	if s.journal != nil {
		entree := nouvelleEntree("profil_utilisateur", result.SAMAccountName, result.Statut,
			fmt.Sprintf("Type: %s, chemin: %s", params.Type, result.CheminResolve))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.profilsResultatFragment(w, langue, result)
}

// profilsResultatFragment rend le fragment HTML du résultat d'un profil.
func (s *Server) profilsResultatFragment(w http.ResponseWriter, langue string, result *ad.ProfileResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "profils.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "profils.utilisateur"), result.SAMAccountName))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "profils.type"), result.Type))
	if result.CheminResolve != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "profils.chemin"), result.CheminResolve))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "profils.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "profils.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Heures de connexion (M16) ──────────────────────────────────────

func (s *Server) pageLogonHours(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "logonhours.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "logon_hours.html", d)
}

func (s *Server) logonHoursSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	preset := strings.TrimSpace(r.FormValue("preset"))

	var heures *ad.LogonHours
	switch preset {
	case "cours":
		h := ad.HeuresCours()
		heures = &h
	case "admin":
		h := ad.HeuresAdmin()
		heures = &h
	case "personnalise":
		// Construire la grille depuis le formulaire
		h := ad.LogonHours{}
		for jour := 0; jour < 7; jour++ {
			for heure := 0; heure < 24; heure++ {
				cle := fmt.Sprintf("h_%d_%d", jour, heure)
				h.Jours[jour][heure] = r.FormValue(cle) == "1"
			}
		}
		heures = &h
	case "":
		// Pas de preset : rien à appliquer
	default:
		s.erreur(w, Tr(langue, "logonhours.preset_invalide"))
		return
	}

	params := ad.LogonHoursParams{
		UserDN: userDN,
		Heures: heures,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.logonHoursResultatFragment(w, langue, &ad.LogonHoursResult{
			UserDN:   params.UserDN,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.LogonHoursSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.logonHoursResultatFragment(w, langue, result)
}

func (s *Server) logonHoursExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	preset := strings.TrimSpace(r.FormValue("preset"))

	var heures *ad.LogonHours
	switch preset {
	case "cours":
		h := ad.HeuresCours()
		heures = &h
	case "admin":
		h := ad.HeuresAdmin()
		heures = &h
	case "personnalise":
		h := ad.LogonHours{}
		for jour := 0; jour < 7; jour++ {
			for heure := 0; heure < 24; heure++ {
				cle := fmt.Sprintf("h_%d_%d", jour, heure)
				h.Jours[jour][heure] = r.FormValue(cle) == "1"
			}
		}
		heures = &h
	case "":
	default:
		s.erreur(w, Tr(langue, "logonhours.preset_invalide"))
		return
	}

	params := ad.LogonHoursParams{
		UserDN: userDN,
		Heures: heures,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.logonHoursResultatFragment(w, langue, &ad.LogonHoursResult{
			UserDN:   params.UserDN,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour appliquer les heures",
		})
		return
	}

	result, err := pool.LogonHoursExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	// Journaliser
	if s.journal != nil {
		entree := nouvelleEntree("logon_hours", result.UserDN, result.Statut,
			fmt.Sprintf("heures de connexion appliquées"))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.logonHoursResultatFragment(w, langue, result)
}

// logonHoursResultatFragment rend le fragment HTML du résultat.
func (s *Server) logonHoursResultatFragment(w http.ResponseWriter, langue string, result *ad.LogonHoursResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "logonhours.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "logonhours.utilisateur"), result.UserDN))
	if result.Heures != nil {
		nbAutorises := 0
		for j := 0; j < 7; j++ {
			for hh := 0; hh < 24; hh++ {
				if result.Heures.EstAutorise(j, hh) {
					nbAutorises++
				}
			}
		}
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d heures autorisées</p>`, Tr(langue, "logonhours.heures_autorisees"), nbAutorises))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "logonhours.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "logonhours.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Scripts logon/logoff (M15) ──────────────────────────────────────

func (s *Server) pageLogonScripts(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "logonscripts.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "logon_scripts.html", d)
}

func (s *Server) logonScriptsSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.ScriptType(strings.TrimSpace(r.FormValue("type")))
	contenu := strings.TrimSpace(r.FormValue("contenu"))

	params := ad.ScriptParams{
		UserDN:  userDN,
		Type:    typ,
		Contenu: contenu,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.logonScriptsResultatFragment(w, langue, &ad.ScriptResult{
			UserDN:   params.UserDN,
			Type:     params.Type,
			Contenu:  params.Contenu,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.ScriptSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.logonScriptsResultatFragment(w, langue, result)
}

func (s *Server) logonScriptsExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.ScriptType(strings.TrimSpace(r.FormValue("type")))
	contenu := strings.TrimSpace(r.FormValue("contenu"))

	params := ad.ScriptParams{
		UserDN:  userDN,
		Type:    typ,
		Contenu: contenu,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.logonScriptsResultatFragment(w, langue, &ad.ScriptResult{
			UserDN:   params.UserDN,
			Type:     params.Type,
			Contenu:  params.Contenu,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour appliquer le script",
		})
		return
	}

	result, err := pool.ScriptExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	if s.journal != nil {
		entree := nouvelleEntree("script_logon", result.UserDN, result.Statut,
			fmt.Sprintf("Type: %s", params.Type))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.logonScriptsResultatFragment(w, langue, result)
}

// logonScriptsResultatFragment rend le fragment HTML du résultat.
func (s *Server) logonScriptsResultatFragment(w http.ResponseWriter, langue string, result *ad.ScriptResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "logonscripts.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "logonscripts.utilisateur"), result.UserDN))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "logonscripts.type"), result.Type))
	if result.Contenu != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "logonscripts.contenu"), result.Contenu))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "logonscripts.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "logonscripts.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Quotas de disque (M14) ──────────────────────────────────────

func (s *Server) pageQuotas(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "quotas.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "quotas.html", d)
}

func (s *Server) quotasSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	ou := strings.TrimSpace(r.FormValue("ou"))
	typ := ad.QuotaType(strings.TrimSpace(r.FormValue("type")))
	limite := int64(0)
	fmt.Sscanf(strings.TrimSpace(r.FormValue("limite")), "%d", &limite)
	seuil := ad.QuotaSeuil(strings.TrimSpace(r.FormValue("seuil")))

	params := ad.QuotaParams{
		UserDN: userDN,
		OU:     ou,
		Type:   typ,
		Limite: limite,
		Seuil:  seuil,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.quotasResultatFragment(w, langue, &ad.QuotaResult{
			UserDN:   params.UserDN,
			OU:       params.OU,
			Type:     params.Type,
			Limite:   params.Limite,
			Seuil:    params.Seuil,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.QuotaSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.quotasResultatFragment(w, langue, result)
}

func (s *Server) quotasExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	ou := strings.TrimSpace(r.FormValue("ou"))
	typ := ad.QuotaType(strings.TrimSpace(r.FormValue("type")))
	limite := int64(0)
	fmt.Sscanf(strings.TrimSpace(r.FormValue("limite")), "%d", &limite)
	seuil := ad.QuotaSeuil(strings.TrimSpace(r.FormValue("seuil")))

	params := ad.QuotaParams{
		UserDN: userDN,
		OU:     ou,
		Type:   typ,
		Limite: limite,
		Seuil:  seuil,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.quotasResultatFragment(w, langue, &ad.QuotaResult{
			UserDN:   params.UserDN,
			OU:       params.OU,
			Type:     params.Type,
			Limite:   params.Limite,
			Seuil:    params.Seuil,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour appliquer le quota",
		})
		return
	}

	result, err := pool.QuotaExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	if s.journal != nil {
		entree := nouvelleEntree("quota", result.UserDN, result.Statut,
			fmt.Sprintf("Type: %s, limite: %s, OU: %s", params.Type, ad.FormateTaille(params.Limite), params.OU))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.quotasResultatFragment(w, langue, result)
}

// quotasResultatFragment rend le fragment HTML du résultat.
func (s *Server) quotasResultatFragment(w http.ResponseWriter, langue string, result *ad.QuotaResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "quotas.resultat") + `</h2>`)
	if result.UserDN != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.utilisateur"), result.UserDN))
	}
	if result.OU != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.ou"), result.OU))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.type"), result.Type))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.limite"), ad.FormateTaille(result.Limite)))
	if result.Seuil != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.seuil"), result.Seuil))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "quotas.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Dossiers personnels (M17) ──────────────────────────────────────

func (s *Server) pageHomeDirs(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "homedirs.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "homedirs.html", d)
}

func (s *Server) homeDirsSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.HomeDirType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))
	LettreLecteur := strings.TrimSpace(r.FormValue("lettre_lecteur"))

	params := ad.HomeDirParams{
		UserDN:        userDN,
		Type:          typ,
		Chemin:        chemin,
		LettreLecteur: LettreLecteur,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.homeDirsResultatFragment(w, langue, &ad.HomeDirResult{
			UserDN:        params.UserDN,
			Type:          params.Type,
			Chemin:        params.Chemin,
			LettreLecteur: params.LettreLecteur,
			Statut:        "attente_ad",
			Detaille:      "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.HomeDirSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.homeDirsResultatFragment(w, langue, result)
}

func (s *Server) homeDirsExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.HomeDirType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))
	LettreLecteur := strings.TrimSpace(r.FormValue("lettre_lecteur"))

	params := ad.HomeDirParams{
		UserDN:        userDN,
		Type:          typ,
		Chemin:        chemin,
		LettreLecteur: LettreLecteur,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.homeDirsResultatFragment(w, langue, &ad.HomeDirResult{
			UserDN:        params.UserDN,
			Type:          params.Type,
			Chemin:        params.Chemin,
			LettreLecteur: params.LettreLecteur,
			Statut:        "attente_ad",
			Detaille:      "connexion AD requise pour appliquer le dossier personnel",
		})
		return
	}

	result, err := pool.HomeDirExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	if s.journal != nil {
		entree := nouvelleEntree("home_dir", result.UserDN, result.Statut,
			fmt.Sprintf("Type: %s, chemin: %s, lecteur: %s", params.Type, params.Chemin, params.LettreLecteur))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.homeDirsResultatFragment(w, langue, result)
}

// homeDirsResultatFragment rend le fragment HTML du résultat.
func (s *Server) homeDirsResultatFragment(w http.ResponseWriter, langue string, result *ad.HomeDirResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "homedirs.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.utilisateur"), result.UserDN))
	if result.SAMAccountName != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.identifiant"), result.SAMAccountName))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.type"), result.Type))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.chemin"), result.Chemin))
	if result.LettreLecteur != "" {
		b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.lettre_lecteur"), result.LettreLecteur))
	}
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "homedirs.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Photos d'identite (M12) ──────────────────────────────────────

func (s *Server) pagePhotos(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "photos.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	s.rendre(w, http.StatusOK, "photos.html", d)
}

func (s *Server) photosSimulate(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.PhotoType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))

	params := ad.PhotoParams{
		UserDN: userDN,
		Type:   typ,
		Chemin: chemin,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.photosResultatFragment(w, langue, &ad.PhotoResult{
			UserDN:   params.UserDN,
			Type:     params.Type,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour vérifier l'existence du compte",
		})
		return
	}

	result, err := pool.PhotoSimulate(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	s.photosResultatFragment(w, langue, result)
}

func (s *Server) photosExecute(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	userDN := strings.TrimSpace(r.FormValue("user_dn"))
	typ := ad.PhotoType(strings.TrimSpace(r.FormValue("type")))
	chemin := strings.TrimSpace(r.FormValue("chemin"))

	params := ad.PhotoParams{
		UserDN: userDN,
		Type:   typ,
		Chemin: chemin,
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.photosResultatFragment(w, langue, &ad.PhotoResult{
			UserDN:   params.UserDN,
			Type:     params.Type,
			Statut:   "attente_ad",
			Detaille: "connexion AD requise pour appliquer la photo",
		})
		return
	}

	result, err := pool.PhotoExecute(r.Context(), params)
	if err != nil {
		s.erreur(w, Tr(langue, "erreur.connexion_ad")+": "+err.Error())
		return
	}

	if s.journal != nil {
		entree := nouvelleEntree("photo", result.UserDN, result.Statut,
			fmt.Sprintf("Type: %s, chemin: %s, taille: %s", params.Type, params.Chemin, ad.FormateTaille(params.Taille)))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}

	s.photosResultatFragment(w, langue, result)
}

// photosResultatFragment rend le fragment HTML du résultat.
func (s *Server) photosResultatFragment(w http.ResponseWriter, langue string, result *ad.PhotoResult) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "photos.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.utilisateur"), result.UserDN))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.type_q"), result.Type))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.chemin_q"), result.Chemin))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.taille_q"), ad.FormateTaille(result.Taille)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.statut"), result.Statut))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "photos.detail"), result.Detaille))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Espaces partagés par classe (M18) ────────────────────────────────────

// dossierClassSpaces retourne le dossier de configuration des espaces : à côté
// de config.json (cohérent en test et avec Python), sinon dossier utilisateur.
func (s *Server) dossierClassSpaces() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := classspaces.DossierConfig(); err == nil && dir != "" {
		return dir
	}
	return "."
}

func (s *Server) pageClassSpaces(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "classspaces.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	d.ConfigClassSpaces = classspaces.ChargerConfig(s.dossierClassSpaces())
	s.rendre(w, http.StatusOK, "class_spaces.html", d)
}

// classSpacesConfig enregistre la configuration des espaces de classe.
func (s *Server) classSpacesConfig(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}

	langue := s.langue(r)
	cfg := classspaces.Config{
		PartageRacine:   strings.TrimSpace(r.FormValue("share_root")),
		ModeleDossier:   strings.TrimSpace(r.FormValue("modele_dossier")),
		GroupeProfs:     strings.TrimSpace(r.FormValue("teachers_group")),
		OUProfs:         strings.TrimSpace(r.FormValue("teachers_ou")),
		AutoSurCreation: r.FormValue("auto_creation") != "",
	}
	if erreurs := cfg.Validate(); len(erreurs) > 0 {
		s.classSpacesMessage(w, langue, strings.Join(erreurs, " "), true)
		return
	}
	if err := classspaces.EnregistrerConfig(s.dossierClassSpaces(), cfg); err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("class_spaces_config", cfg.PartageRacine, "succes",
			fmt.Sprintf("Modèle: %s, profs: %s", cfg.ModeleDossier, cfg.GroupeProfs))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.classSpacesMessage(w, langue, Tr(langue, "classspaces.config_enregistree"), false)
}

// planClassSpaces complète un plan (membres classés profs/élèves) via l'annuaire.
func (s *Server) planClassSpaces(r *http.Request, pool *ad.Pool, cfg classspaces.Config, plan classspaces.Plan) classspaces.Plan {
	g, err := pool.GroupGet(r.Context(), plan.GroupDN)
	if err != nil {
		return plan
	}
	for _, membreDN := range g.Members {
		u, err := pool.UserGet(r.Context(), membreDN)
		if err != nil {
			continue
		}
		if classspaces.ClasserMembre(u.DN, u.MemberOf, cfg) == classspaces.RoleProf {
			plan.Profs = append(plan.Profs, u.SAMAccountName)
		} else {
			plan.Eleves = append(plan.Eleves, u.SAMAccountName)
		}
	}
	return plan
}

// classSpacesPlan construit et affiche le plan d'un espace de classe.
func (s *Server) classSpacesPlan(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	groupDN := strings.TrimSpace(r.FormValue("group_dn"))
	groupName := strings.TrimSpace(r.FormValue("group_name"))
	if groupDN == "" || groupName == "" {
		s.classSpacesMessage(w, langue, Tr(langue, "classspaces.champs_obligatoires"), true)
		return
	}

	cfg := classspaces.ChargerConfig(s.dossierClassSpaces())
	plan := classspaces.Plan{GroupDN: groupDN, GroupName: groupName, Path: cfg.ResolvePath(groupName)}

	pool, err := s.getPool(sess)
	if err != nil {
		s.classSpacesPlanFragment(w, langue, plan, "attente_ad",
			"connexion AD requise pour lister et classer les membres")
		return
	}

	plan = s.planClassSpaces(r, pool, cfg, plan)
	entree := classspaces.ChargerEtat(s.dossierClassSpaces())[groupDN]
	diff := classspaces.PlanifierSync(entree.Members, plan.Membres())
	detail := fmt.Sprintf("%d membre(s) : %d prof(s), %d élève(s) — +%d / -%d depuis l'état enregistré",
		len(plan.Membres()), len(plan.Profs), len(plan.Eleves), len(diff.Ajoutes), len(diff.Retires))
	s.classSpacesPlanFragment(w, langue, plan, "succes", detail)
}

// classSpacesMarkSynced enregistre l'appartenance AD courante comme état de
// référence synchronisé pour un espace de classe.
func (s *Server) classSpacesMarkSynced(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	groupDN := strings.TrimSpace(r.FormValue("group_dn"))
	groupName := strings.TrimSpace(r.FormValue("group_name"))
	if groupDN == "" || groupName == "" {
		s.classSpacesMessage(w, langue, Tr(langue, "classspaces.champs_obligatoires"), true)
		return
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.classSpacesMessage(w, langue, "connexion AD requise pour enregistrer les membres", true)
		return
	}

	cfg := classspaces.ChargerConfig(s.dossierClassSpaces())
	plan := s.planClassSpaces(r, pool, cfg, classspaces.Plan{
		GroupDN: groupDN, GroupName: groupName, Path: cfg.ResolvePath(groupName),
	})
	if err := classspaces.MarquerSynchronise(s.dossierClassSpaces(), groupDN, plan.Membres()); err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.auditClassSpaces(sess, "class_spaces_marquer_sync", plan)
	s.classSpacesMessage(w, langue,
		fmt.Sprintf("%s (%d membre(s))", Tr(langue, "classspaces.marque_sync"), len(plan.Membres())), false)
}

// classSpacesCreationScript génère le script de création (dossier + partage + droits).
func (s *Server) classSpacesCreationScript(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	groupDN := strings.TrimSpace(r.FormValue("group_dn"))
	groupName := strings.TrimSpace(r.FormValue("group_name"))
	if groupName == "" {
		s.classSpacesMessage(w, langue, Tr(langue, "classspaces.group_name_obligatoire"), true)
		return
	}

	cfg := classspaces.ChargerConfig(s.dossierClassSpaces())
	plan := classspaces.Plan{GroupDN: groupDN, GroupName: groupName, Path: cfg.ResolvePath(groupName)}
	// Les membres ne sont chargés que si le groupe est identifié (DN fourni).
	if groupDN != "" {
		if pool, err := s.getPool(sess); err == nil {
			plan = s.planClassSpaces(r, pool, cfg, plan)
		}
	}

	script := classspaces.ScriptCreation([]classspaces.Plan{plan}, sess.Domaine)
	s.classSpacesScriptFragment(w, langue, plan, script)
	s.auditClassSpaces(sess, "class_spaces_creation_script", plan)
}

// classSpacesSyncScript génère le script de synchronisation membres AD ↔ droits.
func (s *Server) classSpacesSyncScript(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	groupDN := strings.TrimSpace(r.FormValue("group_dn"))
	groupName := strings.TrimSpace(r.FormValue("group_name"))
	chemin := strings.TrimSpace(r.FormValue("chemin"))
	if groupDN == "" || groupName == "" {
		s.classSpacesMessage(w, langue, Tr(langue, "classspaces.champs_obligatoires"), true)
		return
	}

	cfg := classspaces.ChargerConfig(s.dossierClassSpaces())
	if chemin == "" {
		chemin = cfg.ResolvePath(groupName)
	}
	plan := classspaces.Plan{GroupDN: groupDN, GroupName: groupName, Path: chemin}

	script := classspaces.ScriptSync(plan, sess.Domaine, cfg.GroupeProfs, cfg.OUProfs)
	s.classSpacesScriptFragment(w, langue, plan, script)
	s.auditClassSpaces(sess, "class_spaces_sync_script", plan)
}

// auditClassSpaces journalise la génération d'un script d'espace de classe.
func (s *Server) auditClassSpaces(sess *session, action string, plan classspaces.Plan) {
	if s.journal == nil {
		return
	}
	entree := nouvelleEntree(action, plan.Path, "succes",
		fmt.Sprintf("Groupe: %s, profs: %d, élèves: %d", plan.GroupName, len(plan.Profs), len(plan.Eleves)))
	entree.Utilisateur = sess.Utilisateur
	entree.Domaine = sess.Domaine
	_ = s.journal.Record(entree)
}

// classSpacesMessage rend un message simple (succès ou erreur).
func (s *Server) classSpacesMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// classSpacesPlanFragment rend le fragment HTML du plan d'un espace.
func (s *Server) classSpacesPlanFragment(w http.ResponseWriter, langue string, plan classspaces.Plan, statut, detail string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "classspaces.resultat") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "classspaces.chemin_espace"), html.EscapeString(plan.Path)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "classspaces.partage_nom"), html.EscapeString(plan.ShareName())))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "classspaces.profs"), len(plan.Profs)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "classspaces.eleves"), len(plan.Eleves)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm font-medium"><strong>%s:</strong> %s</p>`, Tr(langue, "classspaces.statut"), html.EscapeString(statut)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500"><strong>%s:</strong> %s</p>`, Tr(langue, "classspaces.detail"), html.EscapeString(detail)))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// classSpacesScriptFragment rend le script PowerShell généré.
func (s *Server) classSpacesScriptFragment(w http.ResponseWriter, langue string, plan classspaces.Plan, script string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "classspaces.script") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm text-slate-500">%s : %s</p>`, Tr(langue, "classspaces.chemin_espace"), html.EscapeString(plan.Path)))
	b.WriteString(`<pre class="mt-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">`)
	b.WriteString(html.EscapeString(script))
	b.WriteString(`</pre></div>`)
	w.Write([]byte(b.String()))
}

// ─── Microsoft Exchange (M20) ─────────────────────────────────────────────

// dossierExchange retourne le dossier de configuration Exchange.
func (s *Server) dossierExchange() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := exchange.DossierConfig(); err == nil && dir != "" {
		return dir
	}
	return "."
}

func (s *Server) pageExchange(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "exchange.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	d.ConfigExchange = exchange.ChargerConfig(filepath.Join(s.dossierExchange(), exchange.FichierConfig))
	s.rendre(w, http.StatusOK, "exchange.html", d)
}

// exchangeConfigDepuisForm construit la configuration Exchange depuis le formulaire.
func exchangeConfigDepuisForm(r *http.Request) exchange.Config {
	def := exchange.ConfigParDefaut()
	cfg := def
	cfg.Mode = valeurFormulaireOu(r.FormValue("mode"), def.Mode)
	cfg.SMTPDomain = strings.TrimSpace(r.FormValue("smtp_domain"))
	cfg.AliasPattern = valeurFormulaireOu(r.FormValue("alias_pattern"), def.AliasPattern)
	cfg.MailboxQuota = valeurFormulaireOu(r.FormValue("mailbox_quota"), def.MailboxQuota)
	cfg.ArchiveEnabled = r.FormValue("archive_enabled") != ""
	cfg.RetentionPolicy = strings.TrimSpace(r.FormValue("retention_policy"))
	cfg.ExchangeServer = strings.TrimSpace(r.FormValue("exchange_server"))
	cfg.Database = strings.TrimSpace(r.FormValue("database"))
	cfg.TenantID = strings.TrimSpace(r.FormValue("tenant_id"))
	cfg.ClientID = strings.TrimSpace(r.FormValue("client_id"))
	cfg.CertificateThumbprint = strings.TrimSpace(r.FormValue("certificate_thumbprint"))
	cfg.Organization = strings.TrimSpace(r.FormValue("organization"))
	return cfg
}

// valeurFormulaireOu retourne la valeur du formulaire, ou le défaut si vide
// (les champs à choix contraint — mode, motif d'alias, quota — n'ont pas de
// valeur vide valide).
func valeurFormulaireOu(valeur, defaut string) string {
	if strings.TrimSpace(valeur) == "" {
		return defaut
	}
	return strings.TrimSpace(valeur)
}

// exchangeConfigForm enregistre la configuration Exchange.
func (s *Server) exchangeConfigForm(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := exchangeConfigDepuisForm(r)
	if err := exchange.SauvegarderConfig(filepath.Join(s.dossierExchange(), exchange.FichierConfig), cfg); err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("exchange_config", cfg.SMTPDomain, "succes",
			fmt.Sprintf("Mode: %s, quota: %s", cfg.Mode, cfg.MailboxQuota))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.exchangeMessage(w, langue, Tr(langue, "exchange.config_enregistree"), false)
}

// lireComptesFormulaire lit la liste des comptes saisie (une ligne par compte,
// champs séparés par « ; » : sam ; cn ; dn).
func lireComptesFormulaire(r *http.Request) []exchange.UtilisateurBal {
	var comptes []exchange.UtilisateurBal
	for _, ligne := range strings.Split(r.FormValue("comptes"), "\n") {
		ligne = strings.TrimSpace(ligne)
		if ligne == "" {
			continue
		}
		champs := strings.Split(ligne, ";")
		get := func(i int) string {
			if i < len(champs) {
				return strings.TrimSpace(champs[i])
			}
			return ""
		}
		compte := exchange.UtilisateurBal{SAM: get(0), CN: get(1), DN: get(2)}
		if compte.SAM == "" && compte.CN == "" {
			continue
		}
		comptes = append(comptes, compte)
	}
	return comptes
}

// exchangePlansDepuisForm construit les plans depuis le formulaire.
func (s *Server) exchangePlansDepuisForm(r *http.Request, cfg exchange.Config) []exchange.PlanBal {
	return exchange.ConstruirePlansBals(lireComptesFormulaire(r), cfg, strings.TrimSpace(r.FormValue("domain")))
}

func (s *Server) exchangePlans(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := exchange.ChargerConfig(filepath.Join(s.dossierExchange(), exchange.FichierConfig))
	s.exchangePlansFragment(w, langue, s.exchangePlansDepuisForm(r, cfg))
}

func (s *Server) exchangeOnpremScript(w http.ResponseWriter, r *http.Request, _ *session) {
	s.exchangeScript(w, r, "onprem")
}

func (s *Server) exchangeOnlineScript(w http.ResponseWriter, r *http.Request, _ *session) {
	s.exchangeScript(w, r, "online")
}

// exchangeScript génère un script de boîtes (sur site ou en ligne).
func (s *Server) exchangeScript(w http.ResponseWriter, r *http.Request, mode string) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := exchange.ChargerConfig(filepath.Join(s.dossierExchange(), exchange.FichierConfig))
	cfg.Mode = mode
	plans := s.exchangePlansDepuisForm(r, cfg)
	var script string
	if mode == "online" {
		script = exchange.ScriptEnLigne(plans, cfg)
	} else {
		script = exchange.ScriptSurSite(plans, cfg)
	}
	s.exchangeScriptFragment(w, langue, script)
}

func (s *Server) exchangePolicyScript(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := exchange.ChargerConfig(filepath.Join(s.dossierExchange(), exchange.FichierConfig))
	s.exchangeScriptFragment(w, langue, exchange.ScriptPolitiqueAdresses(cfg))
}

// exchangeMessage rend un message simple (succès ou erreur).
func (s *Server) exchangeMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// exchangePlansFragment rend le tableau des plans de boîtes.
func (s *Server) exchangePlansFragment(w http.ResponseWriter, langue string, plans []exchange.PlanBal) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(fmt.Sprintf(`<p class="font-semibold">%d %s</p>`, len(plans), Tr(langue, "exchange.plans")))
	if len(plans) == 0 {
		b.WriteString(`<p class="text-slate-500">` + Tr(langue, "exchange.aucun") + `</p></div>`)
		w.Write([]byte(b.String()))
		return
	}
	b.WriteString(`<table class="mt-2 w-full text-xs"><thead><tr class="text-left text-slate-500">`)
	b.WriteString(`<th class="py-1 pr-3">` + Tr(langue, "exchange.col_identifiant") + `</th>`)
	b.WriteString(`<th class="py-1 pr-3">` + Tr(langue, "exchange.col_principal") + `</th>`)
	b.WriteString(`<th class="py-1 pr-3">` + Tr(langue, "exchange.col_alias") + `</th>`)
	b.WriteString(`<th class="py-1">` + Tr(langue, "exchange.col_upn") + `</th>`)
	b.WriteString(`</tr></thead><tbody>`)
	for _, plan := range plans {
		identifiant := plan.SAM
		if identifiant == "" {
			identifiant = plan.CN
		}
		b.WriteString(fmt.Sprintf(`<tr class="border-t border-slate-100 dark:border-slate-800"><td class="py-1 pr-3 font-mono">%s</td><td class="py-1 pr-3 font-mono">%s</td><td class="py-1 pr-3 font-mono">%s</td><td class="py-1 font-mono">%s</td></tr>`,
			html.EscapeString(identifiant), html.EscapeString(plan.Primaire),
			html.EscapeString(strings.Join(plan.Alias, ", ")), html.EscapeString(plan.UPN)))
	}
	b.WriteString(`</tbody></table></div>`)
	w.Write([]byte(b.String()))
}

// exchangeScriptFragment rend un script PowerShell généré.
func (s *Server) exchangeScriptFragment(w http.ResponseWriter, langue, script string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "exchange.script") + `</h2>`)
	b.WriteString(`<pre class="mt-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">`)
	b.WriteString(html.EscapeString(script))
	b.WriteString(`</pre></div>`)
	w.Write([]byte(b.String()))
}

// ─── RBAC — délégation d'administration (M26) ────────────────────────────

// RoleRBACUI expose un rôle et ses permissions au gabarit.
type RoleRBACUI struct {
	Role        string
	Label       string
	Scoped      bool
	Permissions []string
}

// dossierConfig retourne le dossier des fichiers partagés (config.json,
// delegations.json, label_templates.json…) : à côté de config.json en test,
// sinon dossier de configuration utilisateur (partagé avec Python).
func (s *Server) dossierConfig() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := config.ConfigDir(); err == nil && dir != "" {
		return dir
	}
	return "."
}

// cheminDelegations retourne le chemin de delegations.json.
func (s *Server) cheminDelegations() string {
	return filepath.Join(s.dossierConfig(), rbac.NomFichier)
}

func (s *Server) pageRBAC(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "rbac.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	if grants, err := rbac.ChargerGrants(s.cheminDelegations()); err == nil {
		d.Grants = grants
	}
	for _, role := range rbac.RoleOrder {
		scoped := false
		for _, s := range rbac.ScopedRoles {
			if s == role {
				scoped = true
			}
		}
		perms := make([]string, 0, len(rbac.RolePermissions[role]))
		for permission := range rbac.RolePermissions[role] {
			perms = append(perms, permission)
		}
		d.RolesRBAC = append(d.RolesRBAC, RoleRBACUI{
			Role: string(role), Label: rbac.RoleLabel(role), Scoped: scoped, Permissions: perms,
		})
	}
	s.rendre(w, http.StatusOK, "rbac.html", d)
}

// rbacAjouter enregistre un nouveau délégué.
func (s *Server) rbacAjouter(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	grants, err := rbac.ChargerGrants(s.cheminDelegations())
	if err != nil {
		s.rbacMessage(w, langue, err.Error(), true)
		return
	}
	grant := rbac.Grant{
		ID:        rbac.UniqueGrantID(grants),
		Operateur: strings.TrimSpace(r.FormValue("operateur")),
		Role:      rbac.Role(strings.TrimSpace(r.FormValue("role"))),
		Site:      strings.TrimSpace(r.FormValue("site")),
		OUs:       lignesSimples(r.FormValue("ous")),
		Groupes:   lignesSimples(r.FormValue("groupes")),
		Actif:     r.FormValue("actif") != "",
	}
	if errs := grant.Validate(); len(errs) > 0 {
		s.rbacMessage(w, langue, strings.Join(errs, " "), true)
		return
	}
	grants = append(grants, grant)
	if err := rbac.SauvegarderGrants(grants, s.cheminDelegations()); err != nil {
		s.rbacMessage(w, langue, err.Error(), true)
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("rbac_ajout", grant.Operateur, "succes",
			fmt.Sprintf("Rôle %s", grant.Role))
		_ = s.journal.Record(entree)
	}
	s.rbacMessage(w, langue, fmt.Sprintf(Tr(langue, "rbac.ajoute"), grant.Operateur), false)
}

// rbacSupprimer retire un délégué.
func (s *Server) rbacSupprimer(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	id := strings.TrimSpace(r.FormValue("id"))
	if id == "" {
		s.rbacMessage(w, langue, Tr(langue, "rbac.id_obligatoire"), true)
		return
	}
	grants, err := rbac.ChargerGrants(s.cheminDelegations())
	if err != nil {
		s.rbacMessage(w, langue, err.Error(), true)
		return
	}
	restants := make([]rbac.Grant, 0, len(grants))
	supprime := false
	for _, grant := range grants {
		if grant.ID == id {
			supprime = true
			continue
		}
		restants = append(restants, grant)
	}
	if !supprime {
		s.rbacMessage(w, langue, Tr(langue, "rbac.introuvable"), true)
		return
	}
	if err := rbac.SauvegarderGrants(restants, s.cheminDelegations()); err != nil {
		s.rbacMessage(w, langue, err.Error(), true)
		return
	}
	s.rbacMessage(w, langue, Tr(langue, "rbac.supprime"), false)
}

// rbacMessage rend un message simple (succès ou erreur).
func (s *Server) rbacMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// lignesSimples découpe une zone de texte en lignes non vides (une DN par
// ligne), sans découpage supplémentaire.
func lignesSimples(valeur string) []string {
	var lignes []string
	for _, ligne := range strings.Split(valeur, "\n") {
		if ligne = strings.TrimSpace(ligne); ligne != "" {
			lignes = append(lignes, ligne)
		}
	}
	return lignes
}

// ─── Étiquettes avancées (M24) ───────────────────────────────────────────

// cheminModelesLabel retourne le chemin de label_templates.json.
func (s *Server) cheminModelesLabel() string {
	return filepath.Join(s.dossierConfig(), label.NomFichier)
}

// cheminMailLabel retourne le chemin de label_mail.json.
func (s *Server) cheminMailLabel() string {
	return filepath.Join(s.dossierConfig(), mailer.NomFichier)
}

// cheminCleLabel retourne le chemin de la clé locale (secret.key).
func (s *Server) cheminCleLabel() string {
	return filepath.Join(s.dossierConfig(), mailer.NomFichierCle)
}

// trouverModeleLabel retourne un modèle d'étiquette par identifiant.
func trouverModeleLabel(modeles []label.ModeleLabel, id string) (label.ModeleLabel, bool) {
	for _, m := range modeles {
		if m.ID == id {
			return m, true
		}
	}
	return label.ModeleLabel{}, false
}

// lignesDemonstration construit des comptes fictifs pour l'aperçu d'un modèle.
func lignesDemonstration(nombre int) []map[string]any {
	lignes := make([]map[string]any, 0, nombre)
	for i := 1; i <= nombre; i++ {
		lignes = append(lignes, label.BuildLigneExport(map[string]any{
			"dn":        fmt.Sprintf("CN=Eleve %d,OU=3emeA,DC=exemple,DC=local", i),
			"sam":       fmt.Sprintf("eleve%d", i),
			"cn":        fmt.Sprintf("Eleve %d", i),
			"givenName": fmt.Sprintf("Prenom%d", i),
			"sn":        fmt.Sprintf("Nom%d", i),
			"mail":      fmt.Sprintf("eleve%d@exemple.local", i),
		}))
	}
	return lignes
}

func (s *Server) pageLabel(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "label.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	d.ModelesLabel = label.ChargerModeles(s.cheminModelesLabel())
	d.MailLabel = *mailer.ChargerConfig(s.cheminMailLabel(), s.cheminCleLabel())
	s.rendre(w, http.StatusOK, "label.html", d)
}

// labelApercu génère une planche d'étiquettes de démonstration (téléchargement).
func (s *Server) labelApercu(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	modele, ok := trouverModeleLabel(label.ChargerModeles(s.cheminModelesLabel()), strings.TrimSpace(r.FormValue("modele")))
	if !ok {
		s.erreur(w, Tr(s.langue(r), "label.modele_introuvable"))
		return
	}
	nombre := bornesInt(r.FormValue("nb"), 6, 1, 50)
	donnees, _, err := label.RendreEtiquettesPDF(lignesDemonstration(nombre), modele, nil)
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.telechargerPDF(w, "apercu_"+modele.ID+".pdf", donnees)
}

// labelTrombinoscope génère un trombinoscope de démonstration.
func (s *Server) labelTrombinoscope(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	page := strings.TrimSpace(r.FormValue("page"))
	if page == "" {
		page = "A4"
	}
	titre := strings.TrimSpace(r.FormValue("titre"))
	if titre == "" {
		titre = Tr(langue, "label.trombi_titre_defaut")
	}
	nombre := bornesInt(r.FormValue("nb"), 12, 1, 100)
	donnees, _, err := label.GenererTrombinoscopePDF(lignesDemonstration(nombre), label.OptionsTrombinoscope{
		Page: page, Colonnes: bornesInt(r.FormValue("colonnes"), label.ColonnesParDefaut, 1, 8), Titre: titre,
	})
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.telechargerPDF(w, "trombinoscope.pdf", donnees)
}

// labelMail enregistre la configuration SMTP des étiquettes.
func (s *Server) labelMail(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	cfg := mailer.ChargerConfig(s.cheminMailLabel(), s.cheminCleLabel())
	if v := strings.TrimSpace(r.FormValue("host")); v != "" {
		cfg.Host = v
	}
	if v := strings.TrimSpace(r.FormValue("port")); v != "" {
		cfg.Port = bornesInt(v, cfg.Port, 1, 65535)
	}
	cfg.Username = strings.TrimSpace(r.FormValue("username"))
	if v := strings.TrimSpace(r.FormValue("password")); v != "" {
		cfg.Password = v
	}
	if v := strings.TrimSpace(r.FormValue("from")); v != "" {
		cfg.FromAddr = v
	}
	if v := strings.TrimSpace(r.FormValue("from_name")); v != "" {
		cfg.FromName = v
	}
	cfg.UseSSL = valeurCaseACocher(r.FormValue("ssl"))
	cfg.UseTLS = !valeurCaseACocher(r.FormValue("no_tls")) && !cfg.UseSSL
	if _, err := mailer.SauvegarderConfig(cfg, s.cheminMailLabel(), s.cheminCleLabel()); err != nil {
		s.erreur(w, err.Error())
		return
	}
	http.Redirect(w, r, "/app/label", http.StatusSeeOther)
}

// labelSupprimer retire un modèle personnalisé.
func (s *Server) labelSupprimer(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	id := strings.TrimSpace(r.FormValue("id"))
	modeles := label.ChargerModeles(s.cheminModelesLabel())
	restants := make([]label.ModeleLabel, 0, len(modeles))
	supprime := false
	for _, m := range modeles {
		if m.ID == id {
			supprime = true
			continue
		}
		restants = append(restants, m)
	}
	if !supprime {
		s.erreur(w, Tr(s.langue(r), "label.modele_introuvable"))
		return
	}
	if _, err := label.SauvegarderModeles(restants, s.cheminModelesLabel()); err != nil {
		s.erreur(w, err.Error())
		return
	}
	http.Redirect(w, r, "/app/label", http.StatusSeeOther)
}

// telechargerPDF prépare une réponse de téléchargement PDF.
func (s *Server) telechargerPDF(w http.ResponseWriter, nom string, donnees []byte) {
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", `attachment; filename="`+nom+`"`)
	_, _ = w.Write(donnees)
}

// bornesInt convertit une valeur de formulaire en entier borné.
func bornesInt(valeur string, defaut, min, max int) int {
	n, err := strconv.Atoi(strings.TrimSpace(valeur))
	if err != nil {
		n = defaut
	}
	if n < min {
		n = min
	}
	if n > max {
		n = max
	}
	return n
}

// ─── Multisite / multi-domaine (M25) ─────────────────────────────────────

// dossierMultisite retourne le dossier de domaines.json : à côté de config.json
// en test, sinon dossier de configuration utilisateur (partagé avec Python).
func (s *Server) dossierMultisite() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := config.ConfigDir(); err == nil && dir != "" {
		return dir
	}
	return "."
}

// cheminSites retourne le chemin de domaines.json.
func (s *Server) cheminSites() string {
	return filepath.Join(s.dossierMultisite(), multisite.NomFichier)
}

// cheminCleSites retourne le chemin de la clé locale (secret.key).
func (s *Server) cheminCleSites() string {
	return filepath.Join(s.dossierMultisite(), multisite.NomFichierCle)
}

func (s *Server) pageMultisite(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "multisite.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	if profils, err := multisite.ChargerSites(s.cheminSites(), s.cheminCleSites()); err == nil {
		d.Sites = profils
	}
	if s.journal != nil {
		if domaines, err := s.journal.Domains(); err == nil {
			d.DomainesJournal = domaines
		}
	}
	s.rendre(w, http.StatusOK, "multisite.html", d)
}

// multisiteAjouter enregistre un nouveau profil de domaine.
func (s *Server) multisiteAjouter(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	profils, err := multisite.ChargerSites(s.cheminSites(), s.cheminCleSites())
	if err != nil {
		s.multisiteMessage(w, langue, err.Error(), true)
		return
	}
	memoriser := r.FormValue("remember_password") != ""
	profil := multisite.NouveauProfil(
		strings.TrimSpace(r.FormValue("label")),
		strings.TrimSpace(r.FormValue("domain")),
		multisite.AvecControleur(r.FormValue("controller")),
		multisite.AvecIdentifiant(r.FormValue("username")),
		multisite.AvecMotDePasse(r.FormValue("password"), memoriser),
		multisite.AvecVerificationCertificat(valeurCaseACocher(r.FormValue("verify_certificate"))),
		multisite.AvecCACert(r.FormValue("ca_cert_path")),
		multisite.AvecExistants(profils),
	)
	if errs := profil.Validate(); len(errs) > 0 {
		s.multisiteMessage(w, langue, strings.Join(errs, " "), true)
		return
	}
	profils = append(profils, profil)
	if err := multisite.SauvegarderSites(profils, s.cheminSites(), s.cheminCleSites()); err != nil {
		s.multisiteMessage(w, langue, err.Error(), true)
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("multisite_ajout", profil.Domain, "succes", profil.DisplayName())
		_ = s.journal.Record(entree)
	}
	s.multisiteMessage(w, langue, fmt.Sprintf(Tr(langue, "multisite.ajoute"), profil.DisplayName()), false)
}

// multisiteSupprimer retire un profil de domaine.
func (s *Server) multisiteSupprimer(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	id := strings.TrimSpace(r.FormValue("id"))
	if id == "" {
		s.multisiteMessage(w, langue, Tr(langue, "multisite.id_obligatoire"), true)
		return
	}
	profils, err := multisite.ChargerSites(s.cheminSites(), s.cheminCleSites())
	if err != nil {
		s.multisiteMessage(w, langue, err.Error(), true)
		return
	}
	restants := make([]multisite.ProfilDomaine, 0, len(profils))
	supprime := false
	for _, profil := range profils {
		if profil.ID == id {
			supprime = true
			continue
		}
		restants = append(restants, profil)
	}
	if !supprime {
		s.multisiteMessage(w, langue, Tr(langue, "multisite.introuvable"), true)
		return
	}
	if err := multisite.SauvegarderSites(restants, s.cheminSites(), s.cheminCleSites()); err != nil {
		s.multisiteMessage(w, langue, err.Error(), true)
		return
	}
	s.multisiteMessage(w, langue, Tr(langue, "multisite.supprime"), false)
}

// multisiteMessage rend un message simple (succès ou erreur).
func (s *Server) multisiteMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// valeurCaseACocher indique si une case à cocher de formulaire est cochée.
func valeurCaseACocher(valeur string) bool {
	switch strings.ToLower(strings.TrimSpace(valeur)) {
	case "1", "on", "true", "oui", "yes":
		return true
	default:
		return false
	}
}

// ─── Coffre-fort local des mots de passe ──────────────────────────────────

// dossierCoffre retourne le dossier du coffre : à côté de config.json en test,
// sinon dossier de données utilisateur (partagé avec Python).
func (s *Server) dossierCoffre() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := config.DataDir(); err == nil && dir != "" {
		return dir
	}
	return "."
}

// ouvrirCoffreUI ouvre le coffre partagé (ou celui du dossier de test).
func (s *Server) ouvrirCoffreUI() (*vault.Vault, error) {
	chemin := filepath.Join(s.dossierCoffre(), vault.NomFichier)
	if s.opts.ConfigPath == "" {
		if defaut, err := vault.CheminParDefaut(); err == nil {
			chemin = defaut
		}
	}
	cle := ""
	if s.opts.ConfigPath != "" {
		cle = filepath.Join(s.dossierCoffre(), vault.NomFichierCle)
	}
	return vault.New(chemin, cle)
}

func (s *Server) pageVault(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "vault.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur

	v, err := s.ouvrirCoffreUI()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = v.Close() }()
	if sams, err := v.Liste(); err == nil {
		d.CoffreSAMs = sams
	}
	if nombre, err := v.Count(); err == nil {
		d.NombreCoffre = nombre
	}
	s.rendre(w, http.StatusOK, "vault.html", d)
}

// vaultRechercher réaffiche le mot de passe enregistré d'un compte.
func (s *Server) vaultRechercher(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	sam := strings.TrimSpace(r.FormValue("sam"))
	if sam == "" {
		s.vaultMessage(w, langue, Tr(langue, "vault.sam_obligatoire"), true)
		return
	}
	v, err := s.ouvrirCoffreUI()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = v.Close() }()
	motDePasse, ok := v.Get(sam)
	if !ok {
		s.vaultMessage(w, langue, fmt.Sprintf(Tr(langue, "vault.absent"), sam), true)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprintf(w, `<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`+
		`<p class="font-semibold">%s</p>`+
		`<p class="mt-2 font-mono text-base text-emerald-600 dark:text-emerald-400">%s</p></div>`,
		html.EscapeString(sam), html.EscapeString(motDePasse))
}

// vaultSupprimer supprime l'entrée d'un compte.
func (s *Server) vaultSupprimer(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	sam := strings.TrimSpace(r.FormValue("sam"))
	if sam == "" {
		s.vaultMessage(w, langue, Tr(langue, "vault.sam_obligatoire"), true)
		return
	}
	v, err := s.ouvrirCoffreUI()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = v.Close() }()
	if err := v.Delete(sam); err != nil {
		s.vaultMessage(w, langue, err.Error(), true)
		return
	}
	s.vaultMessage(w, langue, fmt.Sprintf(Tr(langue, "vault.supprime"), sam), false)
}

// vaultPurger vide complètement le coffre.
func (s *Server) vaultPurger(w http.ResponseWriter, r *http.Request, _ *session) {
	langue := s.langue(r)
	v, err := s.ouvrirCoffreUI()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = v.Close() }()
	supprimes, err := v.ClearAll()
	if err != nil {
		s.vaultMessage(w, langue, err.Error(), true)
		return
	}
	s.vaultMessage(w, langue, fmt.Sprintf(Tr(langue, "vault.purge"), supprimes), false)
}

// vaultMessage rend un message simple (succès ou erreur).
func (s *Server) vaultMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// ─── Microsoft 365 / Entra ID (M19) ──────────────────────────────────────

// ConfigM365UI expose la configuration Microsoft 365 lisible par le gabarit :
// le secret (même chiffré) n'y figure jamais.
type ConfigM365UI struct {
	TenantID        string
	ClientID        string
	AuthMode        string
	CertificatePath string
	UsageLocation   string
	SecretDefini    bool
}

// dossierM365 retourne le dossier de configuration Microsoft 365.
func (s *Server) dossierM365() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := graph.DossierConfig(); err == nil && dir != "" {
		return dir
	}
	return "."
}

// cheminM365 retourne le chemin du fichier m365.json partagé avec Python.
func (s *Server) cheminM365() string {
	return filepath.Join(s.dossierM365(), graph.FichierConfig)
}

func (s *Server) pageM365(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "m365.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	cfg := graph.ChargerConfig(s.cheminM365())
	d.ConfigM365 = ConfigM365UI{
		TenantID:        cfg.TenantID,
		ClientID:        cfg.ClientID,
		AuthMode:        cfg.AuthMode,
		CertificatePath: cfg.CertificatePath,
		UsageLocation:   cfg.UsageLocation,
		SecretDefini:    cfg.ClientSecret != "",
	}
	s.rendre(w, http.StatusOK, "m365.html", d)
}

// m365ConfigDepuisForm applique le formulaire à une configuration de base (le
// secret n'est remplacé que si le champ est renseigné : il n'est jamais
// réaffiché dans le gabarit).
func m365ConfigDepuisForm(r *http.Request, base graph.Config) graph.Config {
	def := graph.ConfigParDefaut()
	base.TenantID = strings.TrimSpace(r.FormValue("tenant_id"))
	base.ClientID = strings.TrimSpace(r.FormValue("client_id"))
	base.AuthMode = valeurFormulaireOu(r.FormValue("auth_mode"), def.AuthMode)
	base.CertificatePath = strings.TrimSpace(r.FormValue("certificate_path"))
	base.UsageLocation = valeurFormulaireOu(r.FormValue("usage_location"), def.UsageLocation)
	if secret := strings.TrimSpace(r.FormValue("client_secret")); secret != "" {
		base.ClientSecret = secret
	}
	return base
}

// m365ConfigForm enregistre la configuration Microsoft 365.
func (s *Server) m365ConfigForm(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := m365ConfigDepuisForm(r, graph.ChargerConfig(s.cheminM365()))
	if err := graph.SauvegarderConfig(s.cheminM365(), cfg); err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("m365_config", cfg.TenantID, "succes",
			fmt.Sprintf("Mode d'auth: %s", cfg.AuthMode))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.m365Message(w, langue, Tr(langue, "m365.config_enregistree"), false)
}

// lignesFormulaire découpe une zone de texte en lignes, chaque ligne en champs
// séparés par « ; » (espaces retirés, lignes vides ignorées).
func lignesFormulaire(valeur string) [][]string {
	var resultat [][]string
	for _, ligne := range strings.Split(valeur, "\n") {
		ligne = strings.TrimSpace(ligne)
		if ligne == "" {
			continue
		}
		champs := strings.Split(ligne, ";")
		for i := range champs {
			champs[i] = strings.TrimSpace(champs[i])
		}
		resultat = append(resultat, champs)
	}
	return resultat
}

// m365UserPlans prépare les comptes cloud depuis les comptes AD saisis.
func (s *Server) m365UserPlans(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	var comptes []m365.CompteAD
	for _, champs := range lignesFormulaire(r.FormValue("comptes")) {
		compte := m365.CompteAD{SAM: champs[0]}
		if len(champs) > 1 {
			compte.CN = champs[1]
		}
		comptes = append(comptes, compte)
	}
	if len(comptes) == 0 {
		s.m365Message(w, langue, Tr(langue, "m365.comptes_obligatoires"), true)
		return
	}
	var existants []string
	for _, champs := range lignesFormulaire(r.FormValue("existing_upns")) {
		existants = append(existants, champs[0])
	}
	plans := m365.ConstruirePlansUtilisateurs(comptes, strings.TrimSpace(r.FormValue("domain")), existants)
	libelles := make([]string, 0, len(plans))
	for _, plan := range plans {
		libelles = append(libelles, plan.Label())
	}
	s.m365PlansFragment(w, langue, libelles)
}

// m365GroupPlans prépare les groupes Entra depuis les groupes AD saisis.
func (s *Server) m365GroupPlans(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	kind := strings.TrimSpace(r.FormValue("kind"))
	if _, ok := m365.TypesGroupe[kind]; !ok {
		s.m365Message(w, langue, Tr(langue, "m365.type_inconnu"), true)
		return
	}
	var specs []m365.SpecGroupe
	for _, champs := range lignesFormulaire(r.FormValue("groupes")) {
		spec := m365.SpecGroupe{Nom: champs[0]}
		if len(champs) > 1 {
			spec.Membres = append(spec.Membres, champs[1:]...)
		}
		specs = append(specs, spec)
	}
	if len(specs) == 0 {
		s.m365Message(w, langue, Tr(langue, "m365.groupes_obligatoires"), true)
		return
	}
	plans := m365.ConstruirePlansGroupes(specs, kind, nil)
	libelles := make([]string, 0, len(plans))
	for _, plan := range plans {
		libelles = append(libelles, plan.Label())
	}
	s.m365PlansFragment(w, langue, libelles)
}

// m365Message rend un message simple (succès ou erreur).
func (s *Server) m365Message(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// m365PlansFragment rend une liste de plans (comptes ou groupes).
func (s *Server) m365PlansFragment(w http.ResponseWriter, langue string, libelles []string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	title := Tr(langue, "m365.plans")
	fmt.Fprintf(&b, `<p class="font-semibold">%d %s</p>`, len(libelles), html.EscapeString(title))
	if len(libelles) == 0 {
		b.WriteString(`<p class="mt-2 text-slate-500">` + html.EscapeString(Tr(langue, "m365.aucun")) + `</p>`)
	} else {
		b.WriteString(`<ul class="mt-2 space-y-1 font-mono text-xs">`)
		for _, libelle := range libelles {
			b.WriteString(`<li>` + html.EscapeString(libelle) + `</li>`)
		}
		b.WriteString(`</ul>`)
	}
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── RDS / Bureau à distance (M21) ────────────────────────────────────────

// dossierRDS retourne le dossier de configuration RDS : à côté de config.json
// (cohérent en test et avec Python), sinon dossier utilisateur.
func (s *Server) dossierRDS() string {
	if s.opts.ConfigPath != "" {
		return filepath.Dir(s.opts.ConfigPath)
	}
	if dir, err := rds.DossierConfig(); err == nil && dir != "" {
		return dir
	}
	return "."
}

func (s *Server) pageRDS(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "rds.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	d.ConfigRDS = rds.ChargerConfig(filepath.Join(s.dossierRDS(), rds.FichierConfig))
	s.rendre(w, http.StatusOK, "rds.html", d)
}

// rdsConfigDepuisForm construit une configuration RDS depuis le formulaire.
func rdsConfigDepuisForm(r *http.Request) rds.Config {
	cfg := rds.ChargerConfig("")
	cfg.Broker = strings.TrimSpace(r.FormValue("broker"))
	cfg.Domain = strings.TrimSpace(r.FormValue("domain"))
	if mode := strings.TrimSpace(r.FormValue("profile_mode")); mode != "" {
		cfg.ProfileMode = mode
	}
	cfg.UPDPath = strings.TrimSpace(r.FormValue("upd_path"))
	if taille, err := strconv.Atoi(strings.TrimSpace(r.FormValue("upd_size_gb"))); err == nil {
		cfg.UPDSizeGB = taille
	}
	cfg.UPDIncludePaths = rds.SplitMulti(r.FormValue("upd_include_paths"))
	cfg.UPDExcludePaths = rds.SplitMulti(r.FormValue("upd_exclude_paths"))
	cfg.FSLogixLocations = rds.SplitMulti(r.FormValue("fslogix_locations"))
	if taille, err := strconv.Atoi(strings.TrimSpace(r.FormValue("fslogix_size_mb"))); err == nil {
		cfg.FSLogixSizeMB = taille
	}
	cfg.FSLogixFlipFlop = r.FormValue("fslogix_flipflop") != ""
	return cfg
}

// rdsConfigForm enregistre la configuration RDS.
func (s *Server) rdsConfigForm(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := rdsConfigDepuisForm(r)
	if err := rds.SauvegarderConfig(filepath.Join(s.dossierRDS(), rds.FichierConfig), cfg); err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("rds_config", cfg.Broker, "succes",
			fmt.Sprintf("Mode de profil: %s", cfg.ProfileMode))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.rdsMessage(w, langue, Tr(langue, "rds.config_enregistree"), false)
}

// rdsCollectionScript génère le script PowerShell des collections de sessions.
func (s *Server) rdsCollectionScript(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	name := strings.TrimSpace(r.FormValue("name"))
	if name == "" {
		s.rdsMessage(w, langue, Tr(langue, "rds.nom_obligatoire"), true)
		return
	}
	cfg := rds.ChargerConfig(filepath.Join(s.dossierRDS(), rds.FichierConfig))
	plan := rds.ConstruirePlanCollection(
		name, r.FormValue("description"),
		rds.SplitMulti(r.FormValue("hosts")), rds.SplitMulti(r.FormValue("groups")), cfg)
	script, err := rds.ScriptCollections([]rds.PlanCollection{plan}, cfg)
	if err != nil {
		s.rdsMessage(w, langue, err.Error(), true)
		return
	}
	s.rdsScriptFragment(w, langue, script)
}

// rdsRemoteAppScript génère le script PowerShell de publication des RemoteApp.
func (s *Server) rdsRemoteAppScript(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	displayName := strings.TrimSpace(r.FormValue("display_name"))
	filePath := strings.TrimSpace(r.FormValue("file_path"))
	collection := strings.TrimSpace(r.FormValue("collection"))
	if displayName == "" || filePath == "" || collection == "" {
		s.rdsMessage(w, langue, Tr(langue, "rds.champs_obligatoires"), true)
		return
	}
	cfg := rds.ChargerConfig(filepath.Join(s.dossierRDS(), rds.FichierConfig))
	alias := strings.TrimSpace(r.FormValue("alias"))
	if alias == "" {
		alias = rds.CreerAlias(displayName)
	}
	plan := rds.PlanRemoteApp{
		Alias:           alias,
		DisplayName:     displayName,
		FilePath:        filePath,
		Collection:      collection,
		UserGroups:      normaliserGroupesUI(rds.SplitMulti(r.FormValue("groups")), cfg.Domain),
		ShowInWeb:       r.FormValue("show_in_web") != "",
		FolderName:      strings.TrimSpace(r.FormValue("folder_name")),
		RequiredCommand: strings.TrimSpace(r.FormValue("required_command")),
	}
	if icone, err := strconv.Atoi(strings.TrimSpace(r.FormValue("icon_index"))); err == nil {
		plan.IconIndex = &icone
	}
	script, err := rds.ScriptRemoteApp([]rds.PlanRemoteApp{plan}, cfg)
	if err != nil {
		s.rdsMessage(w, langue, err.Error(), true)
		return
	}
	s.rdsScriptFragment(w, langue, script)
}

// rdsFSLogixScript génère le script PowerShell de configuration FSLogix.
func (s *Server) rdsFSLogixScript(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	cfg := rds.ChargerConfig(filepath.Join(s.dossierRDS(), rds.FichierConfig))
	script, err := rds.ScriptFSLogix(cfg)
	if err != nil {
		s.rdsMessage(w, langue, err.Error(), true)
		return
	}
	s.rdsScriptFragment(w, langue, script)
}

// normaliserGroupesUI applique le préfixe DOMAINE aux groupes sans domaine.
func normaliserGroupesUI(groupes []string, domaine string) []string {
	resultat := []string{}
	for _, groupe := range groupes {
		normalise := rds.NormaliserRefGroupe(groupe, domaine)
		if normalise != "" && !contientValeur(resultat, normalise) {
			resultat = append(resultat, normalise)
		}
	}
	return resultat
}

// contientValeur indique si un slice de chaînes contient une valeur.
func contientValeur(valeurs []string, cible string) bool {
	for _, v := range valeurs {
		if v == cible {
			return true
		}
	}
	return false
}

// rdsMessage rend un message simple (succès ou erreur).
func (s *Server) rdsMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// rdsScriptFragment rend un script PowerShell généré.
func (s *Server) rdsScriptFragment(w http.ResponseWriter, langue, script string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "rds.script") + `</h2>`)
	b.WriteString(`<pre class="mt-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">`)
	b.WriteString(html.EscapeString(script))
	b.WriteString(`</pre></div>`)
	w.Write([]byte(b.String()))
}

// ─── Cache AD SQLite (T2) ─────────────────────────────────────────────────

// cheminCacheAD retourne le chemin du cache AD (à côté de config.json en test,
// sinon chemin partagé avec la version Python).
func (s *Server) cheminCacheAD() string {
	if s.opts.ConfigPath != "" {
		return filepath.Join(filepath.Dir(s.opts.ConfigPath), ad.NomFichierCache)
	}
	if chemin, err := ad.CheminCacheParDefaut(); err == nil && chemin != "" {
		return chemin
	}
	return filepath.Join(".", ad.NomFichierCache)
}

// ouvrirCacheAD ouvre le cache AD pour l'UI.
func (s *Server) ouvrirCacheAD() (*ad.ADCache, error) {
	return ad.NewADCache(s.cheminCacheAD(), ad.CacheTTLDefaut)
}

func (s *Server) pageADCache(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "adcache.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	d.CheminCacheAD = s.cheminCacheAD()
	if cache, err := s.ouvrirCacheAD(); err == nil {
		d.StatsCache = cache.Stats()
		d.CacheADPerime = cache.IsStale()
		_ = cache.Close()
	}
	s.rendre(w, http.StatusOK, "ad_cache.html", d)
}

// adCacheSync synchronise le cache depuis l'annuaire.
func (s *Server) adCacheSync(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	complet := r.FormValue("complet") != ""

	pool, err := s.getPool(sess)
	if err != nil {
		s.adCacheMessage(w, langue, Tr(langue, "adcache.attente_ad"), true)
		return
	}

	cache, err := s.ouvrirCacheAD()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = cache.Close() }()

	stats, err := cache.Sync(r.Context(), pool, complet)
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		mode := "incrémentale"
		if complet {
			mode = "complète"
		}
		entree := nouvelleEntree("ad_cache_sync", s.cheminCacheAD(), "succes",
			fmt.Sprintf("Sync %s : %d OUs, %d groupes, %d utilisateurs", mode, stats.OUsCount, stats.GroupsCount, stats.UsersCount))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.adCacheStatsFragment(w, langue, stats, s.cheminCacheAD())
}

// adCacheSearch recherche des utilisateurs dans le cache.
func (s *Server) adCacheSearch(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	query := strings.TrimSpace(r.FormValue("query"))
	if query == "" {
		s.adCacheMessage(w, langue, Tr(langue, "adcache.query_obligatoire"), true)
		return
	}

	cache, err := s.ouvrirCacheAD()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = cache.Close() }()

	users, err := cache.SearchUsers(query, 100)
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.adCacheUsersFragment(w, langue, users)
}

// adCacheOUs liste les OUs du cache.
func (s *Server) adCacheOUs(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)

	cache, err := s.ouvrirCacheAD()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	defer func() { _ = cache.Close() }()

	ous, err := cache.GetOUs(strings.TrimSpace(r.FormValue("parent")))
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.adCacheOUsFragment(w, langue, ous)
}

// adCacheMessage rend un message simple (succès ou erreur).
func (s *Server) adCacheMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// adCacheStatsFragment rend les statistiques du cache.
func (s *Server) adCacheStatsFragment(w http.ResponseWriter, langue string, stats ad.CacheStats, chemin string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "adcache.stats_titre") + `</h2>`)
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %s</p>`, Tr(langue, "adcache.chemin"), html.EscapeString(chemin)))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "adcache.ous"), stats.OUsCount))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "adcache.groupes"), stats.GroupsCount))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "adcache.utilisateurs"), stats.UsersCount))
	b.WriteString(fmt.Sprintf(`<p class="text-sm"><strong>%s:</strong> %d</p>`, Tr(langue, "adcache.usn"), stats.LastUSNChanged))
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// adCacheUsersFragment rend la liste des utilisateurs trouvés.
func (s *Server) adCacheUsersFragment(w http.ResponseWriter, langue string, users []ad.CacheUser) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "adcache.resultat") + `</h2>`)
	if len(users) == 0 {
		b.WriteString(`<p class="text-sm text-slate-500">` + Tr(langue, "adcache.aucun") + `</p>`)
	} else {
		b.WriteString(`<table class="mt-2 w-full text-sm"><thead><tr class="text-left text-xs uppercase text-slate-500">`)
		b.WriteString(`<th class="py-1">` + Tr(langue, "adcache.col_sam") + `</th>`)
		b.WriteString(`<th class="py-1">` + Tr(langue, "adcache.col_cn") + `</th>`)
		b.WriteString(`<th class="py-1">` + Tr(langue, "adcache.col_ou") + `</th>`)
		b.WriteString(`<th class="py-1">` + Tr(langue, "adcache.col_etat") + `</th></tr></thead><tbody>`)
		for _, u := range users {
			etat := Tr(langue, "adcache.actif")
			if u.Desactive {
				etat = Tr(langue, "adcache.desactive")
			}
			b.WriteString(fmt.Sprintf(`<tr class="border-t border-slate-100 dark:border-slate-800"><td class="py-1 font-mono">%s</td><td class="py-1">%s</td><td class="py-1 text-slate-500">%s</td><td class="py-1">%s</td></tr>`,
				html.EscapeString(u.SAM), html.EscapeString(u.CN), html.EscapeString(u.OUDN), html.EscapeString(etat)))
		}
		b.WriteString(`</tbody></table>`)
	}
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// adCacheOUsFragment rend la liste des OUs.
func (s *Server) adCacheOUsFragment(w http.ResponseWriter, langue string, ous []ad.CacheOU) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<h2 class="text-lg font-semibold">` + Tr(langue, "adcache.resultat") + `</h2>`)
	if len(ous) == 0 {
		b.WriteString(`<p class="text-sm text-slate-500">` + Tr(langue, "adcache.aucun") + `</p>`)
	} else {
		b.WriteString(`<ul class="mt-2 space-y-1 text-sm">`)
		for _, o := range ous {
			b.WriteString(fmt.Sprintf(`<li class="font-mono">%s</li>`, html.EscapeString(o.DN)))
		}
		b.WriteString(`</ul>`)
	}
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// ─── Import/Export avancés (M22) ──────────────────────────────────────────

func (s *Server) pageAdvancedIO(w http.ResponseWriter, r *http.Request, sess *session) {
	d := s.donnees(r, Tr(s.langue(r), "advio.titre"))
	d.Connecte = true
	d.Utilisateur = sess.Utilisateur
	d.Domaine = sess.Domaine
	d.Controleur = sess.Controleur
	if dir, err := ad.DossierConfigAdvIO(); err == nil {
		d.ConfigAdvIO = ad.ChargerConfigAdvIO(dir)
	}
	s.rendre(w, http.StatusOK, "advanced_io.html", d)
}

// advioSourceDepuisForm construit une source LDAP depuis le formulaire.
func advioSourceDepuisForm(r *http.Request) ad.SourceLDAP {
	mode := r.FormValue("mode")
	if mode == "" {
		mode = ad.SourceLDAPOU
	}
	filtre := strings.TrimSpace(r.FormValue("filtre"))
	if filtre == "" {
		filtre = ad.FiltreUtilisateurs
	}
	return ad.SourceLDAP{
		Mode:      mode,
		OUDN:      strings.TrimSpace(r.FormValue("ou_dn")),
		GroupDN:   strings.TrimSpace(r.FormValue("group_dn")),
		Filtre:    filtre,
		BaseDN:    strings.TrimSpace(r.FormValue("base_dn")),
		Attributs: append([]string(nil), ad.AttributsExportDefaut...),
		Limite:    5000,
	}
}

func (s *Server) advioValidateFilter(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	filtre := strings.TrimSpace(r.FormValue("filtre"))
	s.advioFiltreFragment(w, langue, filtre, ad.ValiderFiltreLDAP(filtre))
}

func (s *Server) advioSource(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	source := advioSourceDepuisForm(r)
	base, filtre := source.RequeteLDAP()
	s.advioSourceFragment(w, langue, base, filtre, source.Validate())
}

func (s *Server) advioImport(w http.ResponseWriter, r *http.Request, sess *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	source := advioSourceDepuisForm(r)
	if erreurs := source.Validate(); len(erreurs) > 0 {
		s.advioMessage(w, langue, strings.Join(erreurs, " "), true)
		return
	}

	pool, err := s.getPool(sess)
	if err != nil {
		s.advioMessage(w, langue, Tr(langue, "advio.attente_ad"), true)
		return
	}
	entrees, err := pool.ImporterDepuisAD(r.Context(), source)
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	if s.journal != nil {
		entree := nouvelleEntree("adv_io_import", source.Mode, "succes",
			fmt.Sprintf("Import %s : %d entrée(s)", source.Mode, len(entrees)))
		entree.Utilisateur = sess.Utilisateur
		entree.Domaine = sess.Domaine
		_ = s.journal.Record(entree)
	}
	s.advioImportFragment(w, langue, entrees)
}

func (s *Server) advioGEPMapping(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	var headers []string
	for _, h := range strings.Split(r.FormValue("headers"), ",") {
		if h = strings.TrimSpace(h); h != "" {
			headers = append(headers, h)
		}
	}
	s.advioMappingFragment(w, langue, ad.SuggererMappingGEP(headers))
}

func (s *Server) advioConfig(w http.ResponseWriter, r *http.Request, _ *session) {
	if err := r.ParseForm(); err != nil {
		s.erreur(w, Tr(s.langue(r), "erreur.formulaire"))
		return
	}
	langue := s.langue(r)
	dir, err := ad.DossierConfigAdvIO()
	if err != nil {
		s.erreur(w, err.Error())
		return
	}
	cfg := ad.ConfigAdvIO{CheminOutilGEP: strings.TrimSpace(r.FormValue("gep_tool_path"))}
	if err := ad.EnregistrerConfigAdvIO(dir, cfg); err != nil {
		s.erreur(w, err.Error())
		return
	}
	s.advioMessage(w, langue, Tr(langue, "advio.config_enregistree"), false)
}

// advioMessage rend un message simple.
func (s *Server) advioMessage(w http.ResponseWriter, langue, message string, erreur bool) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	classe := "text-emerald-600 dark:text-emerald-400"
	if erreur {
		classe = "text-rose-600 dark:text-rose-400"
	}
	fmt.Fprintf(w, `<p class="text-sm %s">%s</p>`, classe, html.EscapeString(message))
}

// advioFiltreFragment rend le résultat de la validation d'un filtre.
func (s *Server) advioFiltreFragment(w http.ResponseWriter, langue, filtre string, erreurs []string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<p class="font-mono break-all text-xs text-slate-500">` + html.EscapeString(filtre) + `</p>`)
	if len(erreurs) == 0 {
		b.WriteString(`<p class="mt-2 text-emerald-600 dark:text-emerald-400">` + Tr(langue, "advio.filtre_valide") + `</p>`)
	} else {
		b.WriteString(`<ul class="mt-2 list-disc pl-5 text-rose-600 dark:text-rose-400">`)
		for _, e := range erreurs {
			b.WriteString(`<li>` + html.EscapeString(e) + `</li>`)
		}
		b.WriteString(`</ul>`)
	}
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// advioSourceFragment rend la requête LDAP construite et sa validation.
func (s *Server) advioSourceFragment(w http.ResponseWriter, langue, base, filtre string, erreurs []string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(fmt.Sprintf(`<p><strong>%s :</strong> <span class="font-mono break-all">%s</span></p>`, Tr(langue, "advio.base"), html.EscapeString(base)))
	b.WriteString(fmt.Sprintf(`<p><strong>%s :</strong> <span class="font-mono break-all">%s</span></p>`, Tr(langue, "advio.filtre"), html.EscapeString(filtre)))
	if len(erreurs) == 0 {
		b.WriteString(`<p class="mt-2 text-emerald-600 dark:text-emerald-400">` + Tr(langue, "advio.source_valide") + `</p>`)
	} else {
		b.WriteString(`<ul class="mt-2 list-disc pl-5 text-rose-600 dark:text-rose-400">`)
		for _, e := range erreurs {
			b.WriteString(`<li>` + html.EscapeString(e) + `</li>`)
		}
		b.WriteString(`</ul>`)
	}
	b.WriteString(`</div>`)
	w.Write([]byte(b.String()))
}

// advioImportFragment rend un aperçu (limité) des entrées importées.
func (s *Server) advioImportFragment(w http.ResponseWriter, langue string, entrees []ad.EntreeLDAP) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	const maxAffiche = 50
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(fmt.Sprintf(`<p class="font-semibold">%d %s</p>`, len(entrees), Tr(langue, "advio.entrees")))
	if len(entrees) == 0 {
		b.WriteString(`<p class="text-slate-500">` + Tr(langue, "advio.aucun") + `</p></div>`)
		w.Write([]byte(b.String()))
		return
	}
	b.WriteString(`<ul class="mt-2 space-y-1 font-mono text-xs">`)
	for i, e := range entrees {
		if i >= maxAffiche {
			b.WriteString(fmt.Sprintf(`<li class="text-slate-500">… (+%d)</li>`, len(entrees)-maxAffiche))
			break
		}
		b.WriteString(`<li>` + html.EscapeString(fmt.Sprintf("%v", e["dn"])) + `</li>`)
	}
	b.WriteString(`</ul></div>`)
	w.Write([]byte(b.String()))
}

// advioMappingFragment rend l'association automatique des colonnes GEP.
func (s *Server) advioMappingFragment(w http.ResponseWriter, langue string, mapping map[string]string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<div class="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">`)
	b.WriteString(`<table class="w-full text-sm"><tbody>`)
	for _, col := range ad.ColonnesImportEduSync {
		b.WriteString(fmt.Sprintf(`<tr><td class="py-1 pr-4 text-slate-500">%s</td><td class="py-1 font-mono">%s</td></tr>`,
			html.EscapeString(col), html.EscapeString(mapping[col])))
	}
	b.WriteString(`</tbody></table></div>`)
	w.Write([]byte(b.String()))
}

// nouvelOperation enregistre une opération de masse et retourne son ID
// ainsi qu'un contexte annulable. Le serveur suit la progression via ce ID.
func (s *Server) nouvelOperation(typeOp string) (string, context.Context) {
	return s.progress.NouvelID(typeOp)
}

// majProgression met à jour la progression d'une opération. Retourne faux
// si l'opération n'existe plus ou a été annulée.
func (s *Server) majProgression(id string, actuel int, message string) bool {
	return s.progress.MiseAJour(id, actuel, message)
}

// terminerOperation marque une opération comme terminée.
func (s *Server) terminerOperation(id string, succes bool, message, erreur string) {
	s.progress.Terminer(id, succes, message, erreur)
}

// ─── Rendu simulation utilisateurs ────────────────────────────────────

func (s *Server) rendreSimulationUsers(w http.ResponseWriter, langue string, users []models.GeneratedUser) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	var b strings.Builder
	b.WriteString(`<table class="w-full text-sm"><thead><tr class="bg-slate-50 dark:bg-slate-800"><th class="px-4 py-2 text-left">Prénom</th><th class="px-4 py-2 text-left">Nom</th><th class="px-4 py-2 text-left">Identifiant</th><th class="px-4 py-2 text-left">OU cible</th><th class="px-4 py-2 text-left">Mot de passe</th><th class="px-4 py-2 text-left">Erreur</th></tr></thead><tbody>`)
	for _, u := range users {
		b.WriteString(fmt.Sprintf(`<tr><td class="px-4 py-2">%s</td><td class="px-4 py-2">%s</td><td class="px-4 py-2 font-mono">%s</td><td class="px-4 py-2">%s</td><td class="px-4 py-2 font-mono">%s</td><td class="px-4 py-2 text-red-600">%s</td></tr>`,
			u.Source.Prenom, u.Source.Nom, u.Identifiant, u.OUCible, u.MotDePasse, u.Erreur))
	}
	b.WriteString(`</tbody></table>`)
	w.Write([]byte(b.String()))
}
