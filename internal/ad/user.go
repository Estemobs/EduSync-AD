// Package ad fournit les opérations utilisateur Active Directory.
// Chaque opération suit le pattern : Validate() → Simulate() → Execute() → Audit()
// (ROADMAP_GO_MIGRATION.md §3). Le mode simulation est le chemin par défaut.
package ad

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/go-ldap/ldap/v3"
)

// User représente un compte utilisateur AD.
type User struct {
	DN                string
	SAMAccountName    string
	UserPrincipalName string
	CN                string
	DisplayName       string
	GivenName         string
	SN                string
	Description       string
	Email             string
	Enabled           bool
	MemberOf          []string
	OU                string
}

// UserCreateParams décrit les paramètres de création d'un utilisateur.
type UserCreateParams struct {
	SAMAccountName    string
	UserPrincipalName string
	DisplayName       string
	GivenName         string
	SN                string
	InitialPassword   string
	OU                string // DN de l'OU parente
	Description       string
	Email             string
	Enabled           bool     // true par défaut
	Groups            []string // DN des groupes à ajouter
}

// Validate vérifie la cohérence des paramètres de création.
func (p *UserCreateParams) Validate() error {
	if p.SAMAccountName == "" {
		return errors.New("ad: SAMAccountName obligatoire")
	}
	if len(p.SAMAccountName) > 20 {
		return errors.New("ad: SAMAccountName trop long (>20)")
	}
	// sAMAccountName: letters, digits, dots, underscores, hyphens (cannot start with .)
	if matched, _ := regexp.MatchString(`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`, p.SAMAccountName); !matched {
		return errors.New("ad: SAMAccountName contient des caractères non autorisés")
	}
	if p.UserPrincipalName == "" {
		return errors.New("ad: UserPrincipalName obligatoire")
	}
	// simple email-ish check for UPN
	if matched, _ := regexp.MatchString(`^[^@\s]+@[^@\s]+\.[^@\s]+$`, p.UserPrincipalName); !matched {
		return errors.New("ad: UserPrincipalName doit être une adresse email valide")
	}
	if p.DisplayName == "" {
		return errors.New("ad: DisplayName obligatoire")
	}
	if p.GivenName == "" {
		return errors.New("ad: GivenName obligatoire")
	}
	if p.SN == "" {
		return errors.New("ad: SN obligatoire")
	}
	if p.InitialPassword == "" {
		return errors.New("ad: mot de passe initial obligatoire")
	}
	if len(p.InitialPassword) < 8 {
		return errors.New("ad: mot de passe initial trop court (min 8)")
	}
	if p.OU == "" {
		return errors.New("ad: OU parente obligatoire")
	}
	if p.Email != "" {
		if matched, _ := regexp.MatchString(`^[^@\s]+@[^@\s]+\.[^@\s]+$`, p.Email); !matched {
			return errors.New("ad: email invalide")
		}
	}
	return nil
}

// SimulateResult décrit le résultat d'une simulation.
type SimulateResult struct {
	Action       string
	TargetDN     string
	Changes      []string
	WouldSucceed bool
	Error        string
}

// UserCreateSimulate simule la création d'un utilisateur (sans écrire).
func (p *Pool) UserCreateSimulate(ctx context.Context, params UserCreateParams, conn *ldap.Conn) (*SimulateResult, error) {
	if err := params.Validate(); err != nil {
		return nil, err
	}
	if conn == nil {
		var err error
		conn, err = p.Get(ctx)
		if err != nil {
			return nil, err
		}
		defer p.Put(conn)
	}

	// Vérifier si l'utilisateur existe déjà
	filter := fmt.Sprintf("(&(objectClass=user)(sAMAccountName=%s))", ldap.EscapeFilter(params.SAMAccountName))

	req := ldap.NewSearchRequest(params.OU, ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 1, 0, false, filter, []string{"dn"}, nil)

	res, err := conn.Search(req)
	if err != nil {
		return &SimulateResult{
			Action:       "create_user",
			TargetDN:     "",
			WouldSucceed: false,
			Error:        fmt.Sprintf("recherche existence: %v", err),
		}, nil
	}

	changes := []string{
		fmt.Sprintf("Créer utilisateur: CN=%s,%s", params.DisplayName, params.OU),
		fmt.Sprintf("  sAMAccountName: %s", params.SAMAccountName),
		fmt.Sprintf("  userPrincipalName: %s", params.UserPrincipalName),
		fmt.Sprintf("  displayName: %s", params.DisplayName),
		fmt.Sprintf("  givenName: %s", params.GivenName),
		fmt.Sprintf("  sn: %s", params.SN),
		fmt.Sprintf("  mail: %s", params.Email),
		fmt.Sprintf("  userAccountControl: %d (enabled=%v)", userAccountControl(params.Enabled), params.Enabled),
	}
	if params.Description != "" {
		changes = append(changes, fmt.Sprintf("  description: %s", params.Description))
	}
	for _, g := range params.Groups {
		changes = append(changes, fmt.Sprintf("  Ajouter au groupe: %s", g))
	}
	changes = append(changes, "  Définir mot de passe initial")

	if len(res.Entries) > 0 {
		return &SimulateResult{
			Action:       "create_user",
			TargetDN:     res.Entries[0].DN,
			Changes:      changes,
			WouldSucceed: false,
			Error:        "un utilisateur avec ce sAMAccountName existe déjà dans cette OU",
		}, nil
	}

	return &SimulateResult{
		Action:       "create_user",
		TargetDN:     fmt.Sprintf("CN=%s,%s", params.DisplayName, params.OU),
		Changes:      changes,
		WouldSucceed: true,
	}, nil
}

// UserCreate crée réellement l'utilisateur.
func (p *Pool) UserCreate(ctx context.Context, params UserCreateParams) (*User, error) {
	if err := params.Validate(); err != nil {
		return nil, err
	}
	// Le RDN est échappé comme `ldap3.utils.dn.escape_rdn` côté Python : un nom
	// contenant « , » ou « + » ne doit pas couper le DN en deux.

	// Récupérer une connexion du pool qui sera réutilisée pour toutes les étapes
	conn, err := p.Get(ctx)
	if err != nil {
		return nil, err
	}
	defer p.Put(conn)

	if err := p.verifier("create_user", params.OU); err != nil {
		return nil, err
	}
	for _, groupDN := range params.Groups {
		if err := p.verifier("manage_groups", groupDN); err != nil {
			return nil, err
		}
	}

	sim, err := p.UserCreateSimulate(ctx, params, conn)
	if err != nil {
		return nil, err
	}
	if !sim.WouldSucceed {
		return nil, errors.New(sim.Error)
	}

	// DN final (déjà échappé)
	dn := fmt.Sprintf("CN=%s,%s", EchapperRDN(params.DisplayName), params.OU)

	// Attributs de l'utilisateur
	attrs := map[string][]string{
		"objectClass":        {"top", "person", "organizationalPerson", "user"},
		"cn":                 {params.DisplayName},
		"sAMAccountName":     {params.SAMAccountName},
		"userPrincipalName":  {params.UserPrincipalName},
		"displayName":        {params.DisplayName},
		"givenName":          {params.GivenName},
		"sn":                 {params.SN},
		"userAccountControl": {fmt.Sprint(userAccountControl(params.Enabled))},
	}
	if params.Email != "" {
		attrs["mail"] = []string{params.Email}
	}
	if params.Description != "" {
		attrs["description"] = []string{params.Description}
	}

	addReq := ldap.NewAddRequest(dn, nil)
	for k, v := range attrs {
		addReq.Attribute(k, v)
	}

	if err := conn.Add(addReq); err != nil {
		return nil, fmt.Errorf("ad: création utilisateur %s: %w", dn, err)
	}

	// Mot de passe initial (nécessite connexion SSL/TLS)
	pwdModReq := ldap.NewPasswordModifyRequest(dn, "", params.InitialPassword)
	if _, err := conn.PasswordModify(pwdModReq); err != nil {
		// Rollback: supprimer l'utilisateur créé
		_ = conn.Del(ldap.NewDelRequest(dn, nil))
		return nil, fmt.Errorf("ad: définition mot de passe %s: %w", dn, err)
	}

	// Ajouter aux groupes
	for _, groupDN := range params.Groups {
		modReq := ldap.NewModifyRequest(groupDN, nil)
		modReq.Add("member", []string{dn})
		if err := conn.Modify(modReq); err != nil {
			// Log mais ne pas échouer complètement
			fmt.Printf("ad: avertissement: ajout au groupe %s échoué: %v\n", groupDN, err)
		}
	}

	// Récupérer l'utilisateur créé (réutilise la même connexion)
	return p.UserGet(ctx, dn, conn)
}

// userAccountControl calcule la valeur de userAccountControl.
// 512 = NORMAL_ACCOUNT, 2 = ACCOUNTDISABLE.
func userAccountControl(enabled bool) int {
	if enabled {
		return 512
	}
	return 514
}

// UserGet récupère un utilisateur par son DN.
func (p *Pool) UserGet(ctx context.Context, dn string, conn *ldap.Conn) (*User, error) {
	if conn == nil {
		var err error
		conn, err = p.Get(ctx)
		if err != nil {
			return nil, err
		}
		defer p.Put(conn)
	}

	req := ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false,
		"(objectClass=user)",
		[]string{"dn", "sAMAccountName", "userPrincipalName", "displayName", "givenName", "sn",
			"description", "mail", "userAccountControl", "memberOf", "distinguishedName"},
		nil)

	res, err := conn.Search(req)
	if err != nil {
		return nil, fmt.Errorf("ad: lecture utilisateur %s: %w", dn, err)
	}
	if len(res.Entries) == 0 {
		return nil, fmt.Errorf("ad: utilisateur introuvable: %s", dn)
	}
	return entryToUser(res.Entries[0])
}

// entryToUser convertit une entrée LDAP en User.
func entryToUser(e *ldap.Entry) (*User, error) {
	uac := 512
	if v := e.GetAttributeValue("userAccountControl"); v != "" {
		fmt.Sscanf(v, "%d", &uac)
	}
	return &User{
		DN:                e.DN,
		SAMAccountName:    e.GetAttributeValue("sAMAccountName"),
		UserPrincipalName: e.GetAttributeValue("userPrincipalName"),
		CN:                e.GetAttributeValue("cn"),
		DisplayName:       e.GetAttributeValue("displayName"),
		GivenName:         e.GetAttributeValue("givenName"),
		SN:                e.GetAttributeValue("sn"),
		Description:       e.GetAttributeValue("description"),
		Email:             e.GetAttributeValue("mail"),
		Enabled:           (uac & 2) == 0,
		MemberOf:          e.GetAttributeValues("memberOf"),
		OU:                parentDN(e.DN),
	}, nil
}

// parentDN retourne le DN parent (OU=...,DC=...).
func parentDN(dn string) string {
	parts := strings.SplitN(dn, ",", 2)
	if len(parts) == 2 {
		return parts[1]
	}
	return ""
}

// UserEnable active ou désactive un utilisateur.
func (p *Pool) UserEnable(ctx context.Context, dn string, enable bool) error {
	if err := p.verifier("disable_account", dn); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	uac := userAccountControl(enable)
	modReq := ldap.NewModifyRequest(dn, nil)
	modReq.Replace("userAccountControl", []string{fmt.Sprint(uac)})
	if err := conn.Modify(modReq); err != nil {
		return fmt.Errorf("ad: %s utilisateur %s: %w", map[bool]string{true: "activation", false: "désactivation"}[enable], dn, err)
	}
	return nil
}

// AttributeUpdate décrit le remplacement des valeurs d'un attribut LDAP.
type AttributeUpdate struct {
	// Nom de l'attribut LDAP (ex. « mail », « proxyAddresses »).
	Nom string `json:"name"`
	// Valeurs à écrire (remplacement complet).
	Valeurs []string `json:"values"`
}

// UserSetAttributes remplace les valeurs des attributs fournis sur un objet AD
// (utilisateur ou groupe). Les attributs absents de la requête sont inchangés.
func (p *Pool) UserSetAttributes(ctx context.Context, dn string, majs []AttributeUpdate) error {
	if len(majs) == 0 {
		return nil
	}
	if err := p.verifier("modify_user", dn); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	modReq := ldap.NewModifyRequest(dn, nil)
	for _, maj := range majs {
		if maj.Nom == "" {
			continue
		}
		modReq.Replace(maj.Nom, maj.Valeurs)
	}
	if err := conn.Modify(modReq); err != nil {
		return fmt.Errorf("ad: mise à jour des attributs de %s: %w", dn, err)
	}
	return nil
}

// UserMove déplace un utilisateur vers une autre OU.
func (p *Pool) UserMove(ctx context.Context, dn, newOU string) error {
	if err := p.verifier("move_user", dn, newOU); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	// MODDN pour déplacer
	modDNReq := ldap.NewModifyDNRequest(dn, fmt.Sprintf("CN=%s", rdnFromDN(dn)), true, newOU)
	if err := conn.ModifyDN(modDNReq); err != nil {
		return fmt.Errorf("ad: déplacement utilisateur %s vers %s: %w", dn, newOU, err)
	}
	return nil
}

// rdnFromDN extrait le RDN (CN=...) d'un DN complet.
func rdnFromDN(dn string) string {
	if idx := strings.Index(dn, ","); idx > 0 {
		return dn[:idx]
	}
	return dn
}

// UserSetPassword change le mot de passe d'un utilisateur.
func (p *Pool) UserSetPassword(ctx context.Context, dn, oldPwd, newPwd string) error {
	if err := p.verifier("reset_password", dn); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	pwdReq := ldap.NewPasswordModifyRequest(dn, oldPwd, newPwd)
	if _, err := conn.PasswordModify(pwdReq); err != nil {
		return fmt.Errorf("ad: changement mot de passe %s: %w", dn, err)
	}
	return nil
}

// UserResetPassword réinitialise le mot de passe (admin, sans ancien mot de passe).
func (p *Pool) UserResetPassword(ctx context.Context, dn, newPwd string) error {
	if err := p.verifier("reset_password", dn); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	// En tant qu'admin, on peut utiliser PasswordModify sans ancien mot de passe
	// ou directement remplacer unicodePwd (nécessite SSL)
	pwdReq := ldap.NewPasswordModifyRequest(dn, "", newPwd)
	if _, err := conn.PasswordModify(pwdReq); err != nil {
		return fmt.Errorf("ad: réinitialisation mot de passe %s: %w", dn, err)
	}
	return nil
}

// UserAddToGroup ajoute un utilisateur à un groupe.
func (p *Pool) UserAddToGroup(ctx context.Context, userDN, groupDN string) error {
	if err := p.verifier("manage_groups", groupDN); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	modReq := ldap.NewModifyRequest(groupDN, nil)
	modReq.Add("member", []string{userDN})
	if err := conn.Modify(modReq); err != nil {
		return fmt.Errorf("ad: ajout utilisateur %s au groupe %s: %w", userDN, groupDN, err)
	}
	return nil
}

// UserRemoveFromGroup retire un utilisateur d'un groupe.
func (p *Pool) UserRemoveFromGroup(ctx context.Context, userDN, groupDN string) error {
	if err := p.verifier("manage_groups", groupDN); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	modReq := ldap.NewModifyRequest(groupDN, nil)
	modReq.Delete("member", []string{userDN})
	if err := conn.Modify(modReq); err != nil {
		return fmt.Errorf("ad: retrait utilisateur %s du groupe %s: %w", userDN, groupDN, err)
	}
	return nil
}

// UserDelete supprime un utilisateur (purge, pas archivage).
func (p *Pool) UserDelete(ctx context.Context, dn string) error {
	if err := p.verifier("delete_user", dn); err != nil {
		return err
	}
	conn, err := p.Get(ctx)
	if err != nil {
		return err
	}
	defer p.Put(conn)

	delReq := ldap.NewDelRequest(dn, nil)
	if err := conn.Del(delReq); err != nil {
		return fmt.Errorf("ad: suppression utilisateur %s: %w", dn, err)
	}
	return nil
}

// UserSearch recherche des utilisateurs dans une OU (pagination supportée).
func (p *Pool) UserSearch(ctx context.Context, ou, filter string, attrs []string, pageSize int) ([]*User, error) {
	conn, err := p.Get(ctx)
	if err != nil {
		return nil, err
	}
	defer p.Put(conn)

	if filter == "" {
		filter = "(&(objectClass=user)(objectCategory=person))"
	}
	if len(attrs) == 0 {
		attrs = []string{"dn", "sAMAccountName", "userPrincipalName", "displayName", "givenName", "sn", "mail", "userAccountControl"}
	}

	var allUsers []*User
	pageSize = max(pageSize, 100)
	var cookie []byte // curseur de pagination entre itérations

	for {
		req := ldap.NewSearchRequest(ou, ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 0, 0, false, filter, attrs, nil)
		if pageSize > 0 {
			// Le cookie reprend la page là où la précédente s'est arrêtée ;
			// sans lui, le serveur renverrait la première page en boucle.
			ctrl := ldap.NewControlPaging(uint32(pageSize))
			ctrl.Cookie = cookie
			req.Controls = []ldap.Control{ctrl}
		}

		res, err := conn.Search(req)
		if err != nil {
			return nil, fmt.Errorf("ad: recherche utilisateurs dans %s: %w", ou, err)
		}

		for _, entry := range res.Entries {
			u, err := entryToUser(entry)
			if err != nil {
				continue // skip entrées invalides
			}
			allUsers = append(allUsers, u)
		}

		// Pagination
		cookie = nil
		for _, ctrl := range res.Controls {
			if paging, ok := ctrl.(*ldap.ControlPaging); ok {
				cookie = paging.Cookie
				break
			}
		}
		if len(cookie) == 0 {
			break
		}
	}

	return allUsers, nil
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
