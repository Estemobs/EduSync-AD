// Package ad gère la connexion et les opérations Active Directory.
//
// Pattern universel par opération : Validate() → Simulate() → Execute() → Audit()
// (ROADMAP_GO_MIGRATION.md §3). Aucune écriture AD sans prévisualisation.
package ad

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net"
	"time"

	"github.com/go-ldap/ldap/v3"
	"github.com/jcmturner/gokrb5/v8/client"
	"github.com/jcmturner/gokrb5/v8/config"
	"github.com/jcmturner/gokrb5/v8/keytab"
)

// AuthMethod définit la méthode d'authentification.
type AuthMethod string

const (
	AuthSimple AuthMethod = "simple" // Bind simple (DN + mot de passe)
	AuthSASL   AuthMethod = "sasl"   // SASL/GSSAPI (Kerberos)
	AuthNTLM   AuthMethod = "ntlm"   // NTLM (fallback)
)

// Config décrit la cible AD et les paramètres de connexion.
type Config struct {
	Domain        string        // ex. "EDUSYNC.LOCAL"
	Controller    string        // FQDN ou IP du DC
	Port          int           // 636 (LDAPS) ou 389 (LDAP + StartTLS)
	UseLDAPS      bool          // true = LDAPS direct, false = LDAP + StartTLS
	AuthMethod    AuthMethod    // simple, sasl (GSSAPI), ntlm
	BindDN        string        // DN de liaison (simple/ntlm)
	BindPassword  string        // mot de passe (simple/ntlm)
	BaseDN        string        // base de recherche (ex. "DC=EDUSYNC,DC=LOCAL")
	TLSCAPath     string        // certificat CA racine (PEM) — absent = magasin système
	TLSSkipVerify bool          // désactive la vérification (labo uniquement)
	Timeout       time.Duration // délai par opération (défaut 10 s)
	MaxConns      int           // taille max du pool (défaut 10)

	// Kerberos/GSSAPI
	Krb5Realm      string // Realm Kerberos (ex. "EDUSYNC.LOCAL")
	Krb5KeytabPath string // Chemin vers le keytab (pour SASL)
	Krb5SPN        string // Service Principal Name (ex. "ldap/dc.edusync.local@EDUSYNC.LOCAL")
	Krb5ConfigPath string // Chemin vers krb5.conf (optionnel, défaut système)
}

// Validate vérifie la cohérence de la configuration.
func (c *Config) Validate() error {
	if c.Domain == "" {
		return errors.New("ad: domaine manquant")
	}
	if c.Controller == "" {
		return errors.New("ad: contrôleur manquant")
	}
	if c.Port == 0 {
		c.Port = 636
	}
	if c.AuthMethod == "" {
		c.AuthMethod = AuthSimple
	}
	if c.Timeout == 0 {
		c.Timeout = 10 * time.Second
	}
	if c.MaxConns == 0 {
		c.MaxConns = 10
	}
	if c.BaseDN == "" {
		c.BaseDN = domainToBaseDN(c.Domain)
	}

	// Validation selon la méthode d'auth
	switch c.AuthMethod {
	case AuthSimple, AuthNTLM:
		if c.BindDN == "" {
			return errors.New("ad: BindDN obligatoire pour auth simple/NTLM")
		}
		if c.BindPassword == "" {
			return errors.New("ad: BindPassword obligatoire pour auth simple/NTLM")
		}
	case AuthSASL:
		if c.Krb5Realm == "" {
			return errors.New("ad: Krb5Realm obligatoire pour SASL/GSSAPI")
		}
		if c.Krb5KeytabPath == "" {
			return errors.New("ad: Krb5KeytabPath obligatoire pour SASL/GSSAPI")
		}
		if c.Krb5SPN == "" {
			return errors.New("ad: Krb5SPN obligatoire pour SASL/GSSAPI")
		}
	default:
		return fmt.Errorf("ad: méthode d'authentification inconnue: %s", c.AuthMethod)
	}

	return nil
}

// domainToBaseDN convertit "EDUSYNC.LOCAL" → "DC=EDUSYNC,DC=LOCAL".
func domainToBaseDN(domain string) string {
	var parts []string
	for _, p := range splitDomain(domain) {
		parts = append(parts, "DC="+p)
	}
	return join(parts, ",")
}

func splitDomain(d string) []string {
	var out []string
	var cur string
	for _, r := range d {
		if r == '.' {
			out = append(out, cur)
			cur = ""
		} else {
			cur += string(r)
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}

func join(parts []string, sep string) string {
	if len(parts) == 0 {
		return ""
	}
	if len(parts) == 1 {
		return parts[0]
	}
	res := parts[0]
	for i := 1; i < len(parts); i++ {
		res += sep + parts[i]
	}
	return res
}

// Pool maintient un ensemble de connexions LDAP réutilisables.
type Pool struct {
	cfg      Config
	sem      chan struct{}   // limite le nombre de connexions simultanées
	idle     chan *ldap.Conn // connexions disponibles
	tlsCfg   *tls.Config     // configuration TLS partagée
	closed   bool
	dialFunc func(ctx context.Context, addr string) (net.Conn, error)
	cache    *ExplorerCache // cache explorateur (arbre, recherches)
	cacheAD  *ADCache       // cache AD SQLite (T2), optionnel
	krb5Cli  *client.Client // client Kerberos pour SASL/GSSAPI
	policy   Policy         // garde-fou RBAC (M26), optionnel
}

// Policy est le garde-fou de permissions (M26). Un *rbac.Policy la satisfait.
// Check retourne une erreur si l'action est refusée pour les cibles données.
type Policy interface {
	Check(permission string, dns ...string) error
}

// ErrInsufficientRights signale une action refusée par le garde-fou RBAC.
var ErrInsufficientRights = errors.New("ad: droits insuffisants")

// SetPolicy attache (ou détache avec nil) le garde-fou RBAC du pool.
func (p *Pool) SetPolicy(policy Policy) { p.policy = policy }

// verifierApplique verifie une permission pour les cibles données. Sans
// politique attachée, aucune vérification n'est appliquée (tests, scripts).
func (p *Pool) verifier(permission string, dns ...string) error {
	if p.policy == nil {
		return nil
	}
	if err := p.policy.Check(permission, dns...); err != nil {
		return fmt.Errorf("%w: %v", ErrInsufficientRights, err)
	}
	return nil
}

// NewPool crée un pool de connexions AD.
func NewPool(cfg Config) (*Pool, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}

	tlsCfg, err := buildTLSConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("ad: configuration TLS: %w", err)
	}

	dialer := &net.Dialer{
		Timeout:   cfg.Timeout,
		KeepAlive: 30 * time.Second,
	}
	p := &Pool{
		cfg:    cfg,
		sem:    make(chan struct{}, cfg.MaxConns),
		idle:   make(chan *ldap.Conn, cfg.MaxConns),
		tlsCfg: tlsCfg,
		dialFunc: func(ctx context.Context, addr string) (net.Conn, error) {
			return dialer.DialContext(ctx, "tcp", addr)
		},
	}

	// Initialiser le client Kerberos pour SASL/GSSAPI
	if cfg.AuthMethod == AuthSASL {
		krb5Cli, err := newKrb5Client(cfg)
		if err != nil {
			return nil, fmt.Errorf("ad: initialisation Kerberos: %w", err)
		}
		p.krb5Cli = krb5Cli
	}

	// Pré-remplir avec une connexion pour valider dès maintenant
	if _, err := p.Get(context.Background()); err != nil {
		return nil, err
	}
	return p, nil
}

// NewPoolNoValidate crée un pool sans pré-validation de connexion.
// Utile pour les tests où on veut contrôler quand la première connexion est faite.
func NewPoolNoValidate(cfg Config) (*Pool, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}

	tlsCfg, err := buildTLSConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("ad: configuration TLS: %w", err)
	}

	dialer := &net.Dialer{
		Timeout:   cfg.Timeout,
		KeepAlive: 30 * time.Second,
	}
	p := &Pool{
		cfg:    cfg,
		sem:    make(chan struct{}, cfg.MaxConns),
		idle:   make(chan *ldap.Conn, cfg.MaxConns),
		tlsCfg: tlsCfg,
		dialFunc: func(ctx context.Context, addr string) (net.Conn, error) {
			return dialer.DialContext(ctx, "tcp", addr)
		},
	}

	// Initialiser le client Kerberos pour SASL/GSSAPI
	if cfg.AuthMethod == AuthSASL {
		krb5Cli, err := newKrb5Client(cfg)
		if err != nil {
			return nil, fmt.Errorf("ad: initialisation Kerberos: %w", err)
		}
		p.krb5Cli = krb5Cli
	}

	// Pas de pré-validation
	return p, nil
}

// newKrb5Client crée un client Kerberos à partir du keytab.
func newKrb5Client(cfg Config) (*client.Client, error) {
	kt, err := keytab.Load(cfg.Krb5KeytabPath)
	if err != nil {
		return nil, fmt.Errorf("chargement keytab %s: %w", cfg.Krb5KeytabPath, err)
	}

	var krb5Conf *config.Config
	if cfg.Krb5ConfigPath != "" {
		krb5Conf, err = config.Load(cfg.Krb5ConfigPath)
		if err != nil {
			return nil, fmt.Errorf("chargement krb5.conf %s: %w", cfg.Krb5ConfigPath, err)
		}
	} else {
		// Config minimale par défaut
		krb5Conf = config.New()
		krb5Conf.LibDefaults.DefaultRealm = cfg.Krb5Realm
		krb5Conf.Realms = append(krb5Conf.Realms, config.Realm{
			Realm: cfg.Krb5Realm,
			KDC:   []string{cfg.Controller},
		})
	}

	cl := client.NewWithKeytab(cfg.Krb5SPN, cfg.Krb5Realm, kt, krb5Conf)
	if err := cl.Login(); err != nil {
		return nil, fmt.Errorf("login Kerberos: %w", err)
	}
	return cl, nil
}

// buildTLSConfig construit la configuration TLS à partir de la Config.
func buildTLSConfig(cfg Config) (*tls.Config, error) {
	tlsCfg := &tls.Config{
		ServerName:         cfg.Controller,
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: cfg.TLSSkipVerify, //nolint:gosec // option explicite
	}

	if cfg.TLSCAPath != "" {
		caData, err := readFile(cfg.TLSCAPath)
		if err != nil {
			return nil, fmt.Errorf("ad: lecture CA %s: %w", cfg.TLSCAPath, err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caData) {
			return nil, fmt.Errorf("ad: CA %s invalide (aucun certificat PEM)", cfg.TLSCAPath)
		}
		tlsCfg.RootCAs = pool
	}
	return tlsCfg, nil
}

// Get récupère une connexion du pool (crée si nécessaire).
// La connexion est déjà liée (Bind) et prête à l'emploi.
func (p *Pool) Get(ctx context.Context) (*ldap.Conn, error) {
	if p.closed {
		return nil, errors.New("ad: pool fermé")
	}

	select {
	case p.sem <- struct{}{}:
		// Slot obtenu
	case <-ctx.Done():
		return nil, ctx.Err()
	}

	// Essayer de réutiliser une connexion idle
	select {
	case conn := <-p.idle:
		if p.isHealthy(conn) {
			return conn, nil
		}
		_ = conn.Close()
	default:
		// Pas de connexion idle saine
	}

	// Créer une nouvelle connexion
	conn, err := p.dial(ctx)
	if err != nil {
		<-p.sem
		return nil, err
	}
	return conn, nil
}

// Put remet une connexion dans le pool (ou la ferme si le pool est plein/fermé).
func (p *Pool) Put(conn *ldap.Conn) {
	if p.closed || conn == nil {
		if conn != nil {
			_ = conn.Close()
		}
		return
	}
	select {
	case p.idle <- conn:
		<-p.sem
	default:
		// Pool plein : on ferme la connexion excédentaire
		_ = conn.Close()
		<-p.sem
	}
}

// dial établit une nouvelle connexion LDAP, effectue TLS/StartTLS et Bind.
func (p *Pool) dial(ctx context.Context) (*ldap.Conn, error) {
	addr := net.JoinHostPort(p.cfg.Controller, fmt.Sprint(p.cfg.Port))

	var rawConn net.Conn
	var err error
	if p.cfg.UseLDAPS {
		rawConn, err = tls.DialWithDialer(&net.Dialer{Timeout: p.cfg.Timeout}, "tcp", addr, p.tlsCfg)
		if err != nil {
			return nil, fmt.Errorf("ad: LDAPS dial %s: %w", addr, err)
		}
	} else {
		rawConn, err = p.dialFunc(ctx, addr)
		if err != nil {
			return nil, fmt.Errorf("ad: LDAP dial %s: %w", addr, err)
		}
	}

	ldapConn := ldap.NewConn(rawConn, false)
	ldapConn.SetTimeout(p.cfg.Timeout)

	if !p.cfg.UseLDAPS {
		// StartTLS sur le port 389
		if err := ldapConn.StartTLS(p.tlsCfg); err != nil {
			_ = ldapConn.Close()
			return nil, fmt.Errorf("ad: StartTLS sur %s: %w", addr, err)
		}
	}

	// Bind selon la méthode d'authentification
	switch p.cfg.AuthMethod {
	case AuthSimple:
		if err := ldapConn.Bind(p.cfg.BindDN, p.cfg.BindPassword); err != nil {
			_ = ldapConn.Close()
			return nil, fmt.Errorf("ad: bind simple %s: %w", p.cfg.BindDN, err)
		}
	case AuthNTLM:
		// NTLM bind via SASL (similaire à simple pour go-ldap)
		if err := ldapConn.Bind(p.cfg.BindDN, p.cfg.BindPassword); err != nil {
			_ = ldapConn.Close()
			return nil, fmt.Errorf("ad: bind NTLM %s: %w", p.cfg.BindDN, err)
		}
	case AuthSASL:
		if p.krb5Cli == nil {
			_ = ldapConn.Close()
			return nil, errors.New("ad: client Kerberos non initialisé pour SASL")
		}
		if err := p.bindSASLGSSAPI(ldapConn); err != nil {
			_ = ldapConn.Close()
			return nil, fmt.Errorf("ad: bind SASL/GSSAPI: %w", err)
		}
	default:
		_ = ldapConn.Close()
		return nil, fmt.Errorf("ad: méthode d'auth non supportée: %s", p.cfg.AuthMethod)
	}

	return ldapConn, nil
}

// bindSASLGSSAPI effectue l'authentification SASL/GSSAPI (Kerberos).
func (p *Pool) bindSASLGSSAPI(conn *ldap.Conn) error {
	// Utiliser le client Kerberos pour obtenir un mécanisme GSSAPI
	gssapi, err := p.krb5Cli.GSSAPIClient()
	if err != nil {
		return fmt.Errorf("échec de la création du client GSSAPI: %w", err)
	}
	if err := conn.BindSASL(gssapi); err != nil {
		return fmt.Errorf("bind SASL/GSSAPI échoué: %w", err)
	}
	return nil
}

// isHealthy vérifie qu'une connexion idle est encore valide (ping LDAP).
func (p *Pool) isHealthy(conn *ldap.Conn) bool {
	// Une recherche minimale sur la racine (base DN) suffit
	_, err := conn.SearchWithPaging(ldap.NewSearchRequest(
		p.cfg.BaseDN, ldap.ScopeBaseObject, ldap.NeverDerefAliases,
		1, 0, false, "(objectClass=*)", []string{"1.1"}, nil,
	), 1)
	return err == nil
}

// Close ferme toutes les connexions du pool.
func (p *Pool) Close() error {
	p.closed = true
	close(p.sem)
	for conn := range p.idle {
		_ = conn.Close()
	}
	return nil
}

// BaseDN retourne le DN de base du domaine.
func (p *Pool) BaseDN() string {
	return p.cfg.BaseDN
}

// readFile wrapper pour tests (remplaçable).
var readFile = func(path string) ([]byte, error) {
	return nil, fmt.Errorf("ad: lecture fichier non implémentée: %s", path)
}

// SetReadFile permet d'injecter une fonction de lecture pour les tests.
func SetReadFile(f func(string) ([]byte, error)) {
	readFile = f
}
